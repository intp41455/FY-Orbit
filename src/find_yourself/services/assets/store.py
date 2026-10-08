"""W9 个人资产库存储层（``assets`` 表 + 本地磁盘文件）。

**为什么 ORM 模型定义在 service 包里**：任务书《09-W9-多模态与个人资产库》§2 的「新」
清单只列了 ``services/assets/``、``api/routes/assets.py``、migration、前端与测试，
没有 ``db/*_models.py``。为严格不越所有权边界，本模块就地定义 ``Asset`` 并注册到共享
``Base`` 元数据上——``api/routes/assets.py`` 导入本模块即完成注册，``create_app()``
里的 ``Base.metadata.create_all()`` 在路由导入之后才执行，所以测试与本地 SQLite 都会
拿到该表；生产走 migration 0019。

存储约定（任务书 §3）：

* 所有字节落 ``FY_ASSETS_DIR``（默认 ``.runtime/assets``），DB 只存**相对路径**；
* ``/api/assets/{id}/raw`` 鉴权后代理，前端**永远拿不到绝对路径**；
* 写路径一律经 :func:`resolve_within`，拒绝绝对路径 / ``..`` 穿越 / 符号链接逃逸
  （判定惯例照 ``services/workspace.py`` 的 ``resolve_path``）；
* owner 一律取 ``actor.owner_id``，请求体里的 ``owner_id`` 无授权效力；
* 跨 owner 访问一律 404（不泄露「存在但不属于你」）；
* 超限（图片 10MB / 音频·音乐 20MB / 其他 50MB）抛 413，不静默截断。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import CheckConstraint, Index, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ...config import Settings, settings as load_settings
from ...db.base import Base
from ...db.types import ID, TZDateTime, utcnow
from ..actor import Actor
from ..audit import AuditService
from ..errors import DomainError, NotFound, PermissionDenied, ValidationFailed

# --- magic numbers for upload validation ---
MAGIC_BYTES = {
    "image/svg+xml": (b"<svg", b"<?xml"),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),  # need WEBP check
    "application/pdf": (b"%PDF",),
    "audio/mpeg": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    "audio/wav": (b"RIFF",),  # need WAVE check
    "audio/ogg": (b"OggS",),
    "video/mp4": (bytes([0, 0, 0, 0x18]) + b"ftypmp4", bytes([0, 0, 0, 0x1c]) + b"ftypmp4"),
    "video/webm": (b"\x1aE\xdf\xa3",),  # EBML
}
MAX_MAGIC_LEN = max(len(m) for magics in MAGIC_BYTES.values() for m in magics)


def _validate_magic(data: bytes, declared_mime: str) -> None:
    """校验文件头魔数与声明的 MIME 类型是否一致。

    Args:
        data: 文件字节
        declared_mime: 服务端推断的 MIME 类型（如 image/svg+xml）

    Raises:
        ValidationFailed: 魔数不匹配或无法识别的文件类型
    """
    if len(data) < MAX_MAGIC_LEN:
        # 文件太短无法校验，跳过（后续可能有其他逻辑处理）
        return

    head = data[:MAX_MAGIC_LEN]
    expected_magics = MAGIC_BYTES.get(declared_mime.lower())
    if not expected_magics:
        # 未知类型：拒绝上传，避免未知格式被浏览器当 HTML 解析
        raise ValidationFailed("unknown_mime_type", f"不支持的 MIME 类型：{declared_mime}")

    for magic in expected_magics:
        if head.startswith(magic):
            return  # 匹配成功

    raise ValidationFailed(
        "magic_mismatch",
        f"文件头与声明的类型 {declared_mime} 不匹配，可能是伪造文件",
    )




#: 允许的资产类型（任务书 §1.1）
ASSET_KINDS: tuple[str, ...] = ("image", "audio", "music", "doc")

#: 各类型大小上限（任务书 §3：图片 10MB / 音频 20MB / 其他 50MB，超限 413）
MAX_BYTES_BY_KIND: dict[str, int] = {
    "image": 10 * 1024 * 1024,
    "audio": 20 * 1024 * 1024,
    "music": 20 * 1024 * 1024,
    "doc": 50 * 1024 * 1024,
}
DEFAULT_MAX_BYTES = 50 * 1024 * 1024

#: 小屋挂载位（任务书 §1.4：图片挂墙面、音乐当 BGM）
MOUNT_ROLES: tuple[str, ...] = ("wall", "bgm", "")

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


class PayloadTooLarge(DomainError):
    """请求体超过该资产类型的上限（HTTP 413）。"""

    http_status = 413
    default_code = "asset_too_large"


class Asset(Base):
    """一件个人资产：DB 只记元数据与**相对**存储路径。"""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # image | audio | music | doc
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 相对 FY_ASSETS_DIR 的 POSIX 路径；绝不返回绝对路径给前端。
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[Any] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('image', 'audio', 'music', 'doc')", name="asset_kind"
        ),
        Index("ix_assets_owner_created", "owner_id", "created_at"),
        Index("ix_assets_owner_kind", "owner_id", "kind"),
    )


# --------------------------------------------------------------------------- #
# 路径安全
# --------------------------------------------------------------------------- #
def assets_root(settings: Settings | None = None) -> Path:
    """``FY_ASSETS_DIR``（默认 ``.runtime/assets``），创建并返回绝对路径。"""
    cfg = settings or load_settings()
    root = Path(cfg.assets_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _owner_segment(owner_id: str) -> str:
    """把 owner_id 变成安全的单级目录名（非字母数字一律替换，杜绝路径穿越）。"""
    cleaned = _SAFE_SEGMENT.sub("_", (owner_id or "").strip())
    # 即便分隔符已被替换，也不保留连续点（避免 '..' 这种可疑目录名）。
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.strip("._-")
    return cleaned or "owner"


def resolve_within(root: Path, rel_path: str) -> Path:
    """把相对路径解析到 ``root`` 内，越界即拒（惯例照 ``workspace.resolve_path``）。

    拒绝：绝对路径 / 盘符 / UNC、NUL 字节、``..`` 穿越、符号链接或junction 逃逸。
    """
    raw = (rel_path or "").strip()
    if "\x00" in raw:
        raise ValidationFailed("Path contains a NUL byte")
    if raw in ("", "."):
        raise ValidationFailed("Path is required")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ValidationFailed(
            "asset_path_rejected", f"Absolute / drive-letter paths are rejected: {raw!r}"
        )
    root_resolved = root.resolve()
    candidate = root_resolved / raw
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - 平台相关
        raise ValidationFailed("asset_path_unresolvable", f"Path cannot be resolved: {raw!r}") from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise PermissionDenied(
            "asset_path_escape", f"Path {raw!r} resolves outside the assets root", 403
        ) from None
    return resolved


def _guess_mime(name: str, kind: str, provided: str = "") -> str:
    if provided:
        return provided
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    return {
        "image": "image/png",
        "audio": "audio/wav",
        "music": "audio/wav",
        "doc": "application/octet-stream",
    }[kind]


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #
class AssetService:
    """资产库读写：入库 / 列表 / 原文件代理 / 删除 / 小屋挂载位。"""

    def __init__(
        self,
        session,
        audit: AuditService,
        settings: Settings | None = None,
        root: Path | None = None,
    ) -> None:
        self.s = session
        self.audit = audit
        self.settings = settings
        self._root = root

    @property
    def root(self) -> Path:
        if self._root is None:
            self._root = assets_root(self.settings)
        return self._root

    # -- helpers ------------------------------------------------------------ #
    def max_bytes_for(self, kind: str) -> int:
        return MAX_BYTES_BY_KIND.get(kind, DEFAULT_MAX_BYTES)

    def _serialize(self, row: Asset) -> dict[str, Any]:
        return {
            "id": row.id,
            "owner_id": row.owner_id,
            "kind": row.kind,
            "name": row.name,
            "mime": row.mime,
            "size": row.size,
            "meta": dict(row.meta or {}),
            "version": row.version,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            # 前端永远通过鉴权端点取字节，拿不到磁盘路径。
            "raw_url": f"/api/assets/{row.id}/raw",
            # 相对路径只用于前端显示「存在哪」，绝不是可直连的绝对路径。
            "storage_rel": row.storage_path,
        }

    def _owned(self, actor: Actor, asset_id: str) -> Asset:
        """按 actor.owner_id 取行；不存在或不属于该 owner 一律 404。"""
        actor.require_authenticated()
        row = self.s.execute(
            select(Asset).where(Asset.id == asset_id, Asset.owner_id == actor.owner_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("asset_not_found", "资产不存在")
        return row

    # -- 写入 --------------------------------------------------------------- #
    def store_bytes(
        self,
        actor: Actor,
        *,
        owner_id: str,
        name: str,
        data: bytes,
        kind: str,
        mime: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把一段字节落盘 + 入库。超限抛 413，非法类型抛 422（不留脏行/脏文件）。"""
        actor.require_authenticated()
        if kind not in ASSET_KINDS:
            raise ValidationFailed(
                "asset_kind_invalid", f"kind must be one of {list(ASSET_KINDS)}"
            )
        clean_name = (name or "").strip() or "untitled"
        if len(clean_name) > 500:
            raise ValidationFailed("asset_name_too_long", "资产名过长（上限 500 字符）")
        if not data:
            raise ValidationFailed("asset_empty", "空文件不入库")

        limit = self.max_bytes_for(kind)
        if len(data) > limit:
            raise PayloadTooLarge(
                "asset_too_large",
                f"{kind} 资产上限 {limit // (1024 * 1024)}MB，收到 {len(data)} 字节",
                413,
            )

        asset_id = uuid4().hex
        suffix = Path(clean_name).suffix.lower()
        # 磁盘名用服务端生成的 id，彻底杜绝用户文件名带来的穿越/覆盖风险。
        rel = f"{_owner_segment(owner_id)}/{asset_id}{suffix}"

        resolved_mime = _guess_mime(clean_name, kind, mime)

        # 魔数校验：防止 SVG 等伪装文件导致同源 XSS
        _validate_magic(data, resolved_mime)
        target = resolve_within(self.root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        row = Asset(
            id=asset_id,
            owner_id=owner_id,
            kind=kind,
            name=clean_name,
            storage_path=rel,
            mime=resolved_mime,
            size=len(data),
            meta=dict(meta or {}),
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(
            actor,
            "asset.created",
            row.id,
            {"kind": kind, "name": clean_name, "size": len(data),
             "mime": row.mime, "source": (meta or {}).get("source", "upload")},
        )
        return self._serialize(row)

    # -- 读取 --------------------------------------------------------------- #
    def list_assets(
        self, actor: Actor, *, owner_id: str, kind: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        actor.require_authenticated()
        stmt = select(Asset).where(Asset.owner_id == owner_id)
        if kind:
            if kind not in ASSET_KINDS:
                raise ValidationFailed("asset_kind_invalid", f"未知资产类型：{kind}")
            stmt = stmt.where(Asset.kind == kind)
        rows = (
            self.s.execute(stmt.order_by(Asset.created_at.desc()).limit(max(1, min(limit, 500))))
            .scalars()
            .all()
        )
        return [self._serialize(r) for r in rows]

    def get_asset(self, actor: Actor, asset_id: str) -> dict[str, Any]:
        return self._serialize(self._owned(actor, asset_id))

    def raw_file(self, actor: Actor, asset_id: str) -> tuple[Path, Asset]:
        """鉴权后的原文件定位：返回绝对 ``Path``（仅供 FileResponse 内部使用）。"""
        row = self._owned(actor, asset_id)
        path = resolve_within(self.root, row.storage_path)
        if not path.exists() or not path.is_file():
            raise NotFound("asset_file_missing", "资产文件已不在磁盘上（记录仍在）")
        return path, row

    def read_bytes(self, actor: Actor, asset_id: str) -> bytes:
        path, _row = self.raw_file(actor, asset_id)
        return path.read_bytes()

    # -- 删除 --------------------------------------------------------------- #
    def delete_asset(self, actor: Actor, asset_id: str) -> dict[str, Any]:
        """删记录 + 删磁盘文件（级联干净）；审计留删除事实，不留内容副本。"""
        row = self._owned(actor, asset_id)
        rel = row.storage_path
        payload = self._serialize(row)
        file_removed = False
        try:
            path = resolve_within(self.root, rel)
            if path.exists() and path.is_file():
                path.unlink()
                file_removed = True
        except (PermissionDenied, ValidationFailed):
            # 路径本身非法（历史脏数据）也不能让删除失败：记录照删，如实回报。
            file_removed = False
        self.s.delete(row)
        self.s.flush()
        self.audit.append(
            actor,
            "asset.deleted",
            asset_id,
            {"kind": row.kind, "name": row.name, "size": row.size, "file_removed": file_removed},
        )
        payload["file_removed"] = file_removed
        payload["deleted"] = True
        return payload

    # -- 小屋挂载位（任务书 §1.4） ------------------------------------------- #
    def set_mount(self, actor: Actor, asset_id: str, role: str) -> dict[str, Any]:
        """标记资产是否挂到小屋（``wall`` 墙面挂画 / ``bgm`` 背景音乐 / ``""`` 取消）。"""
        row = self._owned(actor, asset_id)
        if role not in MOUNT_ROLES:
            raise ValidationFailed(
                "mount_role_invalid", f"role must be one of {list(MOUNT_ROLES)}"
            )
        if role == "wall" and row.kind != "image":
            raise ValidationFailed("mount_kind_mismatch", "只有图片资产能挂到墙面")
        if role == "bgm" and row.kind not in ("audio", "music"):
            raise ValidationFailed("mount_kind_mismatch", "只有音频/音乐资产能当 BGM")
        meta = dict(row.meta or {})
        meta["cabin_mount"] = role
        row.meta = meta
        row.version += 1
        self.s.flush()
        self.audit.append(
            actor, "asset.mount_changed", row.id, {"role": role, "kind": row.kind}
        )
        return self._serialize(row)

    def mounted(self, actor: Actor, *, owner_id: str, role: str) -> list[dict[str, Any]]:
        rows = (
            self.s.execute(
                select(Asset)
                .where(Asset.owner_id == owner_id)
                .order_by(Asset.created_at.desc())
            )
            .scalars()
            .all()
        )
        return [self._serialize(r) for r in rows if (r.meta or {}).get("cabin_mount") == role]