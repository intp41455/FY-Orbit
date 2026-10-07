"""插件市场后端（需求 14 后半 · P5）。

市场**不拥有**任何门禁或授权逻辑——它只是既有安全组件的编排面：

* **上架**：:meth:`MarketplaceService.publish` 直接委托
  :meth:`~find_yourself.services.skill.SkillService.promote`——服务端
  ``PROMOTION_GATE_POLICY`` 门禁（plugin 包必须签名已校验 + 扫描已通过）
  原样生效，市场**不可能**绕过它（本模块没有第二个写 ``state`` 的路径）。
* **检索**：只查 ``state == 'active'`` 的包。**未过门禁的包在服务端就查不到**
  （SQL WHERE 过滤，不是前端隐藏）；分页默认有界、超界显式拒绝。
* **安装**：复用 :class:`~find_yourself.services.grant.GrantService` 的授权
  数据面——含可执行文件条目的 ``plugin`` 包安装时必须出示一条**存在且
  active、未过期**的授权（由 ``GrantService.create`` 创建；运行期数据访问
  继续走 ``GrantService.is_authorized``）。本服务不新写任何授权语义。

诚实性：卡片上的 ``risk_level`` / 扫描摘要 / 签名结论全部来自服务端落库的
``scan_report`` 与签名列——市场不发明、不美化任何风险信息。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Grant, Skill
from .actor import Actor
from .audit import AuditService
from .errors import NotFound, ValidationFailed
from .grant import GrantService
from .skill import SkillService
from .marketplace_rating import MarketplaceRatingStore, get_rating_store

#: 分页默认值与上界（「默认有界、超界被拒」的门禁要求）。
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

#: 检索可用的能力轴（由服务端从包结构派生，不是包自声明）。
CAPABILITY_AXES = ("materialize:files", "instruction:inline")
SORT_AXES = ("score", "rating", "reviews", "created", "name")


class MarketplaceService:
    def __init__(self, session: Session, audit: AuditService,
                 skills: SkillService, grants: GrantService,
                 ratings: MarketplaceRatingStore | None = None):
        self.s = session
        self.audit = audit
        self.skills = skills
        self.grants = grants
        self.ratings = ratings or get_rating_store()

    # ------------------------------------------------------------------
    # 上架（复用 promote 门禁，绝不绕过）
    # ------------------------------------------------------------------
    def publish(self, actor: Actor, skill_id: str, evaluation_id: str) -> Skill:
        """上架 = 走既有 ``SkillService.promote`` 门禁。

        门禁判定（扫描/签名策略）在 ``promote`` 内按服务端策略执行，调用方
        无法传参削弱；这里没有第二条把包置为 ``active`` 的路径。
        """
        return self.skills.promote(actor, skill_id, evaluation_id)

    def publish_gate_status(self, actor: Actor, skill_id: str) -> dict[str, Any]:
        """只读报告门禁判定（上架前预检「为什么被拒」）。"""
        return self.skills.promotion_gate_status(skill_id)

    # ------------------------------------------------------------------
    # 检索（服务端过滤 + 有界分页）
    # ------------------------------------------------------------------
    def _active_query(self):
        # 服务端过滤的唯一真源：未过门禁（state != 'active'）的包根本不进查询。
        # 默认排除内置包（source == 'builtin'），使内置系统包不污染用户插件市场视图。
        return self.s.execute(
            select(Skill)
            .where(Skill.state == "active", Skill.source != "builtin")
            .order_by(Skill.created_at.asc())
        ).scalars().all()


    @staticmethod
    def _capabilities_of(skill: Skill) -> list[str]:
        """从**服务端落库的结构事实**派生能力面（不是包的自声明文案）。"""
        if skill.gate_profile == "plugin":
            return ["materialize:files"]
        return ["instruction:inline"]

    @staticmethod
    def _risk_of(skill: Skill) -> tuple[str, list[str]]:
        """确定性风险等级 + 理由（全部可追溯到落库事实）。"""
        reasons: list[str] = []
        scan_risk = str((skill.scan_report or {}).get("risk_level") or "none")
        if skill.gate_profile == "plugin":
            reasons.append("materializes_files")
            level = scan_risk if scan_risk in ("low", "medium", "high") else "medium"
        else:
            level = scan_risk if scan_risk in ("low", "medium", "high") else "low"
        if not skill.signature_verified:
            reasons.append("unsigned")
        return level, reasons

    def _card(self, skill: Skill, *, detail: bool = False) -> dict[str, Any]:
        level, reasons = self._risk_of(skill)
        report = skill.scan_report or {}
        scan_risk = str(report.get("risk_level") or "none")
        rating_summary = self.ratings.get_summary("plugin", skill.id)
        card: dict[str, Any] = {
            "skill_id": skill.id,
            "name": skill.name,
            "version": skill.semantic_version,
            "domain": skill.domain,
            "source": skill.source,
            "license": skill.license,
            "package_hash": skill.package_hash[:12],
            "gate_profile": skill.gate_profile,
            "signature_verified": bool(skill.signature_verified),
            "capabilities": self._capabilities_of(skill),
            "risk_level": level,
            "risk_reasons": reasons,
            "scan": {
                "passed": bool(skill.scan_passed),
                "risk_level": scan_risk,
                "finding_count": int(report.get("finding_count") or 0),
                "scanner_version": report.get("scanner_version"),
            },
            "rating": rating_summary,
        }
        if detail:
            card["scan"]["findings"] = list(report.get("findings") or [])
            card["ratings_distribution"] = rating_summary.get("distribution")
        return card

    def list_packages(
        self,
        actor: Actor,
        *,
        query: str = "",
        domain: str | None = None,
        capability: str | None = None,
        sort_by: str = "score",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """市场列表。服务端过滤 + 评分加权排序 + 有界分页；未过门禁的包不可见。"""
        actor.require_authenticated()
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValidationFailed("page_limit_invalid", "limit 必须是 >= 1 的整数")
        if limit > MAX_PAGE_LIMIT:
            raise ValidationFailed(
                "page_limit_exceeded",
                f"limit 超过上界 {MAX_PAGE_LIMIT}（默认 {DEFAULT_PAGE_LIMIT}）",
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationFailed("page_offset_invalid", "offset 必须是 >= 0 的整数")
        if sort_by not in SORT_AXES:
            raise ValidationFailed(
                "unknown_sort_axis",
                f"未知排序维度 {sort_by!r}；可选：{list(SORT_AXES)}",
            )

        rows = self._active_query()
        if capability is not None and capability not in CAPABILITY_AXES:
            raise ValidationFailed(
                "unknown_capability_axis",
                f"未知能力轴 {capability!r}；可选：{list(CAPABILITY_AXES)}",
            )

        needle = (query or "").strip().lower()
        items: list[dict[str, Any]] = []
        for skill in rows:
            if needle and needle not in skill.name.lower():
                continue
            if domain is not None and skill.domain != domain:
                continue
            if capability is not None and capability not in self._capabilities_of(skill):
                continue
            items.append(self._card(skill))

        # 排序：好用的自然被顶上来 (默认 score: 贝叶斯综合分降序)
        if sort_by == "score":
            items.sort(key=lambda x: (x["rating"]["score"], x["rating"]["rating_count"]), reverse=True)
        elif sort_by == "rating":
            items.sort(key=lambda x: (x["rating"]["average_rating"], x["rating"]["rating_count"]), reverse=True)
        elif sort_by == "reviews":
            items.sort(key=lambda x: x["rating"]["rating_count"], reverse=True)
        elif sort_by == "name":
            items.sort(key=lambda x: x["name"].lower())
        # sort_by == "created": 保持 _active_query 默认的 created_at 排序

        total = len(items)
        return {
            "items": items[offset:offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
        }

    def get_package(self, actor: Actor, skill_id: str) -> dict[str, Any]:
        """市场详情。未上架（state != 'active'）= 查无此包（NotFound，不伪装）。"""
        actor.require_authenticated()
        skill = self.s.get(Skill, skill_id)
        if skill is None or skill.state != "active" or skill.source == "builtin":
            raise NotFound("package_not_listed", f"包不在市场中：{skill_id}")
        card = self._card(skill, detail=True)
        actor_id = getattr(actor, "actor_id", None) or getattr(actor, "id", None) or "user"
        card["my_rating"] = self.ratings.get_user_rating("plugin", skill.id, actor_id)
        return card

    def rate_package(
        self,
        actor: Actor,
        skill_id: str,
        rating: float | int,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """为市场中的包评分（1.0 ~ 5.0）。需登录，记录审计日志。"""
        actor.require_authenticated()
        skill = self.s.get(Skill, skill_id)
        if skill is None or skill.state != "active" or skill.source == "builtin":
            raise NotFound("package_not_listed", f"包不在市场中：{skill_id}")

        actor_id = getattr(actor, "actor_id", None) or getattr(actor, "id", None) or "user"
        res = self.ratings.rate("plugin", skill_id, actor_id, rating, comment=comment)
        self.audit.append(actor, "marketplace.rated", skill_id, {
            "rating": rating,
            "comment": comment,
            "new_score": res["summary"]["score"],
        })
        return res

    def get_package_ratings(
        self,
        actor: Actor,
        skill_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """获取包的分页评价列表与摘要。"""
        actor.require_authenticated()
        skill = self.s.get(Skill, skill_id)
        if skill is None or skill.state != "active" or skill.source == "builtin":
            raise NotFound("package_not_listed", f"包不在市场中：{skill_id}")
        return self.ratings.get_ratings("plugin", skill_id, limit=limit, offset=offset)


    # ------------------------------------------------------------------
    # 安装（复用 GrantService 授权数据面）
    # ------------------------------------------------------------------
    def install(self, actor: Actor, skill_id: str, *, grant_id: str | None = None) -> dict[str, Any]:
        """安装一个市场内的包。

        * 仅主人可安装（与 promote 同一授权边界）。
        * ``plugin`` 画像的包（会物化文件执行）必须出示有效授权：
          ``grant_id`` 指向的授权必须**存在、active、未过期**（由
          ``GrantService.create`` 创建）。运行期对该包的数据访问继续由
          ``GrantService.is_authorized`` 逐记录判定——安装只解决「授权存在」。
        * ``instruction`` 包（惰性文本，不执行）无需授权即可安装。
        """
        actor.require_owner()
        skill = self.s.get(Skill, skill_id)
        if skill is None or skill.state != "active":
            raise NotFound("package_not_listed", f"包不在市场中：{skill_id}")

        grant_row: Grant | None = None
        if skill.gate_profile == "plugin":
            if not grant_id:
                raise ValidationFailed(
                    "install_grant_required",
                    "plugin 包安装需要出示授权（grant_id）：该包会物化并执行文件",
                )
            grant_row = self.s.get(Grant, grant_id)
            if grant_row is None:
                raise ValidationFailed("install_grant_not_found", f"授权不存在：{grant_id}")
            if grant_row.state != "active":
                raise ValidationFailed(
                    "install_grant_inactive", f"授权状态为 {grant_row.state!r}，不可用"
                )
            from ..db.types import utcnow

            if grant_row.expires_at <= utcnow():
                raise ValidationFailed("install_grant_expired", "授权已过期，不可用")

        self.s.flush()
        self.audit.append(actor, "marketplace.installed", skill.id, {
            "package_hash": skill.package_hash[:12],
            "gate_profile": skill.gate_profile,
            "grant_id": grant_id,
            "signature_verified": bool(skill.signature_verified),
        })
        card = self._card(skill, detail=True)
        card["installed"] = True
        card["grant_id"] = grant_id
        return card

    # ------------------------------------------------------------------
    def counts_for_tests(self) -> int:
        """（测试辅助）active 包总数。"""
        return self.s.execute(
            select(func.count()).select_from(Skill).where(Skill.state == "active")
        ).scalar_one()
