"""任务档案库（A-上下文持久化-02 / -03）。

**档案是什么**：一个任务的「目标与里程碑 / 角色分工表 / 决策日志 / 产出物版本 /
依赖 / 变更流水」合起来。需求原文：「给每个任务建『任务档案库』」。

**为什么是读模型**：这些信息**已经存在**——``tasks``（目标与状态）、
``task_events``（变更流水）、``task_dependencies``（依赖）、``task_attempts``
（产出物版本 / 检查点）、``agent_instances`` + ``team_definitions``（分工）、
审计哈希链（决策日志）。另存一份「档案表」必然与真源漂移，所以本模块**现算**，
并把每个字段的来源写进返回值 ``sources``，让「这份档案从哪来」可核对。

三个交付动作
------------

1. :meth:`TaskArchive.archive` —— 档案全景（需求 A-上下文持久化-01 的读面）。
2. :meth:`TaskArchive.briefing` —— **「翻档案」一秒上手**（-02）：产出**有字数上限**
   的可粘贴简报。新加入的 Agent 读这一段就够，不必翻全表。上限是硬约束：
   超限**明确标注已截断**并给出完整档案入口，不做「看起来很全其实被悄悄截掉」。
3. :meth:`TaskArchive.retrospective` / :meth:`TaskArchive.distill` —— **复盘与知识沉淀**
   （-03）：跑完的任务自动生成复盘报告；经验沉淀成**可复用模板**（落
   ``.runtime/dossier/``），下次类似任务直接套。

诚实边界
--------

* 没有的信息就是空（``[]`` / ``None``）+ 一条 ``notes`` 说明**为什么**空，
  绝不填料（例如「该任务没有子任务」而不是编一个分工表）。
* 「决策日志」来自审计哈希链（``audit_events``），按 actor 隔离查询；
  查不到就是空，不伪造决策。
* 「产出物版本」来自 ``task_attempts`` 的检查点；没有尝试行时说明「尚未产生版本」。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from ...db.models import AuditEvent, Task, TaskAttempt, TaskDependency, TaskEvent
from ...db.team_models import AgentInstance, TeamDefinition

#: 「一秒上手」简报的默认字数上限（**硬约束**，超限明确标注截断）。
DEFAULT_BRIEFING_LIMIT = 4000

#: 档案正文里视为「里程碑」的事件类别（``task_events.kind``）。
_MILESTONE_KINDS = ("status", "plan", "progress")

_KNOWLEDGE_FRONT_MATTER = "---\n{body}\n---\n"


def dossier_dir() -> Path:
    return Path(os.environ.get("FY_DOSSIER_DIR", ".runtime/dossier"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", (text or "").strip(), flags=re.UNICODE)
    return s.strip("-").lower() or "knowledge"


def _dt(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


class TaskArchive:
    """任务档案库（读模型 + 知识沉淀）。"""

    def __init__(self, session: Session, *, audit: AuditService | None = None,
                 directory: str | os.PathLike[str] | None = None) -> None:
        self.s = session
        self.audit = audit
        self._dir = Path(directory) if directory else dossier_dir()

    # ------------------------------------------------------------------
    # 载入（owner 隔离）
    # ------------------------------------------------------------------
    def _task(self, actor: Actor, task_id: str) -> Task:
        actor.require_authenticated()
        task = self.s.get(Task, task_id)
        if task is None or task.owner_id != actor.owner_id:
            # 他人任务一律 404，不泄露存在性
            raise NotFound("task_not_found", f"任务不存在：{task_id}")
        return task

    def _events(self, task: Task) -> list[TaskEvent]:
        return list(self.s.execute(
            select(TaskEvent).where(TaskEvent.task_id == task.id)
            .order_by(TaskEvent.created_at.asc())
        ).scalars())

    def _decisions(self, actor: Actor, task: Task) -> list[dict[str, Any]]:
        """决策日志 = 审计哈希链上与本任务相关的帧（按 actor 隔离）。"""
        identity = AuditService.identity_of(actor)
        if identity is None:
            return []
        rows = list(self.s.execute(
            select(AuditEvent)
            .where(AuditEvent.actor == identity)
            .where(
                (AuditEvent.target == task.id)
                | (AuditEvent.details["task_id"].as_string() == task.id)
            )
            .order_by(AuditEvent.seq.asc())
        ).scalars())
        # ⚠️ audit_events **没有时间戳列**（哈希链靠 seq 定序）。所以这里不编一个
        # 时间出来：at 明确为 None，展示层回落到「#seq」。
        return [
            {"seq": r.seq, "action": r.action, "target": r.target,
             "details": r.details or {}, "at": None}
            for r in rows
        ]

    def _roster(self, task: Task) -> dict[str, Any]:
        """角色分工表：来自团队定义与成员实例（真实数据）。"""
        teams = list(self.s.execute(
            select(TeamDefinition).where(
                (TeamDefinition.root_task_id == task.id)
                | (TeamDefinition.root_task_id == (task.root_task_id or task.id))
            )
        ).scalars())
        instances = list(self.s.execute(
            select(AgentInstance).where(
                (AgentInstance.root_task_id == task.id)
                | (AgentInstance.parent_task_id == task.id)
                | (AgentInstance.subtask_id == task.id)
            ).order_by(AgentInstance.run_batch.asc(), AgentInstance.role.asc())
        ).scalars())
        return {
            "teams": [
                {"team_id": t.id, "name": t.name, "mode": t.mode, "state": t.state,
                 "coordinator_role": t.coordinator_role,
                 "members": [m for m in (t.members or [])]}
                for t in teams
            ],
            "members": [
                {"instance_id": i.id, "role": i.role, "title": i.title,
                 "state": i.state, "session_id": i.session_id,
                 "requested_model": i.requested_model,
                 "effective_model": i.effective_model,
                 "steps": i.steps, "blocked_reason": i.blocked_reason,
                 "current_goal": i.current_goal}
                for i in instances
            ],
        }

    def _artifacts(self, task: Task) -> list[dict[str, Any]]:
        """产出物版本：来自任务尝试行（每次尝试 = 一个版本 / 一个检查点）。"""
        attempts = list(self.s.execute(
            select(TaskAttempt).where(TaskAttempt.task_id == task.id)
            .order_by(TaskAttempt.attempt_no.asc())
        ).scalars())
        result = task.result if isinstance(task.result, dict) else None
        return [
            {"version": a.attempt_no, "status": a.status,
             "checkpoint_ref": a.checkpoint_ref,
             "started_at": _dt(a.started_at), "ended_at": _dt(a.ended_at),
             "result": result if a.attempt_no == attempts[-1].attempt_no else None}
            for a in attempts
        ]

    def _dependencies(self, task: Task) -> dict[str, Any]:
        blocked_by = list(self.s.execute(
            select(TaskDependency.depends_on_task_id)
            .where(TaskDependency.task_id == task.id)
        ).scalars())
        blocks = list(self.s.execute(
            select(TaskDependency.task_id)
            .where(TaskDependency.depends_on_task_id == task.id)
        ).scalars())
        subtasks = list(self.s.execute(
            select(Task.id).where(Task.parent_task_id == task.id)
        ).scalars())
        return {"blocked_by": blocked_by, "blocks": blocks, "subtasks": subtasks}

    # ------------------------------------------------------------------
    # 1. 档案全景
    # ------------------------------------------------------------------
    def archive(self, actor: Actor, task_id: str) -> dict[str, Any]:
        """档案全景。每个字段都标了来源（``sources``），便于核对不被美化的口径。"""
        task = self._task(actor, task_id)
        events = self._events(task)
        roster = self._roster(task)
        deps = self._dependencies(task)
        artifacts = self._artifacts(task)
        decisions = self._decisions(actor, task)

        milestones = [
            {"kind": e.kind, "from_status": e.from_status, "to_status": e.to_status,
             "detail": e.detail or {}, "at": _dt(e.created_at)}
            for e in events if e.kind in _MILESTONE_KINDS
        ]

        notes: list[str] = []
        if not milestones:
            notes.append("该任务尚无状态/计划/进度类事件，里程碑为空（不是没展示）")
        if not roster["members"] and not roster["teams"]:
            notes.append("该任务未绑定团队或成员实例，分工表为空")
        if not artifacts:
            notes.append("该任务尚无尝试记录，产出物版本为空")
        if not decisions:
            notes.append("审计链上没有与本任务相关的帧，决策日志为空")
        if not deps["subtasks"]:
            notes.append("该任务没有子任务")

        return {
            "task_id": task.id,
            "objective": {
                "goal": task.goal,
                "domain": task.domain,
                "mode": task.mode,
                "strategy": task.strategy,
                "status": task.status,
                "stage": task.stage,
                "progress_percent": task.progress_percent,
                "weight": task.weight,
                "critical": bool(task.critical),
                "deadline": _dt(task.deadline),
                "created_at": _dt(task.created_at),
                "updated_at": _dt(task.updated_at),
                "blocked_reason": task.blocked_reason,
                "blocked_since": _dt(task.blocked_since),
                "planned_start": _dt(task.planned_start),
                "planned_end": _dt(task.planned_end),
            },
            "milestones": milestones,
            "roster": roster,
            "decisions": decisions,
            "artifacts": artifacts,
            "dependencies": deps,
            "change_log": [
                {"kind": e.kind, "from_status": e.from_status, "to_status": e.to_status,
                 "detail": e.detail or {}, "at": _dt(e.created_at)}
                for e in events
            ],
            "failure": task.failure if isinstance(task.failure, dict) else None,
            "notes": notes,
            "sources": {
                "objective": "tasks",
                "milestones/change_log": "task_events",
                "roster": "team_definitions + agent_instances",
                "decisions": "audit_events（哈希链）",
                "artifacts": "task_attempts（+ tasks.result）",
                "dependencies": "task_dependencies + tasks.parent_task_id",
            },
            "generated_at": _now_iso(),
        }

    # ------------------------------------------------------------------
    # 2. 「翻档案」一秒上手（有字数上限）
    # ------------------------------------------------------------------
    def briefing(self, actor: Actor, task_id: str, *,
                 limit: int = DEFAULT_BRIEFING_LIMIT) -> dict[str, Any]:
        """给新加入 Agent 的**可粘贴简报**：读这一段就够上手。

        ``limit`` 是硬上限（字符数）。超限时返回 ``truncated=True`` 并在正文末尾
        明确标注截断与完整档案入口——**绝不假装这就是全部**。
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 200:
            raise ValidationFailed("briefing_limit_invalid",
                                   "limit 必须是 >= 200 的整数")
        data = self.archive(actor, task_id)
        obj = data["objective"]
        lines: list[str] = [
            f"# 任务档案简报 · {task_id}",
            "",
            "## 一句话目标",
            "",
            obj["goal"],
            "",
            "## 当前状态",
            "",
            f"- 状态：{obj['status']}　阶段：{obj['stage']}　"
            f"进度：{obj['progress_percent'] if obj['progress_percent'] is not None else '未开始'}%",
            f"- 领域/模式/策略：{obj['domain']} / {obj['mode']} / {obj['strategy']}",
            f"- 截止：{obj['deadline'] or '未设置'}",
        ]
        if obj["blocked_reason"]:
            lines.append(f"- 卡点：{obj['blocked_reason']}（自 {obj['blocked_since'] or '未知'}）")

        if data["roster"]["members"]:
            lines += ["", "## 谁在干（分工）", ""]
            for m in data["roster"]["members"]:
                lines.append(
                    f"- {m['role']}｜{m['title'] or '—'}｜状态 {m['state']}"
                    f"｜模型 {m['effective_model'] or m['requested_model'] or '继承默认'}"
                    + (f"｜卡点：{m['blocked_reason']}" if m["blocked_reason"] else "")
                )
        else:
            lines += ["", "## 谁在干（分工）", "", "（未绑定团队/成员实例）"]

        if data["milestones"]:
            lines += ["", "## 关键里程碑（最近 5 条）", ""]
            for m in data["milestones"][-5:]:
                transition = (f"{m['from_status']}→{m['to_status']}"
                              if m["from_status"] or m["to_status"] else m["kind"])
                lines.append(f"- {m['at'] or '?'}　{transition}")

        if data["decisions"]:
            lines += ["", "## 关键决策（最近 5 条）", ""]
            for d in data["decisions"][-5:]:
                lines.append(f"- #{(d.get('seq'))}　{d['action']}")

        if data["artifacts"]:
            lines += ["", "## 产出版本", ""]
            for a in data["artifacts"]:
                lines.append(
                    f"- v{a['version']}｜{a['status']}｜检查点 {a['checkpoint_ref'] or '无'}"
                )
        else:
            lines += ["", "## 产出版本", "", "（尚无尝试记录）"]

        deps = data["dependencies"]
        lines += [
            "", "## 依赖", "",
            f"- 被阻塞于：{deps['blocked_by'] or '无'}",
            f"- 阻塞：{deps['blocks'] or '无'}",
            f"- 子任务：{deps['subtasks'] or '无'}",
        ]
        if data["notes"]:
            lines += ["", "## 说明（哪些是空的、为什么）", ""]
            lines += [f"- {n}" for n in data["notes"]]

        text = "\n".join(lines) + "\n"
        truncated = len(text) > limit
        if truncated:
            marker = (f"\n> …（简报已截断：全文 {len(text)} 字符 > 上限 {limit}；"
                      f"完整档案见 GET /api/dossier/tasks/{task_id}/archive）\n")
            text = text[: max(0, limit - len(marker))] + marker
        return {
            "task_id": task_id,
            "briefing": text,
            "chars": len(text),
            "limit": limit,
            "truncated": truncated,
            "archive_endpoint": f"/api/dossier/tasks/{task_id}/archive",
            "note": "新加入的 Agent 读这一段即可接手；不必翻全表。",
        }

    # ------------------------------------------------------------------
    # 3. 复盘与知识沉淀
    # ------------------------------------------------------------------
    def retrospective(self, actor: Actor, task_id: str) -> dict[str, Any]:
        """任务复盘：时间线 + 决策 + 指标 + 经验条目（每条都能追到来源）。"""
        data = self.archive(actor, task_id)
        obj = data["objective"]
        timeline = data["change_log"]
        roster = data["roster"]

        span_minutes: float | None = None
        started = obj["created_at"]
        ended = obj["updated_at"]
        try:
            if started and ended:
                span_minutes = round((
                    datetime.fromisoformat(ended) - datetime.fromisoformat(started)
                ).total_seconds() / 60, 1)
        except ValueError:
            span_minutes = None

        lessons: list[dict[str, str]] = []
        if obj["blocked_reason"]:
            lessons.append({
                "kind": "blocker",
                "text": f"出现过阻塞：{obj['blocked_reason']}；下次同类任务提前确认该前置条件",
            })
        if data["failure"]:
            lessons.append({
                "kind": "failure",
                "text": f"失败记录：{json.dumps(data['failure'], ensure_ascii=False)[:300]}",
            })
        if roster["members"]:
            reworked = [m for m in roster["members"]
                        if m["state"] in ("waiting_rework", "blocked", "failed")]
            if reworked:
                lessons.append({
                    "kind": "roster",
                    "text": f"{len(reworked)} 个成员在复盘时仍未通过（"
                            + "、".join(m["role"] for m in reworked) + "）；下次给这几步留更多预算",
                })
        if not lessons:
            lessons.append({
                "kind": "baseline",
                "text": "本次没有记录到阻塞/失败/返工，按现有分工与预算直接复用即可",
            })

        report = "\n".join([
            f"# 任务复盘：{obj['goal'][:60]}",
            "",
            f"- 任务：`{task_id}`　状态：{obj['status']}　阶段：{obj['stage']}",
            f"- 进度：{obj['progress_percent'] if obj['progress_percent'] is not None else '未开始'}%"
            f"　总步数：{len(timeline)} 条变更事件",
            f"- 历时（按任务行时间戳）：{span_minutes if span_minutes is not None else '未知'} 分钟",
            f"- 参与成员：{len(roster['members'])} 人 / {len(roster['teams'])} 个团队",
            "",
            "## 经验条目",
            "",
            *[f"- [{l['kind']}] {l['text']}" for l in lessons],
            "",
            "## 时间线",
            "",
            *[f"- {e['at'] or '?'}　{e['kind']}"
              + (f"　{e['from_status']}→{e['to_status']}"
                 if e["from_status"] or e["to_status"] else "")
              for e in timeline],
            "",
            "## 决策日志",
            "",
            *([f"- #{d.get('seq')}　{d['action']}" for d in data["decisions"]] or ["（无）"]),
            "",
            "> 来源：tasks / task_events / task_attempts / agent_instances / 审计哈希链。",
            "",
        ])
        return {
            "task_id": task_id,
            "report": report,
            "timeline": timeline,
            "decisions": data["decisions"],
            "metrics": {
                "change_events": len(timeline),
                "decision_count": len(data["decisions"]),
                "member_count": len(roster["members"]),
                "team_count": len(roster["teams"]),
                "attempt_versions": len(data["artifacts"]),
                "span_minutes": span_minutes,
            },
            "lessons": lessons,
            "generated_at": _now_iso(),
        }

    def distill(self, actor: Actor, task_id: str, *, name: str | None = None,
                tags: list[str] | None = None) -> dict[str, Any]:
        """把复盘沉淀成**可复用知识模板**（落盘），下次类似任务直接套。"""
        retro = self.retrospective(actor, task_id)
        arch = self.archive(actor, task_id)
        title = name or f"{arch['objective']['goal'][:40]} · 经验模板"
        knowledge_id = _slug(title)
        body = "\n".join([
            f"# {title}",
            "",
            "## 适用场景",
            "",
            arch["objective"]["goal"],
            "",
            "## 分工模板",
            "",
            *([f"- {m['role']}｜{m['title'] or '—'}"
               for m in arch["roster"]["members"]] or ["（无记录；按同类任务默认分工）"]),
            "",
            "## 步骤模板",
            "",
            *([f"- {e['kind']}"
               + (f"：{e['from_status']}→{e['to_status']}"
                  if e["from_status"] or e["to_status"] else "")
               for e in arch["change_log"]] or ["（无变更流水）"]),
            "",
            "## 经验条目",
            "",
            *[f"- [{l['kind']}] {l['text']}" for l in retro["lessons"]],
            "",
            "## 复用时怎么改",
            "",
            "1. 替换目标与输入；2. 按上表核对该有的角色是否齐；"
            "3. 预算按复盘里的用量上调/下调；4. 保留本页「经验条目」作为检查清单。",
            "",
        ])
        front = _KNOWLEDGE_FRONT_MATTER.format(body=json.dumps({
            "owner_id": actor.owner_id,
            "source_task_id": task_id,
            "title": title,
            "tags": list(tags or []),
            "created_at": _now_iso(),
        }, ensure_ascii=False, sort_keys=True))
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{knowledge_id}.md"
        path.write_text(front + body, encoding="utf-8", newline="\n")
        if self.audit is not None:
            self.audit.append(actor, "dossier.knowledge_distilled", knowledge_id,
                              {"task_id": task_id, "path": str(path)})
        return {
            "knowledge_id": knowledge_id,
            "title": title,
            "path": str(path),
            "source_task_id": task_id,
            "lessons": retro["lessons"],
            "report": retro["report"],
        }

    def list_knowledge(self, actor: Actor) -> dict[str, Any]:
        """本 owner 沉淀过的知识模板（按 front matter 的 owner_id 隔离）。"""
        actor.require_authenticated()
        items: list[dict[str, Any]] = []
        if self._dir.is_dir():
            for path in sorted(self._dir.glob("*.md")):
                meta = _read_front_matter(path.read_text(encoding="utf-8"))
                if meta is None or meta.get("owner_id") != actor.owner_id:
                    continue
                items.append({
                    "knowledge_id": path.stem,
                    "title": meta.get("title"),
                    "source_task_id": meta.get("source_task_id"),
                    "tags": list(meta.get("tags") or []),
                    "created_at": meta.get("created_at"),
                })
        items.sort(key=lambda it: str(it.get("created_at")), reverse=True)
        return {"items": items, "total": len(items)}

    def knowledge_detail(self, actor: Actor, knowledge_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        path = self._dir / f"{knowledge_id}.md"
        if not path.is_file():
            raise NotFound("knowledge_not_found", f"知识模板不存在：{knowledge_id}")
        text = path.read_text(encoding="utf-8")
        meta = _read_front_matter(text) or {}
        if meta.get("owner_id") != actor.owner_id:
            raise NotFound("knowledge_not_found", f"知识模板不存在：{knowledge_id}")
        return {"knowledge_id": knowledge_id, "meta": meta, "markdown": text}


def _read_front_matter(text: str) -> dict[str, Any] | None:
    """读 ``---\\n{json}\\n---`` 形式的 front matter（与 P13 内容包同一范式）。"""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        data = json.loads(text[4:end])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


__all__ = ["TaskArchive", "dossier_dir", "DEFAULT_BRIEFING_LIMIT"]
