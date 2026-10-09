"""W9 个人资产库 API（``/api/assets``）。

鉴权与 owner 隔离（任务书 §1.1 + 冻结契约 §1/§5）：

* 读走 ``get_actor``，写走 ``csrf_protected``（CSRF + 同源 Origin）；
* owner 一律取 ``actor.owner_id``，请求体里的 ``owner_id`` **无授权效力**；
* 跨 owner 的资产一律 404（不泄露存在性）。

上传协议与 W3 一致：``POST /api/assets?name=&kind=`` + **原始字节请求体**
（``Content-Type: application/octet-stream``），避免引入 ``python-multipart``。
前端 ``web/src/api/assets.ts`` 用 ``fetch`` + ``credentials: same-origin`` +
``X-CSRF-Token`` 直接发字节。

``GET /api/assets/{id}/raw`` 是**唯一**的字节出口：鉴权后 FileResponse 代理，
带 ``Content-Disposition: inline`` + ``X-Content-Type-Options: nosniff``，
**绝不**把磁盘绝对路径交给前端。

超限一律 413（``asset_too_large``），非法类型/参数 422，路径穿越 403。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...config import Settings
from ...services.actor import Actor
from ...services.assets import (
    ASSET_KINDS,
    MAX_BYTES_BY_KIND,
    AssetService,
    ImageGenerationService,
    SpeechService,
    channel_status,
    forget_credentials,
    mood_catalog,
    render_wav,
    set_credentials,
)
from ...services.audit import AuditService
from ..deps import csrf_protected, get_actor, get_db, get_settings

router = APIRouter(prefix="/api/assets", tags=["assets"])

MAX_UPLOAD_BYTES = max(MAX_BYTES_BY_KIND.values())


def _assets(
    session: Session,
    audit: AuditService,
    settings: Settings,
    root=None,
) -> AssetService:
    return AssetService(session, audit, settings=settings, root=root)


def _svc(session: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> AssetService:
    return AssetService(session, AuditService(session), settings=settings)


class GenerateImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)
    size: str = Field(default="1024x1024", max_length=32)
    model: str = Field(default="", max_length=120)
    task_id: str = Field(default="", max_length=64)


class SynthesizeMusicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood: str = Field(min_length=1, max_length=32)
    seconds: float = Field(default=12.0, gt=0, le=300)
    name: str = Field(default="", max_length=200)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    voice: str = Field(default="alloy", max_length=64)
    model: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=200)


class ChannelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=400)
    api_key: str = Field(default="", max_length=400)
    model: str = Field(default="", max_length=120)


class MountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: "wall" 墙面挂画 / "bgm" 背景音乐 / "" 取消挂载
    role: str = Field(default="", max_length=16)


# --------------------------------------------------------------------------- #
# 列表 / 上传 / 原文件 / 删除
# --------------------------------------------------------------------------- #
@router.get("")
async def list_assets(
    kind: str = Query(default="", max_length=16),
    actor: Actor = Depends(get_actor),
    svc: AssetService = Depends(_svc),
) -> dict:
    items = svc.list_assets(actor, owner_id=actor.owner_id, kind=kind or None)
    return {
        "assets": items,
        "count": len(items),
        "kinds": list(ASSET_KINDS),
        "max_bytes_by_kind": dict(MAX_BYTES_BY_KIND),
    }


@router.post("")
async def upload_asset(
    request: Request,
    name: str = Query(min_length=1, max_length=500),
    kind: str = Query(min_length=1, max_length=16),
    actor: Actor = Depends(csrf_protected),
    svc: AssetService = Depends(_svc),
) -> dict:
    data = await request.body()
    # 不把请求的 Content-Type 当成 mime：裸字节上传永远是 application/octet-stream，
    # 真实类型由服务端按文件名 / 字节嗅探判定（不信任客户端声明）。
    asset = svc.store_bytes(
        actor, owner_id=actor.owner_id, name=name, data=data, kind=kind,
        meta={"source": "upload"},
    )
    svc.s.commit()
    return {"asset": asset}


@router.get("/channels")
async def list_channels(
    actor: Actor = Depends(get_actor),
    settings: Settings = Depends(get_settings),
) -> dict:
    """前端「通道状态」卡：如实报告图片/TTS 是否接入，未接入不返回假数据。"""
    actor.require_authenticated()
    return {
        "channels": [channel_status("image", settings), channel_status("tts", settings)],
        "music": {
            "channel": "music",
            "configured": True,
            "provider": "local_synth",
            "detail": "本地芯片音乐合成器：零外部依赖，未配置任何 key 也可真实产出 WAV",
        },
        "moods": mood_catalog(),
    }


@router.post("/channels/{channel}/configure")
async def configure_channel(
    channel: str,
    body: ChannelConfigRequest,
    actor: Actor = Depends(csrf_protected),
    settings: Settings = Depends(get_settings),
) -> dict:
    """凭证只存进程内存（重启即失效），返回值只报「哪些字段已填」，绝不回显。"""
    if channel not in ("image", "tts"):
        from ...services.errors import NotFound

        raise NotFound("asset_channel_not_found", f"未知通道：{channel}")
    set_credentials(channel, body.model_dump())
    return channel_status(channel, settings)


@router.delete("/channels/{channel}")
async def forget_channel(channel: str, actor: Actor = Depends(csrf_protected)) -> dict:
    if channel not in ("image", "tts"):
        from ...services.errors import NotFound

        raise NotFound("asset_channel_not_found", f"未知通道：{channel}")
    return {"channel": channel, "forgotten": forget_credentials(channel)}


@router.get("/{asset_id}/raw")
async def raw_asset(
    asset_id: str,
    actor: Actor = Depends(get_actor),
    svc: AssetService = Depends(_svc),
) -> FileResponse:
    path, row = svc.raw_file(actor, asset_id)
    return FileResponse(
        path,
        media_type=row.mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{row.id}{_suffix(row.name)}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def _suffix(name: str) -> str:
    from pathlib import Path

    suffix = Path(name).suffix
    return suffix if len(suffix) <= 12 else ""


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: AssetService = Depends(_svc),
) -> dict:
    result = svc.delete_asset(actor, asset_id)
    svc.s.commit()
    return result


@router.put("/{asset_id}/mount")
async def set_mount(
    asset_id: str,
    body: MountRequest,
    actor: Actor = Depends(csrf_protected),
    svc: AssetService = Depends(_svc),
) -> dict:
    asset = svc.set_mount(actor, asset_id, body.role)
    svc.s.commit()
    return {"asset": asset}


# --------------------------------------------------------------------------- #
# 生成通道
# --------------------------------------------------------------------------- #
@router.post("/generate/image")
async def generate_image(
    body: GenerateImageRequest,
    actor: Actor = Depends(csrf_protected),
    svc: AssetService = Depends(_svc),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = ImageGenerationService(settings=settings, audit=svc.audit, budget=None).generate(
        actor, prompt=body.prompt, size=body.size, model=body.model, task_id=body.task_id
    )
    meta = dict(result["meta"])
    meta.setdefault("provider", "openai_compatible_images")
    asset = svc.store_bytes(
        actor,
        owner_id=actor.owner_id,
        name=f"image-{body.prompt[:32].strip() or 'generated'}.png",
        data=result["data"],
        kind="image",
        mime=result["mime"],
        meta=meta,
    )
    svc.s.commit()
    return {"asset": asset}


@router.post("/generate/music")
async def generate_music(
    body: SynthesizeMusicRequest,
    actor: Actor = Depends(csrf_protected),
    svc: AssetService = Depends(_svc),
) -> dict:
    """程序化合成：未配置任何 key 也有**真实**WAV 产出（任务书 §1.3）。"""
    result = render_wav(mood=body.mood, seconds=body.seconds, seed=body.seed)
    meta = dict(result.meta)
    asset = svc.store_bytes(
        actor,
        owner_id=actor.owner_id,
        name=body.name.strip() or f"chiptune-{body.mood}.wav",
        data=result.wav,
        kind="music",
        mime="audio/wav",
        meta=meta,
    )
    svc.s.commit()
    return {"asset": asset}


@router.post("/generate/speech")
async def generate_speech(
    body: SpeakRequest,
    actor: Actor = Depends(csrf_protected),
    svc: AssetService = Depends(_svc),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = SpeechService(settings=settings, audit=svc.audit).synthesize(
        actor, text=body.text, voice=body.voice, model=body.model
    )
    meta = dict(result["meta"])
    asset = svc.store_bytes(
        actor,
        owner_id=actor.owner_id,
        name=body.name.strip() or "speech.wav",
        data=result["data"],
        kind="audio",
        mime=result["mime"],
        meta=meta,
    )
    svc.s.commit()
    return {"asset": asset}


@router.get("/cabin/mounted")
async def cabin_mounted(
    role: str = Query(default="", max_length=16),
    actor: Actor = Depends(get_actor),
    svc: AssetService = Depends(_svc),
) -> dict:
    """小屋消费面：取当前 owner 挂在墙面 / BGM 位上的资产（供 CabinPage 用）。"""
    if role not in ("wall", "bgm", ""):
        from ...services.errors import ValidationFailed

        raise ValidationFailed("mount_role_invalid", "role must be wall / bgm / 空")
    items = svc.mounted(actor, owner_id=actor.owner_id, role=role)
    return {"role": role, "assets": items, "count": len(items)}
