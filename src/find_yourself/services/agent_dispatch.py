"""子 Agent 派发验证：Task 统一协议 + 独立验收器（工单 P1-20）。

实现方向（claw-dialogue-extraction §1.1 最小版）：

* **Task 四件套** —— ``capability / payload / acceptance / env_contract``。
  ``acceptance`` 进路由：子 Agent 干完活不算完，必须由独立验收器按
  acceptance 真实复核；``env_contract`` 按 §2 采纳意见做成轻量"环境标注"
  （随 trace 留档，不做独立路由层）。
* **子 Agent = 确定性工作器** —— 按 payload 声明的工具清单真实调用
  P1-05 工具注册中心（``tool_registry.invoke``），产出 result 并自报
  status（self_report）。
* **独立验收器（Verifier）** —— 不信任子 Agent 自报：
  - ``output_contains``：从工具回执的 result 真实重推产出文本，逐项检查
    acceptance.contains 是否全部命中；
  - ``tool_invoked``：重新查询工具调用日志（recent_calls），核实声明的
    工具在派发时间之后确实以 executed=True 被调用过。
* **状态机** —— parent/child 均含"验收中（verifying）/已拒绝（rejected）"
  两态：``dispatched → verifying → verified | rejected``。
* **经验回写（预留）** —— 验收拒绝时把失败原因写入 child trace 的
  ``experience`` 字段（L0 工作记忆形态，接口已预留，后续接入 PMI 层）。

Trace 只落内存 + 归档目录（env ``FY_DISPATCH_ARCHIVE_DIR``），不写数据库迁移。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scheduler import (
    CHANNEL_INTERNAL_AGENT,
    TASK_FAILED,
    TASK_RECLAIMED,
    DispatchRequest,
    UnifiedScheduler,
)
from .scheduler import scheduler as _default_scheduler
from .tool_registry import ToolRegistryService, tool_registry

ACCEPTANCE_TYPES = ("output_contains", "tool_invoked")
CAPABILITY_RE_MAX = 128

#: 子 Agent 执行在调度中心里的 worker 标识（内部 agent 与外部成品 agent 同池）。
DISPATCH_WORKER_ID = "internal.dispatch-child"


class DispatchValidationError(ValueError):
    """Task 协议不合法（capability / acceptance / tools 结构错误）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class AgentDispatchService:
    """父子两级派发 + 独立验收 + trace 归档（内存 + JSON 文件）。"""

    def __init__(
        self,
        archive_dir: str | os.PathLike | None = None,
        registry: ToolRegistryService | None = None,
        scheduler: UnifiedScheduler | None = None,
        claw_pipeline: Any | None = None,
    ):
        self._dir = Path(archive_dir) if archive_dir else None
        self._registry = registry or tool_registry
        # 统一调度中心（A-统一接入-08 接线）：子 Agent 执行统一经调度中心派发，
        # 不再由本服务私起线程。默认用进程级共享单例（与 delegation / A2A 同池）。
        self._scheduler = scheduler or _default_scheduler
        # Claw 三层把关（A-Claw架构-01/02/03/04）：可选注入；未注入时派发流程
        # 行为不变（诚实可选，治理闸门由装配方决定是否启用）。
        self._claw_pipeline = claw_pipeline
        self._lock = threading.Lock()
        self._parents: dict[str, dict[str, Any]] = {}

    def _ensure_worker(self) -> None:
        """把确定性子 Agent 执行器登记进调度中心（幂等）。

        executor 只从 ``req.payload["run"]`` 取**提交时绑定的闭包**（绑定到提交
        方 service 实例与该次 child/plan），不在注册时捕获任何实例状态——
        多个 AgentDispatchService 实例（测试注入/多租户会话）共享同一 worker
        时互不串扰。
        """
        if self._scheduler.get_worker(DISPATCH_WORKER_ID) is None:
            self._scheduler.register_simple_worker(
                DISPATCH_WORKER_ID,
                CHANNEL_INTERNAL_AGENT,
                lambda req: req.payload["run"](),
                max_parallel=4,
                tags=("dispatch", "deterministic-worker"),
                description="P1-20 确定性子 Agent：按工具清单真实调用 tool_registry",
            )

    # -- Task 协议校验 ----------------------------------------------------------

    def validate_task(self, task: Any) -> dict[str, Any]:
        if not isinstance(task, dict):
            raise DispatchValidationError("task 必须是 JSON 对象")
        capability = task.get("capability")
        if not isinstance(capability, str) or not capability.strip() \
                or len(capability) > CAPABILITY_RE_MAX:
            raise DispatchValidationError(
                f"capability 必须是 1-{CAPABILITY_RE_MAX} 字的非空字符串")
        payload = task.get("payload", {})
        if not isinstance(payload, dict):
            raise DispatchValidationError("payload 必须是对象")
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, dict):
            raise DispatchValidationError("acceptance 必须是对象")
        atype = acceptance.get("type")
        if atype not in ACCEPTANCE_TYPES:
            raise DispatchValidationError(
                f"acceptance.type 必须是 {list(ACCEPTANCE_TYPES)} 之一")
        if atype == "output_contains":
            contains = acceptance.get("contains")
            if not isinstance(contains, list) or not contains \
                    or not all(isinstance(s, str) for s in contains):
                raise DispatchValidationError(
                    "acceptance.type=output_contains 需要非空 contains 字符串数组")
        else:  # tool_invoked
            tools = acceptance.get("tools")
            if not isinstance(tools, list) or not tools \
                    or not all(isinstance(s, str) for s in tools):
                raise DispatchValidationError(
                    "acceptance.type=tool_invoked 需要非空 tools 字符串数组")
        env_contract = task.get("env_contract", {})
        if not isinstance(env_contract, dict):
            raise DispatchValidationError("env_contract 必须是对象（轻量环境标注）")
        return {
            "capability": capability.strip(),
            "payload": payload,
            "acceptance": acceptance,
            "env_contract": env_contract,
        }

    def validate_tools(self, tools: Any) -> list[dict[str, Any]]:
        if not isinstance(tools, list) or not tools:
            raise DispatchValidationError("tools 必须是非空数组（子 Agent 的工作清单）")
        cleaned: list[dict[str, Any]] = []
        for i, item in enumerate(tools):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise DispatchValidationError(f"tools[{i}] 需要 name 字段")
            name = item["name"]
            arguments = item.get("arguments", {})
            if not isinstance(arguments, dict):
                raise DispatchValidationError(f"tools[{i}].arguments 必须是对象")
            try:
                self._registry.get_tool(name)
            except Exception as exc:  # noqa: BLE001 — 未注册工具在派发期即失败
                raise DispatchValidationError(
                    f"tools[{i}]: 工具 '{name}' 未在 P1-05 注册中心登记") from exc
            cleaned.append({"name": name, "arguments": arguments})
        return cleaned

    # -- 派发与子 Agent 执行 ------------------------------------------------------

    def dispatch(self, task: Any, tools: Any) -> dict[str, Any]:
        spec = self.validate_task(task)
        plan = self.validate_tools(tools)

        parent_id = _short_id("ptask")
        child_id = _short_id("ctask")
        dispatched_at = _now()

        child: dict[str, Any] = {
            "child_task_id": child_id,
            "parent_task_id": parent_id,
            "capability": spec["capability"],
            "status": "dispatched",
            "dispatched_at": dispatched_at,
            "finished_at": None,
            "tool_calls": [],
            "result_text": None,
            "self_report": None,
            "verification": None,
            "experience": None,
        }
        parent: dict[str, Any] = {
            "parent_task_id": parent_id,
            "task": spec,
            "status": "dispatched",
            "dispatched_at": dispatched_at,
            "verified_at": None,
            "children": [child],
        }
        with self._lock:
            self._parents[parent_id] = parent

        # 统一调度中心接线（A-统一接入-08）：子 Agent 执行经调度中心派发，
        # 与外部成品 agent 同池（统一路由/优先级/并发上限/回收/状态回传）。
        # requested_capability="dispatch" 把路由钉在本通路 worker 上（同通道
        # 还有 delegation 等其它内部 worker，靠 tags 精确匹配互不串扰）。
        self._ensure_worker()
        record = self._scheduler.submit_and_wait(
            DispatchRequest(
                channel=CHANNEL_INTERNAL_AGENT,
                action="run_child",
                requested_capability="dispatch",
                payload={
                    # 绑定到本次派发的执行闭包（worker 不捕获注册时的实例）。
                    "run": lambda: self._run_child(child, plan),
                    "child": child,
                    "plan": plan,
                },
                timeout_seconds=600.0,
                meta={"parent_task_id": parent_id, "capability": spec["capability"]},
            ),
            timeout=600.0,
        )
        parent["scheduler_task_id"] = record.task_id
        if record.status == TASK_RECLAIMED:
            # 调度中心回收（超时）后迟到结果一律丢弃：子 Agent 诚实标记失败，
            # 交由独立验收器复核拒绝，绝不冒充成功。
            child["status"] = TASK_FAILED
            child["self_report"] = {
                "status": "failed",
                "summary": "子 Agent 执行被调度中心回收（超时），结果按迟到丢弃",
            }
        # 验收中态：验收器接手，与子 Agent 自报无关
        parent["status"] = "verifying"
        child["status"] = "verifying"
        verdict = self._verify(child, spec["acceptance"], dispatched_at)
        child["status"] = "verified" if verdict["verdict"] == "verified" else "rejected"
        parent["status"] = child["status"]
        parent["verified_at"] = _now()
        # Claw 三层把关（A-Claw架构-01/02/03/04）：验收复核之后、归档之前，
        # 产出过治理闸门（自审→交叉验证→独立质检）。拦截 = 拒绝 + 原因回写。
        if self._claw_pipeline is not None:
            claw_out = self._claw_pipeline.run(
                task_id=str(parent_id),
                agent_role=str(child.get("role") or spec.get("capability") or "dispatch-child"),
                primary_output={"text": child.get("result_text") or ""},
                attempts=int(parent.get("attempts", 1) or 1),
            )
            parent["claw_gate"] = {
                "verdict": claw_out.verdict.value,
                "blocked": claw_out.blocked,
                "layers": [o.layer.value for o in claw_out.outcomes],
                "findings": [
                    {"rule": f.rule, "message": f.message[:200]}
                    for o in claw_out.outcomes for f in o.findings
                ][:10],
            }
            if claw_out.blocked:
                verdict["verdict"] = "rejected"
                verdict["reasons"] = list(verdict.get("reasons") or []) + [
                    f"Claw 把关拦截（{claw_out.verdict.value}）: "
                    + "；".join(f.message for o in claw_out.outcomes for f in o.findings
                                if f.severity == "block")[:300]
                ]
                child["status"] = "rejected"
                parent["status"] = "rejected"
        if verdict["verdict"] == "rejected":
            # 经验回写（预留结构）：失败原因回写 L0 工作记忆
            child["experience"] = {
                "written_back": True,
                "written_at": _now(),
                "scope": "L0",
                "lesson": "；".join(verdict["reasons"]),
            }
        self._archive(parent)
        return parent

    def _run_child(self, child: dict[str, Any], plan: list[dict[str, Any]]) -> None:
        """确定性子 Agent：真实调用工具，产出 result + 自报 status。"""
        texts: list[str] = []
        all_ok = True
        for call in plan:
            entry: dict[str, Any] = {
                "tool": call["name"],
                "arguments": call["arguments"],
                "ok": False,
                "call_id": None,
                "result": None,
                "error": None,
            }
            try:
                receipt = self._registry.invoke(call["name"], call["arguments"])
                entry["ok"] = bool(receipt.get("executed"))
                entry["call_id"] = receipt.get("call_id")
                entry["result"] = receipt.get("result")
                texts.append(self._textify(receipt.get("result")))
            except Exception as exc:  # noqa: BLE001 — 工具失败计入自报
                entry["error"] = str(exc)
                all_ok = False
            child["tool_calls"].append(entry)
        child["result_text"] = "\n".join(t for t in texts if t)
        child["self_report"] = {
            "status": "success" if all_ok else "failed",
            "summary": (
                f"执行 {len(plan)} 项工具调用，全部成功"
                if all_ok else f"执行 {len(plan)} 项工具调用，存在失败项"
            ),
        }
        child["status"] = "completed"
        child["finished_at"] = _now()

    @staticmethod
    def _textify(result: Any) -> str:
        """把工具回执的 result 确定性地序列化为可验收文本。"""
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    # -- 独立验收器 ---------------------------------------------------------------

    def _verify(
        self,
        child: dict[str, Any],
        acceptance: dict[str, Any],
        dispatched_at: str,
    ) -> dict[str, Any]:
        """独立复核：只看真实产出与调用日志，不看 self_report。"""
        checks: list[dict[str, Any]] = []
        reasons: list[str] = []

        if acceptance["type"] == "output_contains":
            for needle in acceptance["contains"]:
                hit = needle in (child["result_text"] or "")
                checks.append({"check": "output_contains", "target": needle, "passed": hit})
                if not hit:
                    reasons.append(f"产出文本未包含验收要求片段: {needle!r}")
        else:  # tool_invoked
            for tool_name in acceptance["tools"]:
                invoked = self._invoked_in_log(tool_name, dispatched_at)
                checks.append({"check": "tool_invoked", "target": tool_name, "passed": invoked})
                if not invoked:
                    reasons.append(f"调用日志中未发现工具真实调用: {tool_name!r}")

        # 交叉核对：自报失败但验收器判通过 → 以验收器为准仍需标记不一致
        self_ok = (child.get("self_report") or {}).get("status") == "success"
        passed = not reasons
        verdict = {
            "verdict": "verified" if passed else "rejected",
            "checks": checks,
            "reasons": reasons,
            "self_report_status": child.get("self_report", {}).get("status"),
            "consistent": passed == self_ok,
            "verified_at": _now(),
        }
        child["verification"] = verdict
        return verdict

    def _invoked_in_log(self, tool_name: str, dispatched_at: str) -> bool:
        """独立证据：重查 P1-05 调用日志，而非采信子 Agent 回传的回执。"""
        try:
            calls = self._registry.recent_calls(limit=500)
        except Exception:  # noqa: BLE001
            return False
        for receipt in calls:
            if receipt.get("tool") != tool_name or not receipt.get("executed"):
                continue
            if str(receipt.get("executed_at", "")) >= dispatched_at:
                return True
        return False

    # -- trace 读取与归档 ------------------------------------------------------------

    def get(self, parent_task_id: str) -> dict[str, Any] | None:
        with self._lock:
            parent = self._parents.get(parent_task_id)
            return json.loads(json.dumps(parent, ensure_ascii=False)) if parent else None

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._parents)

    def _archive(self, parent: dict[str, Any]) -> None:
        if not self._dir:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"dispatch-{parent['parent_task_id']}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(parent, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # 归档失败不影响主流程（trace 仍在内存）


