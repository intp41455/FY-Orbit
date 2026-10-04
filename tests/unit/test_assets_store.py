"""W9 资产库存储层单测（service 层）。

覆盖任务书验收清单的后端存储部分：

* 字节入库 → 相对路径落盘 → ``raw`` 能原样取回（DB 里**没有**绝对路径）；
* 路径穿越 / 绝对路径 / NUL 字节被拒（惯例照 ``workspace.resolve_path``）；
* 大小上限：图片 10MB / 音频·音乐 20MB / 其他 50MB，超限 413 且**不写文件不建行**；
* 跨 owner 隔离：owner-2 拿不到 owner-1 的资产（404）与字节；
* 删除级联干净（磁盘文件 + DB 行 + 审计）；
* 小屋挂载位：类型校验（图片才能上墙、音频才能当 BGM）+ 取消挂载；
* 合成器确定性 + 峰值不爆音；图片字节嗅探。
"""

from __future__ import annotations

import struct
import wave
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.models import AuditEvent
from find_yourself.db.types import TZDateTime
from find_yourself.services.actor import Actor
from find_yourself.services.assets import (
    ASSET_KINDS,
    MAX_BYTES_BY_KIND,
    Asset,
    AssetService,
    PayloadTooLarge,
    mood_catalog,
    render_wav,
    resolve_within,
    sniff_image_mime,
)
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import NotFound, PermissionDenied, ValidationFailed

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=__import__("datetime").timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    target = tmp_path / "assets"
    target.mkdir()
    return target


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def svc(session_maker, root):
    session = session_maker()
    return AssetService(session, AuditService(session), root=root)


# --------------------------------------------------------------------------- #
# 入库 / 读回
# --------------------------------------------------------------------------- #
def test_store_bytes_writes_relative_path_and_no_absolute_leak(svc, session_maker, root):
    payload = svc.store_bytes(
        Actor.owner("owner"), owner_id="owner", name="壁纸.png", data=PNG, kind="image"
    )
    assert payload["kind"] == "image"
    assert payload["mime"] == "image/png"
    assert payload["size"] == len(PNG)
    assert payload["raw_url"] == f"/api/assets/{payload['id']}/raw"
    # 前端拿不到任何绝对路径字段
    assert not any("/" == str(v)[:1] and ":" in str(v) for v in payload.values())

    row = session_maker().execute(select(Asset)).scalars().one()
    assert not Path(row.storage_path).is_absolute()
    assert (root / row.storage_path).read_bytes() == PNG


def test_read_bytes_roundtrip(svc):
    created = svc.store_bytes(
        Actor.owner("owner"), owner_id="owner", name="a.wav", data=b"RIFFfake", kind="music"
    )
    assert svc.read_bytes(Actor.owner("owner"), created["id"]) == b"RIFFfake"


def test_invalid_kind_and_empty_payload_rejected(svc, session_maker):
    with pytest.raises(ValidationFailed):
        svc.store_bytes(Actor.owner("owner"), owner_id="o", name="x", data=b"a", kind="video")
    with pytest.raises(ValidationFailed):
        svc.store_bytes(Actor.owner("owner"), owner_id="o", name="x", data=b"", kind="image")
    assert session_maker().execute(select(Asset)).scalars().all() == []


@pytest.mark.parametrize("kind", ASSET_KINDS)
def test_size_limit_per_kind(kind, svc, session_maker, root):
    limit = MAX_BYTES_BY_KIND[kind]
    ok_payload = b"\x00" * 16
    svc.store_bytes(Actor.owner("owner"), owner_id="owner", name="ok", data=ok_payload, kind=kind)
    with pytest.raises(PayloadTooLarge) as err:
        svc.store_bytes(
            Actor.owner("owner"), owner_id="owner", name="big", data=b"\x00" * (limit + 1), kind=kind
        )
    assert err.value.http_status == 413
    # 超限不留脏行、不留脏文件
    assert len(session_maker().execute(select(Asset)).scalars().all()) == 1
    assert len(list((root / "owner").iterdir())) == 1


# --------------------------------------------------------------------------- #
# 路径安全
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    ["../escape.png", "/etc/passwd", "C:\\Windows\\evil", "owner/../../out.png", "a\x00b"],
)
def test_resolve_within_rejects_escapes(root, bad):
    with pytest.raises((PermissionDenied, ValidationFailed)):
        resolve_within(root, bad)


def test_resolve_within_accepts_nested_inside(root):
    resolved = resolve_within(root, "owner/sub/file.png")
    assert resolved == (root / "owner" / "sub" / "file.png").resolve()


def test_owner_id_is_sanitised_into_single_segment(svc, root):
    svc.store_bytes(
        Actor.owner("evil/../owner"), owner_id="evil/../owner", name="x.png", data=PNG, kind="image"
    )
    segments = list(root.iterdir())
    assert len(segments) == 1
    assert "/" not in segments[0].name and ".." not in segments[0].name


# --------------------------------------------------------------------------- #
# owner 隔离
# --------------------------------------------------------------------------- #
def test_cross_owner_cannot_read_or_fetch_bytes(svc):
    created = svc.store_bytes(
        Actor.owner("owner-1"), owner_id="owner-1", name="secret.png", data=PNG, kind="image"
    )
    with pytest.raises(NotFound):
        svc.get_asset(Actor.owner("owner-2"), created["id"])
    with pytest.raises(NotFound):
        svc.read_bytes(Actor.owner("owner-2"), created["id"])
    with pytest.raises(NotFound):
        svc.delete_asset(Actor.owner("owner-2"), created["id"])
    assert svc.list_assets(Actor.owner("owner-2"), owner_id="owner-2") == []
    assert len(svc.list_assets(Actor.owner("owner-1"), owner_id="owner-1")) == 1


