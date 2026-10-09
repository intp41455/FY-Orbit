"""多 Agent 协同编排：拆分 → 并行派单 → 汇总。

为什么需要这个模块
------------------
用户要的核心是「把电脑上多个 Agent 连起来共同执行一个任务，中枢实时调度」。
hub 解决了「单个 Agent 怎么接、怎么调」，但**没有「多个 Agent 怎么协同」**：
调用方必须自己串好 N 次 HTTP 调用、自己判断该派给谁、自己拼多轮上下文。

本模块把这三件事收进一个可复用入口，且严格复用既有能力、不重复造轮子：
* 拆分与派单用 ``CapabilityRouter.route(hint)``（确定性打分，非 LLM 猜）
* 实际调用用注入的 ``HubService.invoke``（凭证解密、信任分级、SSRF 防护都在里面）
* 多轮上下文用刚修好的 ``params.messages``

三种编排形态
------------
``sequential``  顺序执行，前一步输出拼进后一步上下文（写作/审校类）
``parallel``    并行执行同一任务的不同侧面，互不知情（多角度调研类）
``pipeline``    顺序执行且显式指定每步用哪个连接（需要精确控制时）

诚实原则
--------
* 某个 Agent 失败**不吞掉**，记进 ``failed`` 并如实返回
* 匹配不上能力就**不派单**，绝不随便挑一个凑数
* 汇总器也是 Agent 才调用；不是则如实返回各步原始产出

一个实测得出的约束
------------------
并行度默认压到 3：两个云端 key 都是免费档，实测连续调用会撞 429
``rate_limited``。真正的并发上限由上游配额决定，不是线程数。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import ValidationFailed

#: 并行派单默认并发上限。上游免费额度才是真瓶颈（实测 429），不宜再高。
DEFAULT_MAX_WORKERS = 3


@dataclass(frozen=True)
class StepResult:
    """一步的执行结果。``ok=False`` 时 ``error`` 一定有值，不留空白。"""

    index: int
    step: str
    connection_id: str = ""
    connection_name: str = ""
    capability: str = ""
    ok: bool = False
    output: Any = None
    error: str = ""
    latency_ms: int = 0

    def to_public(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step": self.step,
            "connection_id": self.connection_id,
            "connection_name": self.connection_name,
            "capability": self.capability,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


@dataclass
class OrchestrationResult:
    """一次编排的完整结果，含每步明细与最终产出。"""

    ok: bool
    mode: str
    steps: list[StepResult] = field(default_factory=list)
    final_output: Any = None
    error: str = ""
    duration_ms: int = 0

    @property
    def succeeded(self) -> list[StepResult]:
        return [s for s in self.steps if s.ok]

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if not s.ok]

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "steps": [s.to_public() for s in self.steps],
            "step_count": len(self.steps),
            "succeeded_count": len(self.succeeded),
            "failed_count": len(self.failed),
            "final_output": self.final_output,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def _bind_invoke(hub: Any, actor: Any) -> Callable[..., dict[str, Any]]:
    """把 ``HubService.invoke`` 绑成编排器期望的 ``(conn_id, *, action, ...)`` 形状。

    这是签名错位的**唯一修复点**。真实签名是
    ``invoke(actor, conn_id, *, action, params, timeout_seconds, ...)``，
    编排器按 ``invoke(conn_id, *, action, params, timeout_seconds)`` 调用；
    直接传 ``hub.invoke`` 会把 ``conn_id`` 错位到 ``actor`` 上。

    这里用闭包把 actor 预先绑好，并**只透传编排器真正用到的关键字参数**
    （多余的 transport/sleep 不给默认值，保持与 :meth:`Orchestrator._run_step`
    的调用面一致）。
    """
    def _invoke(conn_id: str, *, action: str, params: dict[str, Any] | None = None,
                timeout_seconds: float = 15.0) -> dict[str, Any]:
        return hub.invoke(actor, conn_id, action=action, params=params,
                          timeout_seconds=timeout_seconds)
    return _invoke


def _capability_name(cap: Any) -> str:
    """从路由候选的 ``capability`` 字段里取出能力名——**必须兼容 dict 与对象**。

    为什么需要这个函数（一个真实踩过、被单测全绿掩盖的 P0）：
    ``CapabilityRouter.route`` 返回的是 ``capability: capability.to_public()``
    —— **dict**（``{"name":..., "tags":..., "aliases":...}``），不是
    :class:`Capability` 实例。而这里原先写的是
    ``getattr(cap, "name", "")``，dict 没有 ``.name`` 属性，``getattr`` 恒返回
    空串 ``""``，紧接着的 ``if not cap_name: continue`` 就把**全部候选丢弃**，
    最终必然走到 ``raise ValueError("没有 Agent 能处理")``——编排器永远派不出单，
    ``run_sequential`` / ``run_parallel`` 100% 失败。

    这里**不去改** ``route()`` 的返回形状（那是公开 API，牵动路由试算 UI），
    只在消费端做形状兼容：dict 取 ``["name"]``，对象取 ``.name``，字符串直接用。

    顺带说明为什么单测没抓到：``tests/unit/hub/test_orchestrator.py::_cand``
    构造的是 ``{"capability": type("C", (), {"name": cap})()}``——**对象形态**，
    与真实 ``route()`` 的 dict 形态不一致。mock 自己定义了协议，于是双方永远
    「一致」。本文件末尾的契约守卫专门为这个洞而设。
    """
    if isinstance(cap, dict):
        return str(cap.get("name") or "")
    if isinstance(cap, str):
        return cap
    return str(getattr(cap, "name", "") or "")


def _bind_prepare(hub: Any, actor: Any) -> Callable[[str], Callable[..., dict[str, Any]]]:
    """把 ``HubService.prepare_invoke`` 绑成 ``(conn_id) -> 可跨线程调用的 invoke``。

    与 :func:`_bind_invoke` 同理：真实签名 ``prepare_invoke(actor, conn_id, ...)``
    的 actor 在第一位，这里闭包把它预先绑掉。

    返回的可调用对象**已经完成了全部 DB 读取**（连接行、凭证解密、适配器构造），
    因此可以在任何线程里安全执行——见 :func:`_as_invoke_shim`。
    """
    def _prepare(conn_id: str) -> Callable[..., dict[str, Any]]:
        return hub.prepare_invoke(actor, conn_id)
    return _prepare


def _as_invoke_shim(prepared: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """把「已就绪的单连接调用器」包装成 :meth:`Orchestrator._run_step` 的调用面。

    ``prepare_invoke`` 返回的是 ``call(action, *, params, timeout_seconds)``
    （连接已定，不需要 conn_id）；``_run_step`` 按
    ``invoke(conn_id, *, action, params, timeout_seconds)`` 调用。这里补上被
    忽略的 conn_id 位，让 :meth:`Orchestrator._dispatch_step` 能直接把它当
    invoke 传下去。
    """
    def _invoke(_conn_id: str, *, action: str, params: dict[str, Any] | None = None,
                timeout_seconds: float = 15.0) -> dict[str, Any]:
        return prepared(action=action, params=params or {}, timeout_seconds=timeout_seconds)
    return _invoke


class Orchestrator:
    """把一个任务派给多个 Agent 执行并汇总。

    ``invoke`` 是 ``HubService.invoke`` 的同签名可调用对象（由调用方注入）——
    本模块不自己建连接、不自己解凭证，保持与既有鉴权链路单一事实源。
    ``route`` 是 ``CapabilityRouter.route`` 的同签名可调用对象。
    """

    def __init__(
        self,
        *,
        invoke: Callable[..., dict[str, Any]],
        route: Callable[..., list[dict[str, Any]]],
        max_workers: int = DEFAULT_MAX_WORKERS,
        prepare: Callable[[str], Callable[..., dict[str, Any]]] | None = None,
    ) -> None:
        """``prepare`` 是并行派单的**线程安全开关**，见 :meth:`_prepare_invoke`。

        缺省为 ``None``——直接构造的编排器（单测里的 mock）不涉及 DB，线程里
        没有 Session 可争，不需要它。只有 :meth:`bind` 接了真实 HubService 才建。
        """
        self._invoke = invoke
        self._route = route
        self._max_workers = max(1, min(int(max_workers), 8))
        self._prepare = prepare

    # ------------------------------------------------------------------ #
    # 接线
    # ------------------------------------------------------------------ #
    @staticmethod
    def bind(
        hub: Any,
        *,
        actor: Any = None,
        session: Any = None,
        owner_id: str | None = None,
        router: Any = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> "Orchestrator":
        """用真实的 :class:`HubService` 造一个编排器 —— **生产接线唯一正确入口**。

        为什么必须有这个工厂：``HubService.invoke`` 的真实签名是
        ``invoke(self, actor, conn_id, *, action, ...)`` —— **actor 是第一个位置参数**。
        而 :class:`Orchestrator` 内部按 ``invoke(conn_id, *, action, ...)`` 调用
        （见 :meth:`_run_step`）。直接把 ``hub.invoke`` 注入 Orchestrator 会让
        ``conn_id`` 落到 ``actor`` 形参上，真实调用必抛
        ``TypeError: invoke() missing 1 required positional argument: 'conn_id'``。

        历史真相：单测里的 ``_FakeInvoke.__call__(self, conn_id, *, action, ...)``
        是**按调用方期望伪造**的签名，所以 43 条编排测试全绿却掩盖了错位 ——
        这是「假绿测试」的典型。真实接线必须经本工厂把 actor 绑进去。

        参数：
            hub:      真实的 ``HubService``。
            actor:    发起编排的 :class:`Actor`（owner 身份）。
            session:  DB session —— 仅在未显式传 ``router`` 且需要自建
                      :class:`CapabilityRouter` 时使用。
            owner_id: 同上；``CapabilityRouter(session, owner_id)`` 需要它。
            router:   可选的路由试算器；缺省时按 ``session`` + ``owner_id`` 自建。

        用法::

            orch = Orchestrator.bind(hub, actor=owner_actor,
                                     session=session, owner_id=owner_id)
            report = orch.run_pipeline(steps, user_intent="...")
        """
        if router is None:
            if session is None:
                raise ValidationFailed(
                    "hub_orchestrator_router_required",
                    "构造编排器需要 router，或同时提供 session 与 owner_id 以自建路由",
                )
            from .router import CapabilityRouter  # 局部导入：避免 hub 包初始化时的循环依赖

            router = CapabilityRouter(session, owner_id or getattr(actor, "owner_id", None))
        return Orchestrator(
            invoke=_bind_invoke(hub, actor),
            route=router.route,
            max_workers=max_workers,
            prepare=_bind_prepare(hub, actor) if hasattr(hub, "prepare_invoke") else None,
        )

    # ------------------------------------------------------------------ #
    # 线程安全：并行派单不能在工作线程里碰 Session
    # ------------------------------------------------------------------ #

    def _prepare_invoke(self, conn_id: str) -> Callable[..., dict[str, Any]] | None:
        """在**主线程**把这一步的 DB 读取做完，返回可跨线程调用的执行器。

        SQLAlchemy 的 :class:`~sqlalchemy.orm.Session` **不是线程安全的**——它是
        单个「工作单元」，内部持有 DBAPI 连接与身份映射。``run_parallel`` 把派单
        丢进 ThreadPoolExecutor，而 ``HubService.invoke`` 内部要用 ``self.s`` 查
        ``hub_connections``；共用主线程那个 Session 就是跨线程共用一个连接，实测
        （SQLite，单一共享连接）直接硬崩：

            ProgrammingError: (sqlite3.ProgrammingError) SQLite objects created
            in a thread can only be used in that same thread. The object was
            created in thread id 74584 and this is thread id 84768.

        实测还见过同一根因的第二副面孔：换成独立连接后，新连接看不到主线程**未
        提交**的写入，于是 ``NotFound: 连接不存在``。可见「给每个线程发一条新
        Session」也不够——只要线程里还在读 DB，就仍在赌连接与事务的可见性。

        所以这里采用**更彻底**的分工：DB 读取（取连接行、校验归属与状态、解密
        凭证、构造适配器）全部留在**主线程**（本方法里）做完，工作线程只拿到一个
        已经就绪、不碰 DB 的调用器，纯粹做网络/本地调用。好处有三：

        * 线程里零 DB 访问 —— 与连接池策略、``check_same_thread`` 设置都无关；
        * 仍看得见主线程未提交的写入（读取发生在同一个工作单元里）；
        * 真正的网络调用仍在线程池里并发，没有退化成串行。

        校验与凭证解密仍然走 ``HubService.prepare_invoke``（真实链路），**不在
        编排器里另写一份**——另写一份就会与真实链路分叉，那正是 P0-1 那类事故的
        温床。 ``invoke`` 本身也已改为委托给它，两处共用同一份逻辑。

        返回 ``None`` 表示没有 prepare 通道（直接构造的编排器 / 假 hub），
        调用方退回 ``self._invoke``——至少与修复前一致，且 :meth:`_run_step`
        仍会如实记录失败，不吞异常。
        """
        if self._prepare is None:
            return None
        # 异常不在这里吞：取不到 / 被停用 / 缺凭证都是「派不出单」，由调用方
        # 如实记进 planning_errors，而不是悄悄退化成别的行为。
        return self._prepare(conn_id)

    # ------------------------------------------------------------------ #
    # 派单
    # ------------------------------------------------------------------ #

    def _pick(self, hint: str, *, exclude: set[str] | None = None,
              kind: str | None = None) -> tuple[str, str, str]:
        """挑一个 Agent，返回 ``(connection_id, capability, connection_name)``。

        匹配不上就抛错——**绝不随便挑一个凑数**（诚实原则）。

        ``exclude`` 用于并行派单时避开已用过的连接：两个云端连接的 chat
        能力声明几乎相同、preference 都是 0，打分必然打平（实测三个候选
        同为 4.5），不显式排除就会三次全落同一个连接，「多 Agent 协同」
        退化成单 Agent 重复调用。
        """
        skip = exclude or set()
        picked: list[tuple[str, str, str]] = []
        for cand in self._route(hint, top_k=10, kind=kind) or []:
            conn_id = str(cand.get("connection_id") or "")
            if not conn_id:
                continue
            cap = cand.get("capability")
            cap_name = _capability_name(cap)
            if not cap_name:
                continue
            if conn_id in skip:
                picked.append((conn_id, str(cap_name),
                               str(cand.get("connection_name") or "")))
                continue
            return conn_id, str(cap_name), str(cand.get("connection_name") or "")
        if picked:
            # 全部候选都被排除了 —— 如实说明是「都用过了」，而不是「没匹配上」
            raise ValueError(f"候选 Agent 都已被本轮其它步骤占用：{hint!r}")
        raise ValueError(f"没有 Agent 能处理：{hint!r}（不硬凑）")

    # ------------------------------------------------------------------ #
    # 单步
    # ------------------------------------------------------------------ #

    def _run_step(
        self,
        index: int,
        step: str,
        hint: str,
        *,
        connection_id: str = "",
        connection_name: str = "",
        capability: str = "",
        tool: str = "",
        kind: str | None = None,
        context: list[dict[str, str]] | None = None,
        timeout_seconds: float = 60.0,
        params_extra: dict[str, Any] | None = None,
        invoke: Callable[..., dict[str, Any]] | None = None,
    ) -> StepResult:
        """执行一步。失败**如实记录**而不抛出——编排需要看到部分成功。

        ``invoke`` 是并行派单时注入的**已就绪、不碰 DB**的调用器（见
        :meth:`_prepare_invoke`）；缺省用 ``self._invoke``。它只换「怎么调到
        Agent」，调用面与鉴权链路完全一致。

        ``connection_name`` 由调用方在显式指定连接时一并给出：路由选出的名字
        否则会丢，结果里就看不出「这一问到底派给了谁」。

        ``tool`` 是给「必须指名工具」的适配器（``mcp_server``）用的：它的
        ``call_tool`` 读``params["name"]``，收到自然语言 hint 会诚实报
        ``hub_missing_tool``。所以显式指定这类连接时必须给出工具名。
        """
        name = connection_name
        if connection_id:
            # 显式指定：能力名可省（交给适配器默认动作）。但不能无脑兜底成
            # "invoke" —— 对需要工具名的适配器那样必失败，且失败信息误导。
            cap_name = capability or "invoke"
        else:
            try:
                connection_id, cap_name, name = self._pick(hint, kind=kind)
            except ValueError as exc:
                return StepResult(index=index, step=step, ok=False, error=str(exc))

        params: dict[str, Any] = {"prompt": hint}
        if tool:
            # mcp_server 的 call_tool 只认 params["name"]；实测 `tool` 键无效。
            params["name"] = tool
        if context:
            # 多轮：上文 + 本轮指令。刚修好的 messages 支持就落在这里。
            params = {"messages": [*context, {"role": "user", "content": hint}]}
            if tool:
                params["name"] = tool
        if params_extra:
            params.update(params_extra)

        started = time.perf_counter()
        try:
            call = invoke or self._invoke
            raw = call(connection_id, action=cap_name, params=params,
                       timeout_seconds=timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — 编排不该被单步异常打断
            return StepResult(index=index, step=step, connection_id=connection_id,
                              capability=cap_name, ok=False,
                              error=f"{type(exc).__name__}: {exc}",
                              latency_ms=int((time.perf_counter() - started) * 1000))

        result = (raw or {}).get("result") or {}
        elapsed = int((time.perf_counter() - started) * 1000)
        if not result.get("ok"):
            return StepResult(index=index, step=step, connection_id=connection_id,
                              connection_name=name, capability=cap_name, ok=False,
                              error=str(result.get("error") or "未知错误"),
                              latency_ms=elapsed)

        return StepResult(index=index, step=step, connection_id=connection_id,
                          connection_name=name, capability=cap_name, ok=True,
                          output=result.get("output"), latency_ms=elapsed)

    # ------------------------------------------------------------------ #
    # 形态
    # ------------------------------------------------------------------ #

    def run_sequential(
        self,
        task: str,
        steps: list[str],
        *,
        kind: str | None = "openai_chat",
        timeout_seconds: float = 60.0,
    ) -> OrchestrationResult:
        """顺序执行：上一步的输出作为下一步的上下文。

        用于「起草 → 审校 → 定稿」这类每步都依赖前一步产出的工作。
        """
        started = time.perf_counter()
        context: list[dict[str, str]] = []
        results: list[StepResult] = []

        for i, step in enumerate(steps):
            res = self._run_step(i, step, f"{task}\n\n{step}", kind=kind,
                                 context=context or None,
                                 timeout_seconds=timeout_seconds)
            results.append(res)
            if not res.ok:
                # 一步失败就停：后续步骤以上文为前提，硬跑只会产出垃圾
                return OrchestrationResult(
                    ok=False, mode="sequential", steps=results, final_output=None,
                    error=f"第 {i + 1} 步「{step}」失败：{res.error}",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            text = _as_text(res.output)
            if text:
                context.append({"role": "assistant", "content": text})

        return OrchestrationResult(
            ok=True, mode="sequential", steps=results,
            final_output=_as_text(results[-1].output) if results else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _dispatch_step(
        self,
        index: int,
        aspect: str,
        hint: str,
        connection_id: str,
        connection_name: str,
        capability: str,
        kind: str | None,
        timeout_seconds: float,
        invoke: Callable[..., dict[str, Any]] | None = None,
    ) -> StepResult:
        """ThreadPoolExecutor 的任务体：只做「调一个已经就绪的 Agent」。

        路由（``_pick``）与 DB 读取（``_prepare_invoke``）都已在**主线程**完成，
        线程里拿到的 ``invoke`` 是个不碰 Session 的闭包——所以这里可以安全地
        并发，不会碰上跨线程共用 Session 的那两个实测崩溃（见
        :meth:`_prepare_invoke`）。
        """
        return self._run_step(
            index, aspect, hint,
            connection_id=connection_id, connection_name=connection_name,
            capability=capability, tool=_tool_name(capability), kind=kind,
            timeout_seconds=timeout_seconds, invoke=invoke,
        )

    def run_parallel(
        self,
        task: str,
        aspects: list[str],
        *,
        kind: str | None = None,
        max_workers: int | None = None,
        timeout_seconds: float = 60.0,
    ) -> OrchestrationResult:
        """并行执行同一任务的不同侧面，互不知情。

        用于「从技术/经济/法律三个角度评估 X」——各角度不该被彼此污染。
        """
        started = time.perf_counter()
        if not aspects:
            return OrchestrationResult(ok=False, mode="parallel", steps=[],
                                      error="没有指定任何侧面", duration_ms=0)

        workers = max_workers or self._max_workers
        # 串行地「先路由，再在主线程把 DB 读取做完」：每步避开前面已用的连接，
        # 否则打平的分数会让多个侧面全落同一个 Agent（实测三个候选同为 4.5）。
        # 真正的网络调用仍在线程里并行，所以耗时不叠加。
        picked: list[tuple[str, str, str]] = []
        # 每个侧面一个「已就绪、不碰 DB」的执行器；为 None 表示退回共享 invoke。
        prepared: list[Callable[..., dict[str, Any]] | None] = []
        used: set[str] = set()
        planning_errors: list[tuple[int, str, str]] = []
        for i, aspect in enumerate(aspects):
            hint = f"{task}\n\n{aspect}"
            try:
                choice = self._pick(hint, exclude=used, kind=kind)
            except ValueError as exc:
                planning_errors.append((i, aspect, str(exc)))
                continue
            try:
                # ⚠️ 必须在主线程做：连接行、凭证解密、适配器构造都在这里读 DB。
                # 放到工作线程里就会跨线程共用 Session（见 _prepare_invoke）。
                ready = self._prepare_invoke(choice[0])
            except Exception as exc:  # noqa: BLE001 — 取不到/被停用/缺凭证都如实记
                planning_errors.append(
                    (i, aspect, f"{type(exc).__name__}: {exc}"))
                continue
            used.add(choice[0])
            picked.append((i, aspect, choice))
            prepared.append(_as_invoke_shim(ready) if ready is not None else None)

        results: list[StepResult] = [
            StepResult(index=i, step=aspect, ok=False, error=err)
            for i, aspect, err in planning_errors
        ]
        if picked:
            with ThreadPoolExecutor(max_workers=min(workers, len(picked))) as pool:
                futures = [
                    pool.submit(self._dispatch_step, i, aspect, f"{task}\n\n{aspect}",
                                cid, name, cap, kind, timeout_seconds, ready)
                    for (i, aspect, (cid, cap, name)), ready in zip(picked, prepared)
                ]
                results.extend(f.result() for f in futures)

        results.sort(key=lambda s: s.index)
        ok_count = sum(1 for s in results if s.ok)
        return OrchestrationResult(
            ok=ok_count > 0, mode="parallel", steps=results,
            final_output=[s.to_public() for s in results],
            error="" if ok_count == len(results)
            else f"{len(results) - ok_count}/{len(results)} 个侧面失败",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def run_pipeline(
        self,
        stages: list[dict[str, str]],
        *,
        timeout_seconds: float = 60.0,
    ) -> OrchestrationResult:
        """显式指定每步用哪个连接。

        ``stages`` 每项可含::

            {"connection_id": ..., "capability": ..., "tool": ...,
             "params_extra": {...}, "instruction": ...}

        ``tool`` 只有「必须指名工具」的适配器（如 ``mcp_server``）需要——
        它的 ``call_tool`` 读 ``params["name"]``，只给自然语言会诚实报
        ``hub_missing_tool``。``chat`` 这类自然语言能力不需要它。

        需要精确控制时用这个方法：路由打分是确定性的，但未必符合你的意图。
        """
        started = time.perf_counter()
        context: list[dict[str, str]] = []
        results: list[StepResult] = []

        for i, stage in enumerate(stages):
            instruction = str(stage.get("instruction") or "")
            res = self._run_step(
                i, str(stage.get("step") or f"stage{i + 1}"), instruction,
                connection_id=str(stage.get("connection_id") or ""),
                capability=str(stage.get("capability") or ""),
                tool=str(stage.get("tool") or ""),
                context=context or None, timeout_seconds=timeout_seconds,
                params_extra=stage.get("params_extra") or None,
            )
            results.append(res)
            if not res.ok:
                return OrchestrationResult(
                    ok=False, mode="pipeline", steps=results, final_output=None,
                    error=f"第 {i + 1} 步失败：{res.error}",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            text = _as_text(res.output)
            if text:
                context.append({"role": "assistant", "content": text})

        return OrchestrationResult(
            ok=True, mode="pipeline", steps=results,
            final_output=_as_text(results[-1].output) if results else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------------ #
    # 汇总
    # ------------------------------------------------------------------ #

    def summarize(
        self,
        result: OrchestrationResult,
        *,
        instruction: str = "把这些结果整合成一份结论。",
        kind: str | None = "openai_chat",
        timeout_seconds: float = 90.0,
    ) -> OrchestrationResult:
        """让一个 Agent 把多步产出整合成最终结论。

        汇总器不是 Agent 时（匹配不上）**如实返回原结果**，
        不硬凑一个「总结」出来。
        """
        if not result.succeeded:
            return result

        parts = [f"【{s.step}】\n{_as_text(s.output)}"
                 for s in result.succeeded if _as_text(s.output)]
        if not parts:
            return result

        merged = self._run_step(len(result.steps), "汇总",
                               f"{instruction}\n\n" + "\n\n".join(parts),
                               kind=kind, timeout_seconds=timeout_seconds)
        if not merged.ok:
            # 汇总失败**不抹掉**已成功的步骤产出
            return OrchestrationResult(
                ok=result.ok, mode=result.mode, steps=[*result.steps, merged],
                final_output=result.final_output,
                error=f"汇总失败（已保留各步产出）：{merged.error}",
                duration_ms=result.duration_ms,
            )
        return OrchestrationResult(
            ok=True, mode=f"{result.mode}+summarize", steps=[*result.steps, merged],
            final_output=merged.output,
            duration_ms=result.duration_ms + merged.latency_ms,
        )


def _tool_name(cap: str) -> str:
    """从路由给出的能力名里取工具名。

    mcp_server 的 ``call_tool`` 读 ``params["name"]``，而路由把它暴露成
    ``tool:add`` 这样的能力名，所以取最后一段作为工具名。

    对 ``chat`` 这类自然语言能力返回空串 —— 适配器按自然语言处理，不需要
    指定工具。实测 ``{prompt, tool, arguments}`` 会失败，只有
    ``{name, arguments}`` 成功。
    """
    return cap.split(":", 1)[1] if cap.startswith("tool:") else ""


def _as_text(output: Any) -> str:
    """把任意输出压成可拼进上下文的文本。

    openai_chat 返回 ``{"text":..., "model":...}``，mcp 返回原始工具结果。
    """
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("text", "content", "result", "output", "data"):
            val = output.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, (dict, list)):
                nested = _as_text(val)
                if nested:
                    return nested
        return ""
    if isinstance(output, list):
        return "\n".join(p for p in (_as_text(i) for i in output) if p)
    return str(output)