# 进程内单例；归档目录可用 env 打开（evidence 归档用）。

def _ensure_builtin_tools() -> None:
    """确保 echo/add 工具已注册到 tool_registry。

    前端下拉框里的 echo/add 是硬编码的，但后端 tool_registry 里没有注册，
    导致派发子 Agent 时 100% 报 422。这里在模块加载时自动注册。
    """
    for name, desc, params in [
        ("echo", "回显输入参数，用于测试连通性", {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        }),
        ("add", "两数相加", {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个数"},
                "b": {"type": "number", "description": "第二个数"},
            },
            "required": ["a", "b"],
        }),
    ]:
        try:
            tool_registry.register(
                name=name,
                description=desc,
                parameters=params,
                entry={"type": "builtin", "executor": name},
            )
        except Exception:
            pass  # 已注册则跳过


_ensure_builtin_tools()

agent_dispatch = AgentDispatchService(
    archive_dir=os.environ.get("FY_DISPATCH_ARCHIVE_DIR") or None
)

def _ensure_builtin_tools() -> None:
    """确保 echo/add 工具已注册到 tool_registry。"""
    for name, desc, params in [
        ("echo", "回显输入参数，用于测试连通性", {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        }),
        ("add", "两数相加", {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个数"},
                "b": {"type": "number", "description": "第二个数"},
            },
            "required": ["a", "b"],
        }),
    ]:
        try:
            tool_registry.register(
                name=name,
                description=desc,
                parameters=params,
                entry={"type": "builtin", "executor": name},
            )
        except Exception:
            pass


_ensure_builtin_tools()

