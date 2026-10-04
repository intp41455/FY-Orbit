"""产物版本门禁（需求 7）。

「产物」是什么
--------------
本模块把**产物**定义为：**由某个执行体（工作流 / Agent / 代码导出）生成、
有确定字节内容、且会被「使用」或「对外」的输出**。在本项目里它落成两种形态
（:data:`~find_yourself.db.artifact_gate_models.ARTIFACT_KINDS`）：

* ``asset`` —— 资产库里的图片/音频/音乐/文档（``assets`` 表）。选它因为它是
  本项目唯一「生成即成品」的形态：``imagegen``/``audiogen`` 直接落字节，且
  存在明确的「对外」语义（``GrantService`` 的 gdrive出口）。
* ``code_bundle`` —— DSL 画布/工作流导出的代码包。选它因为它是唯一「内容会
  被执行」的形态：一份没验过的代码包被放行，等于把未验证代码推给别人跑。

**不选知识库文档**：``kb_documents`` 有自己的 ``status``（indexing/ready/...）
与chunk 完整性语义，把门禁套上去会与那套状态**并列成两个真相源**——和
``team_approval_models`` 拒绝复制 HITL 状态是同一条纪律。

不选的还有画布实例本身：``canvas_instances`` 是**协作容器**（长期存在、反复
被改），不是「一次产出」。给它套「第 N 版 + 冻结内容」会把协作语义和产物
语义搅在一起。

版本即不可变快照
----------------
一登记就���版本冻结：``content_digest`` 由**真实字节**算出，之后这一行不再变。
改内容 = 建新的一版。这不是洁癖，而是门禁能成立的前提：若一版的内容可以
悄悄变，「验证通过」到底证明了什么就无法回答，TOCTOU 会把门禁变成装饰。

必检项来自服务端策略，不来自调用方
----------------------------------
见 :mod:`~find_yourself.db.artifact_gate_models` 的模块 docstring。调用方能
加额外检查，不能声明「无需检查」。

「独立测试」怎么理解
--------------------
需求原文「产物版本门禁与独立测试」有两种读法，本模块**两种都做**，因为它们
防的是不同的东西：

1. **门禁机制自身有独立测试** —— ``tests/unit/test_artifact_gate.py`` 里的
   契约测试（含「本该失败但实现会通过」那一条）。防的是**门禁退化**。
2. **产物能触发独立测试，且独立测试是放行的前提** —— 这里的取舍。产物要
   走到 ``verified``，**必须**有一份 ``independent_test`` 的**通过**证据，
   而该证据由 :meth:`ArtifactGateService.run_independent_test` 在**独立子
   进程**里真实执行命令产生（复用
   :class:`~find_yourself.services.verification.TrustedVerificationRunner`），
   不是由调用方声称的布尔值。防的是**未验证产物被当成合格产物**。

选第2 种作为设计主线，理由：只有第 1 种的话，门禁本身可以被测得很好，而
被门禁放行的东西依然是「调用方说它好了」——那不是门禁，是转发器。而第 2 种
让「独立测试」成为**数据模型里的一个必需证据**，不是一句流程规范。
第 1 种作为回归防线叠在上面。

判定侧的三道闸
--------------
:meth:`ArtifactGateService.evaluate` 判「能不能用/ 能不能对外」时叠三道：

1. **状态** —— 必须是 ``verified`` / ``released``。
2. **必检项齐全且全过** —— 少一项就是「未验证」，而不是「默认通过」。
3. **证据仍然新鲜** —— 每份摘要类证据的 ``observed_digest`` 必须仍等于版本
   的 ``content_digest``。这一条专门挡「先验后改」。

所以 ``evaluate`` **不接受**「调用方额外声明的通过项」来补必检项：额外检查
可以加严，不能替代。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.artifact_gate_models import (
    ARTIFACT_KINDS,
    ARTIFACT_STATES,
    GATE_TRANSITIONS,
    TERMINAL_ARTIFACT_STATES,
    USABLE_ARTIFACT_STATES,
    ArtifactGateCheck,
    ArtifactVersion,
    required_checks_for,
)
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

#: 独立测试的默认超时。取30s：比产品侧 ``execute_test`` 默认更保守，因为
#: 这里跑在**用户请求**的同步路径上，不能让一个挂死的测试拖住 API。
INDEPENDENT_TEST_TIMEOUT_SECONDS = 30.0

#: 摘要类检查：它们的证据里必须带 ``observed_digest``，判定时要与版本摘要
#: 比对。带外键式的作用——改内容后这些证据自动失效。
DIGEST_BOUND_CHECKS = ("content_digest",)


class ArtifactGateService:
    """产物版本门禁：登记 / 送检 / 记录独立测试 / 判定 / 放行 / 扣住。

    刻意保持小而完整：六个动作对应产物的六个时刻。与 ``hitl`` /
    ``team_approval`` 共用同一个 ``AuditService`` 哈希链与同一个 ``Actor``，
    但**不共用状态机**——那三者管的是「执行是否被挂起」，本模块管的是
    「产物是否合格」。把它们合并会产生两个真相源。
    """

    def __init__(self, session: Session, audit: Any | None = None):
        self.session = session
        self.audit = audit

    # ------------------------------------------------------------------
    # 1) 登记一个新版本（内容此刻冻结）
    # ------------------------------------------------------------------
    def register(
        self,
        actor: Actor,
        *,
        artifact_kind: str,
        artifact_id: str,
        workspace_dir: str | Path,
        target_files: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """登记「某产物的第 N 版」，返回版本视图。初始状态恒为 ``draft``。

        版号由服务自己算：同产物已有几版就登记第 N+1 版。调用方**不能**指定
        版号——能指定就能覆盖既有版本，「不可变快照」当场失效。

        ``content_digest`` 在这里由真实字节算出（走
        :meth:`compute_digest`），所以它描述的是磁盘上此刻的内容，而不是
        调用方声称的内容。
        """
        actor.require_authenticated()
        if artifact_kind not in ARTIFACT_KINDS:
            raise ValidationFailed(
                "unknown_artifact_kind",
                f"artifact_kind must be one of {list(ARTIFACT_KINDS)}, "
                f"got {artifact_kind!r}",
            )
        artifact_id = (artifact_id or "").strip()
        if not artifact_id:
            raise ValidationFailed("artifact_id_required", "artifact_id is required")

        root = Path(workspace_dir).resolve()
        if not root.is_dir():
            raise ValidationFailed(
                "workspace_missing",
                f"workspace_dir does not exist or is not a directory: {root}",
            )
        # 必检项由策略给，不是由参数给。这里取一次有两个作用：把「这个形态
        # 有策略」这件事在登记时就验证掉（而不是等到判定时才发现），
        # 以及让视图能如实告诉调用方「这一版要满足什么」。
        try:
            required = required_checks_for(artifact_kind)
        except ValueError as exc:  # pragma: no cover - 与上面的白名单同源
            raise ValidationFailed("unknown_artifact_kind", str(exc)) from exc

        files = list(target_files) if target_files else None
        digest, observed = self.compute_digest(root, files)

        version_no = self._next_version_no(artifact_kind, artifact_id)
        row = ArtifactVersion(
            id=f"agv-{uuid4().hex[:12]}",
            owner_id=actor.owner_id or actor.service_id,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            version_no=version_no,
            content_digest=digest,
            workspace_ref={
                "dir": str(root),
                "files": sorted(observed),
            },
            state="draft",
            decision_note=note or "",
            created_by=actor.owner_id or actor.service_id,
        )
        self.session.add(row)
        self.session.flush()
        self._audit(
            actor, "artifact_gate.version_registered", row.id,
            {"kind": artifact_kind, "artifact_id": artifact_id,
             "version_no": version_no, "digest": digest},
        )
        return self._view(row)

    # ------------------------------------------------------------------
    # 2) 送检
    # ------------------------------------------------------------------
    def submit(self, actor: Actor, version_id: str, *, note: str = "") -> dict[str, Any]:
        """把 ``draft`` 送进 ``in_review``。此步不检查任何条件。

        此刻还没有证据可查，所以「送检」不该假装成一个判定——它只是把版本
        放到「等待证据」的位置上。真正判定在 :meth:`decide`。
        """
        actor.require_authenticated()
        row = self._row(version_id)
        self._require_visible(actor, row)
        return self._transition(
            actor, row, "submit",
            values={"submitted_at": utcnow()},
            note=note,
        )

    # ------------------------------------------------------------------
    # 3) 记录检查证据
    # ------------------------------------------------------------------
    def record_check(
        self,
        actor: Actor,
        version_id: str,
        check_name: str,
        *,
        status: str,
        observed_digest: str | None = None,
        evidence: dict[str, Any] | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        """记录一条检查证据（同版同名覆盖旧证据）。

        允许 ``owner`` 与 ``service`` 都记录：检查是**执行体**跑出来的，
        限定只有人能记录反而会逼着大家谎称结果由人生成。
        但注意这条**只写证据、不改状态**——它离「放行」还差一次显式的
        :meth:`decide`，所以「自己给自己写个 passed」不构成放行。
        """
        actor.require_authenticated()
        if status not in ("passed", "failed"):
            raise ValidationFailed(
                "bad_check_status",
                f"status must be 'passed' or 'failed', got {status!r} "
                "(there is deliberately no 'skipped': skipping and passing must "
                "not look alike in the ledger)",
            )
        name = (check_name or "").strip()
        if not name:
            raise ValidationFailed("check_name_required", "check_name is required")
        row = self._row(version_id)
        self._require_visible(actor, row)
        if row.state in TERMINAL_ARTIFACT_STATES:
            raise Conflict(
                "artifact_terminal",
                f"Artifact version {version_id} is terminal (state={row.state}); "
                "register a new version instead of re-testing this one",
            )
        if observed_digest is not None and len(observed_digest) != 64:
            raise ValidationFailed(
                "bad_observed_digest",
                "observed_digest must be a 64-char sha256 hex digest",
            )

        existing = self.session.execute(
            select(ArtifactGateCheck).where(
                ArtifactGateCheck.artifact_version_id == row.id,
                ArtifactGateCheck.check_name == name,
            )
        ).scalar_one_or_none()
        actor_id = actor.owner_id or actor.service_id
        if existing is None:
            existing = ArtifactGateCheck(
                id=f"agc-{uuid4().hex[:12]}",
                artifact_version_id=row.id,
                check_name=name,
                status=status,
                observed_digest=observed_digest,
                evidence=dict(evidence or {}),
                detail=detail or "",
                ran_by=actor_id,
            )
            self.session.add(existing)
        else:
            # 重跑覆盖：证据不并列。留多行就得回答「取哪一行」，而那个问题
            # 一旦答错，一份陈旧的通过证据就会复活。
            existing.status = status
            existing.observed_digest = observed_digest
            existing.evidence = dict(evidence or {})
            existing.detail = detail or ""
            existing.ran_by = actor_id
            existing.updated_at = utcnow()
            existing.version += 1
        self.session.flush()
        self._audit(
            actor, "artifact_gate.check_recorded", row.id,
            {"check": name, "status": status, "observed_digest": observed_digest},
        )
        return self._check_view(existing)

    # ------------------------------------------------------------------
    # 4) 独立测试：真跑一条命令
    # ------------------------------------------------------------------
    def run_independent_test(
        self,
        actor: Actor,
        version_id: str,
        command: list[str] | str,
        *,
        timeout_seconds: float = INDEPENDENT_TEST_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """在**独立子进程**里真跑 ``command``，把结果记成 ``independent_test`` 证据。

        复用 :class:`~find_yourself.services.verification.TrustedVerificationRunner`
        而不是自己写一遍 ``subprocess``：那份实现已经负责了「命令规范化 /
        超时 / 退出码 / stdout / stderr / 摘要」，而这里最怕的就是**抄一份
        然后漂移**（比如那边把超时记成 124，这边记成 -1，两边就不一致了）。

        跑的是**这个版本自己工作区**里的文件（``workspace_ref``），并且把
        实际观测到的摘要一并记进证据——于是「验完之后改内容」会在判定时被
        核对出来。
        """
        actor.require_authenticated()
        from .verification import TrustedVerificationRunner

        row = self._row(version_id)
        self._require_visible(actor, row)
        if row.state in TERMINAL_ARTIFACT_STATES:
            raise Conflict(
                "artifact_terminal",
                f"Artifact version {version_id} is terminal (state={row.state})",
            )
        if not command:
            raise ValidationFailed("command_required", "command is required")

        workdir = (row.workspace_ref or {}).get("dir")
        if not workdir:
            raise ValidationFailed(
                "no_workspace", f"Artifact version {version_id} has no workspace_ref.dir"
            )
        files = (row.workspace_ref or {}).get("files") or None

        receipt = TrustedVerificationRunner.execute_test(
            workdir,
            command,
            env=env,
            timeout_seconds=timeout_seconds,
            target_files=files,
        )
        # 观测摘要取自本子进程重算的结果，而不是抄版本上的 content_digest。
        # 抄上去就等于「自己判自己及格」——那样内容被改后依然会显示通过。
        observed = receipt.get("composite_artifact_hash") or None
        status = "passed" if receipt.get("passed") else "failed"
        detail = (
            f"exit_code={receipt.get('exit_code')} "
            f"verification_id={receipt.get('verification_id')}"
        )
        self.record_check(
            actor,
            row.id,
            "independent_test",
            status=status,
            observed_digest=observed,
            evidence=receipt,
            detail=detail,
        )
        return {
            "artifact_version_id": row.id,
            "check": "independent_test",
            "status": status,
            "observed_digest": observed,
            "receipt": receipt,
        }

    # ------------------------------------------------------------------
    # 5) 判定（唯一的入闸动作）
    # ------------------------------------------------------------------
    def decide(
        self, actor: Actor, version_id: str, action: str, *, note: str = ""
    ) -> dict[str, Any]:
        """推进状态机。只有 ``actor.require_owner()`` 能判定/放行/扣住。

        ``action`` 必须是 ``GATE_TRANSITIONS`` 里当前状态**真实存在**的出边。
        非法动作一律 ``ValidationFailed``（409/422），且**不改变任何状态**。

        ``verify_pass`` 是唯一会自动重核证据的动作：它会调
        :meth:`evaluate` 确认必检项齐全、全过、且证据未因内容变更而失效；
        不满足就落 ``verify_fail``（rejected），而不是抛错——
        「检查没过」是一个**结论**，不是一次调用失败。
        """
        actor.require_owner()  # 执行体不能给自己放行
        row = self._row(version_id)
        self._require_visible(actor, row)

        allowed = GATE_TRANSITIONS.get(row.state, {})
        if action not in allowed:
            raise ValidationFailed(
                "illegal_transition",
                f"action {action!r} is not legal from state {row.state!r}; "
                f"legal actions here: {sorted(allowed) or 'none (terminal state)'}",
            )

        if action == "verify_pass":
            #自动复核：证据不齐/ 过期 / 有失败项 → 直接落 rejected。
            verdict = self.evaluate(actor, version_id)
            if not verdict["all_required_passed"]:
                return self._transition(
                    actor, row, "verify_fail",
                    note=note or f"blocked by gate: {verdict['blocking_reasons']}",
                )
        return self._transition(actor, row, action, note=note)

    # ------------------------------------------------------------------
    # 6) 门禁判定：能不能用 / 能不能对外
    # ------------------------------------------------------------------
    def evaluate(self, actor: Actor, version_id: str) -> dict[str, Any]:
        """判定某一版当前「能不能用/ 能不能对外」，并给出**为什么**。

        叠三道闸（见模块 docstring）：状态、必检项、**内容未被偷改**。

        第三道闸的关键实现细节
        ----------------------
        「证据过期」**不是**拿 ``check.observed_digest`` 和
        ``row.content_digest`` 互相比——那两个都是**已冻结的历史值**，永远
        相等，那道闸就成了永真的装饰。真正的判据是拿**当前磁盘上的字节**
        重算摘要，再与版本冻结的 ``content_digest`` 比：不一致就说明
        「验完之后有人动了产物」，此时那份通过证据描述的是另一个内容。

        所以这一道闸**会读文件系统**，且在读不到时**fail closed**
        （按 stale 处理）——「工作区不见了」不能被当成「内容没变」。
        """
        actor.require_authenticated()
        row = self._row(version_id)
        self._require_visible(actor, row)

        required = required_checks_for(row.artifact_kind)
        checks = {
            c.check_name: c
            for c in self.session.execute(
                select(ArtifactGateCheck).where(
                    ArtifactGateCheck.artifact_version_id == row.id
                )
            ).scalars()
        }

        missing: list[str] = []
        failed: list[str] = []
        stale: list[str] = []
        for name in required:
            check = checks.get(name)
            if check is None:
                missing.append(name)
                continue
            if check.status != "passed":
                failed.append(name)
                continue
            if name in DIGEST_BOUND_CHECKS and not self._evidence_still_valid(row, check):
                stale.append(name)

        reasons: list[str] = []
        if missing:
            reasons.append(f"missing required checks: {sorted(missing)}")
        if failed:
            reasons.append(f"failed required checks: {sorted(failed)}")
        if stale:
            reasons.append(
                f"stale evidence (content changed after check): {sorted(stale)}"
            )
        if row.state not in USABLE_ARTIFACT_STATES:
            reasons.append(f"state is {row.state!r}, not in {list(USABLE_ARTIFACT_STATES)}")

        all_passed = not (missing or failed or stale)
        usable = all_passed and row.state in USABLE_ARTIFACT_STATES
        return {
            "artifact_version_id": row.id,
            "state": row.state,
            "usable": usable,
            # 「对外」比「可用」更严：必须已放行。仅 verified 还只是自用。
            "exportable": all_passed and row.state == "released",
            "all_required_passed": all_passed,
            "required_checks": list(required),
            "missing_checks": sorted(missing),
            "failed_checks": sorted(failed),
            "stale_checks": sorted(stale),
            "blocking_reasons": reasons,
            "content_digest": row.content_digest,
            "current_digest": self._current_digest(row),
            "version_no": row.version_no,
        }

    def _current_digest(self, row: ArtifactVersion) -> str | None:
        """按**当前磁盘字节**重算摘要。读不到就返回 ``None``（fail closed）。"""
        ref = row.workspace_ref or {}
        workdir = ref.get("dir")
        if not workdir:
            return None
        try:
            digest, _files = self.compute_digest(workdir, ref.get("files") or None)
        except (OSError, ValueError):
            return None
        return digest

    def _content_unchanged(self, row: ArtifactVersion) -> bool:
        """内容是否仍与冻结时一致。读不到字节**按已变处理**（保守默认）。"""
        current = self._current_digest(row)
        return current is not None and current == row.content_digest

    def _evidence_still_valid(
        self, row: ArtifactVersion, check: ArtifactGateCheck
    ) -> bool:
        """一条摘要类证据是否仍然作数。**两个条件都要满足**：

        1. **证据自洽**——它当时观测到的摘要（``observed_digest``）确实等于
           这一版冻结的摘要。否则就是有人手写了一个 ``passed`` 却填了别的
           摘要（或者干脆没填），那不是「检查通过」，那是一句声明。
        2. **内容未变**——按**当前磁盘字节**重算的摘要仍等于冻结摘要。
           这是挡「先验后改」的那一道：证据里记的是改动前的内容。

        只做第 1 条 → 漏「验完偷改」；只做第 2 条 → 漏「伪造证据」。
        两条都做才是一个真的门禁。任一条件拿不到答案都**按不通过处理**
        （fail closed）：读不到字节不等于「内容没变」。
        """
        if check.observed_digest != row.content_digest:
            return False
        return self._content_unchanged(row)

    def require_usable(self, actor: Actor, version_id: str) -> dict[str, Any]:
        """``evaluate`` 的强硬版：不可用就抛 :class:`PermissionDenied`。

        给「取出产物去执行/渲染」这类调用点用。把「能不能用」收在一个函数里，
        是为了让**每个**消费点都自动受同一套判据约束——散落在各处的
        ``if state == 'ok'`` 正是门禁失效的典型方式。
        """
        verdict = self.evaluate(actor, version_id)
        if not verdict["usable"]:
            raise PermissionDenied(
                "artifact_not_usable",
                f"Artifact version {version_id} is not usable: "
                f"{'; '.join(verdict['blocking_reasons'])}",
                403,
            )
        return verdict

    def require_exportable(self, actor: Actor, version_id: str) -> dict[str, Any]:
        """可对外（已放行 + 证据新鲜），否则 :class:`PermissionDenied`��"""
        verdict = self.evaluate(actor, version_id)
        if not verdict["exportable"]:
            raise PermissionDenied(
                "artifact_not_exportable",
                f"Artifact version {version_id} is not exportable: "
                f"{'; '.join(verdict['blocking_reasons'])}",
                403,
            )
        return verdict

    def get(self, actor: Actor, version_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        row = self._row(version_id)
        self._require_visible(actor, row)
        return self._view(row)

    def list_versions(
        self, actor: Actor, *, artifact_kind: str | None = None,
        artifact_id: str | None = None, state: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出我可见的版本。归属隔离：只返回 ``owner_id`` 是我自己的。"""
        actor.require_authenticated()
        stmt = select(ArtifactVersion).where(
            ArtifactVersion.owner_id == (actor.owner_id or actor.service_id)
        )
        if artifact_kind is not None:
            stmt = stmt.where(ArtifactVersion.artifact_kind == artifact_kind)
        if artifact_id is not None:
            stmt = stmt.where(ArtifactVersion.artifact_id == artifact_id)
        if state is not None:
            if state not in ARTIFACT_STATES:
                raise ValidationFailed(
                    "bad_state_filter",
                    f"state must be one of {list(ARTIFACT_STATES)}, got {state!r}",
                )
            stmt = stmt.where(ArtifactVersion.state == state)
        rows = self.session.execute(
            stmt.order_by(ArtifactVersion.created_at, ArtifactVersion.version_no)
        ).scalars()
        return [self._view(r) for r in rows]

    def list_checks(self, actor: Actor, version_id: str) -> list[dict[str, Any]]:
        actor.require_authenticated()
        row = self._row(version_id)
        self._require_visible(actor, row)
        checks = self.session.execute(
            select(ArtifactGateCheck)
            .where(ArtifactGateCheck.artifact_version_id == row.id)
            .order_by(ArtifactGateCheck.check_name)
        ).scalars()
        return [self._check_view(c) for c in checks]

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def compute_digest(
        workspace_dir: str | Path, target_files: list[str] | None = None
    ) -> tuple[str, list[str]]:
        """重算工作区的复合摘要，返回 ``(digest, 参与计算的文件相对路径)``。

        委托给 :class:`TrustedVerificationRunner.compute_artifact_digests`
        而不是自己写一遍 hash：那边已经处理了「跳过 . 开头目录 /
        __pycache__ / venv」和「文件不存在时跳过」，抄一份必然在这些边角上
        漂移，而漂移的表现是**同一份内容在两处算出不同摘要**——
        那种 bug 极难定位。
        """
        from .verification import TrustedVerificationRunner

        digests, composite = TrustedVerificationRunner.compute_artifact_digests(
            workspace_dir, target_files
        )
        return composite, sorted(digests)

    def syntax_check(self, workspace_dir: str | Path, target_files: list[str] | None = None) -> tuple[bool, str]:
        """对Python 源码做语法解析。返回 ``(ok, detail)``。

        刻意用 :func:`ast.parse` 而不是 ``compile``/``exec``：**只解析，不
        执行**。门禁不该在验证阶段运行被验证的代码。
        """
        root = Path(workspace_dir).resolve()
        if target_files:
            candidates = [root / f for f in target_files]
        else:
            candidates = [
                p for p in sorted(root.rglob("*.py"))
                if p.is_file() and "__pycache__" not in p.parts
            ]
        if not candidates:
            return False, "no Python files found to parse"
        errors: list[str] = []
        for path in candidates:
            if not path.is_file():
                errors.append(f"{path.name}: missing")
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                errors.append(f"{path.name}:{exc.lineno}: {exc.msg}")
        if errors:
            return False, "; ".join(errors[:10])
        return True, f"{len(candidates)} file(s) parsed"

    def _next_version_no(self, artifact_kind: str, artifact_id: str) -> int:
        current = self.session.execute(
            select(ArtifactVersion.version_no)
            .where(
                ArtifactVersion.artifact_kind == artifact_kind,
                ArtifactVersion.artifact_id == artifact_id,
            )
            .order_by(ArtifactVersion.version_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        return (current or 0) + 1

    def _row(self, version_id: str) -> ArtifactVersion:
        row = self.session.get(ArtifactVersion, version_id)
        if row is None:
            raise NotFound(f"artifact_version_not_found: {version_id}")
        return row

    def _require_visible(self, actor: Actor, row: ArtifactVersion) -> None:
        caller = actor.owner_id or actor.service_id
        if not caller or row.owner_id != caller:
            # 不确认存在性（沿用 hitl/team_approval 的归属隔离纪律）。
            raise NotFound(f"artifact_version_not_found: {row.id}")

    def _transition(
        self, actor: Actor, row: ArtifactVersion, action: str, *,
        values: dict[str, Any] | None = None, note: str = "",
    ) -> dict[str, Any]:
        """执行一次状态流转。

        用带状态守卫的**条件 UPDATE**：``WHERE id=? AND state=<当前状态>``。
        前置检查挡得住顺序重复，**挡不住并发**——两个请求都读到 ``draft``、
        都通过校验，然后都去写。单赢家语义完全依赖这个守卫，
        ``rowcount == 0`` 即冲突。
        """
        target = GATE_TRANSITIONS[row.state][action]
        now = utcnow()
        payload: dict[str, Any] = {
            "state": target,
            "updated_at": now,
            "version": ArtifactVersion.version + 1,
        }
        if values:
            payload.update(values)
        # draft 是唯一没有「判定时刻」的状态（ck_agv_decided_shape）。
        if target != "draft":
            payload["decided_at"] = now
            payload["decided_by"] = actor.owner_id or actor.service_id
        if note:
            payload["decision_note"] = note

        res = self.session.execute(
            update(ArtifactVersion)
            .where(
                ArtifactVersion.id == row.id,
                ArtifactVersion.state == row.state,
            )
            .values(**payload)
        )
        if res.rowcount == 0:
            raise Conflict(
                "transition_conflict",
                f"Artifact version {row.id} changed state concurrently "
                f"(expected {row.state!r})",
            )
        self.session.flush()
        self._audit(
            actor, f"artifact_gate.{action}", row.id,
            {"from": row.state, "to": target, "note": note or ""},
        )
        return self._view(self._row(row.id))

    def _audit(
        self, actor: Actor, action: str, target: str, details: dict | None
    ) -> None:
        if self.audit is not None:
            self.audit.append(actor, action, target, details or {})

    def _view(self, row: ArtifactVersion) -> dict[str, Any]:
        required = required_checks_for(row.artifact_kind)
        return {
            "id": row.id,
            "owner_id": row.owner_id,
            "artifact_kind": row.artifact_kind,
            "artifact_id": row.artifact_id,
            "version_no": row.version_no,
            "content_digest": row.content_digest,
            "state": row.state,
            "terminal": row.state in TERMINAL_ARTIFACT_STATES,
            "usable": row.state in USABLE_ARTIFACT_STATES,
            "required_checks": list(required),
            "legal_actions": sorted(GATE_TRANSITIONS.get(row.state, {})),
            "workspace_ref": dict(row.workspace_ref or {}),
            "submitted_at": _iso(row.submitted_at),
            "decided_at": _iso(row.decided_at),
            "decided_by": row.decided_by,
            "decision_note": row.decision_note,
            "created_by": row.created_by,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "version": row.version,
        }

    @staticmethod
    def _check_view(row: ArtifactGateCheck) -> dict[str, Any]:
        return {
            "id": row.id,
            "artifact_version_id": row.artifact_version_id,
            "check_name": row.check_name,
            "status": row.status,
            "observed_digest": row.observed_digest,
            "evidence": dict(row.evidence or {}),
            "detail": row.detail,
            "ran_by": row.ran_by,
            "created_at": _iso(row.created_at),
            "version": row.version,
        }


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None