def test_list_filters_by_kind(svc):
    svc.store_bytes(Actor.owner("o"), owner_id="o", name="a.png", data=PNG, kind="image")
    svc.store_bytes(Actor.owner("o"), owner_id="o", name="b.wav", data=b"RIFF", kind="music")
    assert len(svc.list_assets(Actor.owner("o"), owner_id="o", kind="image")) == 1
    assert len(svc.list_assets(Actor.owner("o"), owner_id="o", kind="music")) == 1
    with pytest.raises(ValidationFailed):
        svc.list_assets(Actor.owner("o"), owner_id="o", kind="video")


# --------------------------------------------------------------------------- #
# 删除级联
# --------------------------------------------------------------------------- #
def test_delete_removes_file_row_and_audits(svc, root):
    created = svc.store_bytes(
        Actor.owner("owner"), owner_id="owner", name="gone.png", data=PNG, kind="image"
    )
    row = svc.s.execute(select(Asset)).scalars().one()
    path = root / row.storage_path
    assert path.exists()

    result = svc.delete_asset(Actor.owner("owner"), created["id"])
    assert result["deleted"] is True and result["file_removed"] is True
    assert not path.exists()
    assert svc.s.execute(select(Asset)).scalars().all() == []
    actions = [e.action for e in svc.s.execute(select(AuditEvent)).scalars().all()]
    assert "asset.created" in actions and "asset.deleted" in actions


def test_missing_file_is_reported_not_faked(svc, root):
    created = svc.store_bytes(
        Actor.owner("owner"), owner_id="owner", name="x.png", data=PNG, kind="image"
    )
    for child in (root / "owner").iterdir():
        child.unlink()
    with pytest.raises(NotFound) as err:
        svc.raw_file(Actor.owner("owner"), created["id"])
    assert err.value.code == "asset_file_missing"


# --------------------------------------------------------------------------- #
# 小屋挂载位
# --------------------------------------------------------------------------- #
def test_mount_roles_validated(svc):
    image = svc.store_bytes(
        Actor.owner("owner"), owner_id="owner", name="p.png", data=PNG, kind="image"
    )
    music = svc.store_bytes(
        Actor.owner("owner"), owner_id="owner", name="m.wav", data=b"RIFF", kind="music"
    )
    assert svc.set_mount(Actor.owner("owner"), image["id"], "wall")["meta"]["cabin_mount"] == "wall"
    assert svc.set_mount(Actor.owner("owner"), music["id"], "bgm")["meta"]["cabin_mount"] == "bgm"
    with pytest.raises(ValidationFailed):
        svc.set_mount(Actor.owner("owner"), music["id"], "wall")
    with pytest.raises(ValidationFailed):
        svc.set_mount(Actor.owner("owner"), image["id"], "bgm")
    with pytest.raises(ValidationFailed):
        svc.set_mount(Actor.owner("owner"), image["id"], "ceiling")

    assert len(svc.mounted(Actor.owner("owner"), owner_id="owner", role="wall")) == 1
    assert len(svc.mounted(Actor.owner("owner"), owner_id="owner", role="bgm")) == 1
    assert svc.set_mount(Actor.owner("owner"), image["id"], "")["meta"]["cabin_mount"] == ""
    assert svc.mounted(Actor.owner("owner"), owner_id="owner", role="wall") == []


# --------------------------------------------------------------------------- #
# 芯片音乐合成器
# --------------------------------------------------------------------------- #
def test_synth_is_deterministic_and_produces_real_wav():
    first = render_wav(mood="calm", seconds=3, seed=7)
    second = render_wav(mood="calm", seconds=3, seed=7)
    assert first.wav == second.wav
    with wave.open(BytesIO(first.wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 44100
        assert abs(w.getnframes() - 44100 * 3) <= 2
        frames = w.readframes(w.getnframes())
    peak = max(abs(v) for v, in struct.iter_unpack("<h", frames))
    assert 0 < peak <= 32767 * 0.9  # 有真实信号且不爆音


def test_synth_duration_clamped_and_flagged():
    long = render_wav(mood="bright", seconds=600)
    assert long.meta["truncated"] is True
    assert long.meta["seconds"] <= 60
    with wave.open(BytesIO(long.wav), "rb") as w:
        assert w.getnframes() <= 44100 * 60 + 1


def test_synth_rejects_unknown_mood():
    with pytest.raises(ValidationFailed):
        render_wav(mood="metal", seconds=5)


def test_mood_catalog_lists_every_synthesizable_mood():
    from find_yourself.services.assets import MOOD_IDS

    catalog = mood_catalog()
    assert {m["id"] for m in catalog} == set(MOOD_IDS)
    # 目录里列出的每个情绪都必须真能合成（不做「列了但跑不通」的假入口）
    for mood in MOOD_IDS:
        assert render_wav(mood=mood, seconds=2).meta["provider"] == "local_synth"
    assert all(m["label"] and m["tempo_bpm"] > 0 for m in catalog)


# --------------------------------------------------------------------------- #
# 字节嗅探
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "data,expected",
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a...", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"not-an-image", ""),
    ],
)
def test_sniff_image_mime(data, expected):
    assert sniff_image_mime(data) == expected


def test_settings_expose_assets_dir(tmp_path):
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        assets_dir=str(tmp_path / "custom-assets"),
    )
    assert settings.assets_dir.endswith("custom-assets")