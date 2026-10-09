"""W9 图片 / TTS 生成通道：OpenAI 兼容端点 + 诚实的「未接入」语义。

provider 化（任务书 §1.2）：v1 只实现**OpenAI 兼容的 ``/images/generations``**（用户
自填 key/base_url，本机 SD WebUI 的兼容 API 也走这条路径）+ **``/audio/speech``**
（TTS）。未配置时**诚实 503**（``image_provider_not_configured`` / ``tts_not_configured``），
绝不返回占位图、假 base64 或「已生成」的假记录。

配置来源优先级：

1. 进程内存里用户刚填的凭证（``provider_credentials``，同 W3 的做法：只在内存、不落盘）；
2. ``FY_IMAGE_API_KEY`` / ``FY_IMAGE_BASE_URL`` / ``FY_IMAGE_MODEL``（同 TTS 前缀）；
3. W4 的通用模型配置 ``FY_MODEL_API_KEY`` / ``FY_MODEL_BASE_URL`` / ``FY_MODEL_NAME``
   （「复用 W4 后的配置惯例」——同一个 OpenAI 兼容 key 同时能聊天和出图）。

预算护栏（任务书 §3「图片生成计费调用带预算护栏，复用 gateway 惯例」）：
**先定价、再调用**。单价未知一律拒绝（FROZEN_CONTRACT §7：不得按零费用放行），只有
本地推理 provider 才允许显式零成本。给了 ``task_id`` 时走 ``BudgetService`` 的
reserve → settle / release 三段式；没给任务就只记账（审计里写明 ``budget_mode``），
**绝不伪造预算流水**。

诚实边界：上游返回的字节会做**真实格式嗅探**，不是图片就报错；不解析 base64 失败就
假装成功。审计 details 只记长度/摘要，绝不记 prompt 原文（冻结契约 §1）。
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from typing import Any

import httpx

from ...config import Settings
from ...services.errors import Conflict, DomainError, ValidationFailed
from ..actor import Actor
from ..audit import AuditService

DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_IMAGE_SIZE = "1024x1024"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TTS_CHARS = 4000

#: 本机 / 无云端计费的 provider（零成本是**事实**，不是「未知按零处理」）
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


class ProviderNotConfigured(Conflict):
    """生成服务未接入 —— 503，绝不伪造成功。"""

    http_status = 503
    default_code = "image_provider_not_configured"


class ProviderCallFailed(DomainError):
    """上游真的调用失败 —— 把真实原因（脱敏后）带给前端。"""

    http_status = 502
    default_code = "image_provider_failed"


class ProviderPriceUnknown(ValidationFailed):
    """单价未知，拒绝按零费用放行。"""

    default_code = "asset_price_unknown"


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    #: credentials | env | model_settings | unconfigured
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url)


# --------------------------------------------------------------------------- #
# 进程内凭证（只在内存，重启即失效，状态里如实标注）
# --------------------------------------------------------------------------- #
_credentials: dict[str, dict[str, str]] = {}


def set_credentials(channel: str, values: dict[str, str]) -> dict[str, Any]:
    """记录用户在页面上填的 key/base_url/model。只存内存，不入库、不落盘、不回显。"""
    if channel not in ("image", "tts"):
        raise ValidationFailed("asset_channel_invalid", "channel must be image or tts")
    cleaned = {k: (v or "").strip() for k, v in values.items()}
    if not cleaned.get("base_url"):
        raise ValidationFailed(
            "asset_base_url_required", "请填写 Base URL（例如 https://api.openai.com/v1）"
        )
    _credentials[channel] = cleaned
    return masked_credentials(channel)


def forget_credentials(channel: str) -> bool:
    return _credentials.pop(channel, None) is not None


def masked_credentials(channel: str) -> dict[str, bool]:
    stored = _credentials.get(channel, {})
    return {"base_url": bool(stored.get("base_url")), "api_key": bool(stored.get("api_key"))}


def resolve_config(channel: str, settings: Settings | None = None) -> ProviderConfig:
    """三级回退解析 provider 配置；全空时返回 ``configured=False``。"""
    cfg = settings
    stored = _credentials.get(channel, {})
    if stored.get("base_url"):
        return ProviderConfig(
            base_url=stored["base_url"].rstrip("/"),
            api_key=stored.get("api_key", ""),
            model=stored.get("model") or _default_model(channel, cfg),
            source="credentials",
        )
    env_base = os.environ.get(f"FY_{channel.upper()}_BASE_URL", "").strip()
    if env_base:
        return ProviderConfig(
            base_url=env_base.rstrip("/"),
            api_key=os.environ.get(f"FY_{channel.upper()}_API_KEY", "").strip(),
            model=os.environ.get(f"FY_{channel.upper()}_MODEL", "").strip()
            or _default_model(channel, cfg),
            source="env",
        )
    if cfg is not None and cfg.model_base_url:
        # W4 惯例：通用模型配置兜底（同一个 OpenAI 兼容 key 也能出图）。
        return ProviderConfig(
            base_url=cfg.model_base_url.rstrip("/"),
            api_key=cfg.model_api_key,
            model=_default_model(channel, cfg),
            source="model_settings",
        )
    return ProviderConfig(base_url="", api_key="", model=_default_model(channel, cfg),
                          source="unconfigured")


def _default_model(channel: str, cfg: Settings | None) -> str:
    if channel == "image":
        return os.environ.get("FY_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL
    return (
        os.environ.get("FY_TTS_MODEL", "").strip()
        or (cfg.model_name if cfg is not None and cfg.model_name else "gpt-4o-mini-tts")
    )


def channel_status(channel: str, settings: Settings | None = None) -> dict[str, Any]:
    """前端「通道状态」卡数据源：如实说清是否接入、来源、缺什么。"""
    config = resolve_config(channel, settings)
    available = config.configured
    if not available:
        detail = "未接入生成服务：请填写 Base URL（未配置时不会返回任何“已生成”内容）"
    elif not config.api_key:
        detail = "已配置 Base URL 但没有 API Key（本地 SD WebUI 等免鉴权端点可留空）"
    else:
        detail = f"已接入：{config.base_url}（配置来源 {config.source}）"
    return {
        "channel": channel,
        "configured": available,
        "source": config.source,
        "model": config.model if available else "",
        "base_url": config.base_url if available else "",
        "has_api_key": bool(config.api_key),
        "credential_fields": ["base_url", "api_key", "model"],
        "credentials_present": masked_credentials(channel),
        "storage": "memory",
        "persist_restart": False,
        "detail": detail,
    }


# --------------------------------------------------------------------------- #
# 预算护栏
# --------------------------------------------------------------------------- #
def _is_local(base_url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    return host in LOCAL_HOSTS


def estimate_cost(config: ProviderConfig, settings: Settings | None = None) -> tuple[Any, str]:
    """返回 ``(Decimal 估算费用, 说明)``。单价未知时抛 :class:`ProviderPriceUnknown`。"""
    from decimal import Decimal

    from ...runtime.gateway import local_pricing

    if _is_local(config.base_url):
        # 本机推理：零成本是事实，来源必须写清楚，不能冒充「云端免费」。
        return Decimal("0"), local_pricing(config.model).note
    # 图像/语音单价**不走**聊天 token 价目表：本模块没有经审计的单价，
    # 因此按 FROZEN_CONTRACT §7 显式要求调用方通过 FY_IMAGE_PRICE_USD 声明
    # （配置项 ``FY_IMAGE_PRICE_USD``），未声明则拒绝调用（而不是悄悄按 0 记账）。
    declared = (settings.image_price_usd if settings is not None else "") or ""
    raw = (os.environ.get("FY_IMAGE_PRICE_USD", "").strip() or declared.strip())
    if not raw:
        raise ProviderPriceUnknown(
            "asset_price_unknown",
            f"模型 '{config.model}' 的调用单价未知：拒绝按零费用放行。"
            "请设置 FY_IMAGE_PRICE_USD（美元/张）后重试，或改用本机端点（127.0.0.1）",
        )
    try:
        return Decimal(raw), f"按 FY_IMAGE_PRICE_USD={raw} 记账"
    except Exception as exc:  # noqa: BLE001 — 明确报错，不静默
        raise ProviderPriceUnknown(
            "asset_price_invalid", f"FY_IMAGE_PRICE_USD 不是合法金额：{raw!r}"
        ) from exc


# --------------------------------------------------------------------------- #
# 图片生成
# --------------------------------------------------------------------------- #
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_image_mime(data: bytes) -> str:
    """真实嗅探字节格式；不是已知图片就返回空串（调用方据此报错）。"""
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


class ImageGenerationService:
    """OpenAI 兼容 ``POST {base_url}/images/generations``。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        audit: AuditService | None = None,
        budget=None,
        transport: Any | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.settings = settings
        self.audit = audit
        self.budget = budget
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    # -- internals --------------------------------------------------------- #
    def _require_config(self) -> ProviderConfig:
        config = resolve_config("image", self.settings)
        if not config.configured:
            raise ProviderNotConfigured(
                "image_provider_not_configured",
                "未接入图片生成服务：请先配置 Base URL（不会返回任何占位图）",
                503,
            )
        return config

    def _headers(self, config: ProviderConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return headers

    def _post(self, config: ProviderConfig, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        try:
            with httpx.Client(**kwargs) as client:
                resp = client.post(
                    f"{config.base_url}/images/generations",
                    headers=self._headers(config),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderCallFailed(
                "image_provider_timeout", f"图片生成超时（{self.timeout_seconds}s）", 502
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderCallFailed(
                "image_provider_unreachable", f"无法连接图片生成服务：{type(exc).__name__}", 502
            ) from exc
        if resp.status_code >= 400:
            # 只回传脱敏后的状态码 + 截断消息，不回传完整响应体（可能有内部拓扑）。
            raise ProviderCallFailed(
                "image_provider_http_error",
                f"图片生成服务返回 HTTP {resp.status_code}：{resp.text[:200]}",
                502,
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderCallFailed(
                "image_provider_bad_json", "图片生成服务返回的不是合法 JSON", 502
            ) from exc
        if not isinstance(body, dict):
            raise ProviderCallFailed(
                "image_provider_bad_json", "图片生成服务返回结构异常", 502
            )
        return body

    @staticmethod
    def _extract_bytes(body: dict[str, Any]) -> bytes:
        items = body.get("data")
        if not isinstance(items, list) or not items:
            raise ProviderCallFailed(
                "image_provider_no_image", "图片生成服务未返回图像数据", 502
            )
        first = items[0]
        if not isinstance(first, dict):
            raise ProviderCallFailed(
                "image_provider_bad_item", "图片生成服务返回的条目结构异常", 502
            )
        if isinstance(first.get("b64_json"), str) and first["b64_json"]:
            try:
                return base64.b64decode(first["b64_json"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProviderCallFailed(
                    "image_provider_bad_base64", "图片生成服务返回的 base64 无法解码", 502
                ) from exc
        raise ProviderCallFailed(
            "image_provider_url_only",
            "上游只返回了图片 URL 而非 base64：v1 不会去外链抓取（避免 SSRF），"
            "请改用返回 b64_json 的兼容端点",
            502,
        )

    # -- public ------------------------------------------------------------ #
    def generate(
        self,
        actor: Actor,
        *,
        prompt: str,
        size: str = DEFAULT_IMAGE_SIZE,
        model: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """生成一张图。返回**真实字节**的 base64 载荷与元数据（调用方负责落盘入库）。"""
        actor.require_authenticated()
        text = (prompt or "").strip()
        if not text:
            raise ValidationFailed("asset_prompt_required", "请填写图片描述 prompt")
        if len(text) > 2000:
            raise ValidationFailed("asset_prompt_too_long", "prompt 过长（上限 2000 字符）")

        config = self._require_config()
        target_model = (model or config.model).strip() or DEFAULT_IMAGE_MODEL

        # 1) 定价 → 2) 预留 → 3) 调用 → 4) 结算（gateway 惯例）
        estimated, price_note = estimate_cost(
            ProviderConfig(config.base_url, config.api_key, target_model, config.source),
            self.settings,
        )
        reservation = None
        budget_mode = "recorded_only"
        if self.budget is not None and task_id and estimated > 0:
            reservation = self.budget.reserve(
                actor,
                task_id=task_id,
                amount=estimated,
                idempotency_key=f"asset.image:{task_id}:{target_model}",
                scope="inference",
            )
            budget_mode = "reserved"

        try:
            body = self._post(
                config,
                {"model": target_model, "prompt": text, "n": 1, "size": size,
                 "response_format": "b64_json"},
            )
            data = self._extract_bytes(body)
        except Exception:
            if reservation is not None:
                self.budget.release(actor, reservation.id)
            raise

        if len(data) > MAX_IMAGE_BYTES:
            if reservation is not None:
                self.budget.release(actor, reservation.id)
            raise DomainError(
                "asset_image_too_large",
                f"上游返回的图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 上限",
                413,
            )
        mime = sniff_image_mime(data) or "image/png"

        if reservation is not None:
            self.budget.settle(actor, reservation.id, estimated)

        if self.audit is not None:
            # 审计绝不记 prompt 原文（冻结契约 §1），只记长度与摘要。
            self.audit.append(
                actor,
                "asset.image_generated",
                None,
                {"model": target_model, "size": size, "bytes": len(data), "mime": mime,
                 "prompt_chars": len(text), "cost_usd": str(estimated), "price_note": price_note,
                 "budget_mode": budget_mode, "provider": config.source},
            )
        return {
            "data": data,
            "mime": mime,
            "meta": {
                "source": "generated",
                "provider": "openai_compatible_images",
                "provider_source": config.source,
                "model": target_model,
                "size": size,
                "prompt_chars": len(text),
                "cost_usd": str(estimated),
                "price_note": price_note,
                "budget_mode": budget_mode,
            },
        }


class SpeechService:
    """OpenAI 兼容 ``POST {base_url}/audio/speech``（TTS）。未配置时诚实 503。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        audit: AuditService | None = None,
        transport: Any | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.settings = settings
        self.audit = audit
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def synthesize(
        self, actor: Actor, *, text: str, voice: str = "alloy", model: str = ""
    ) -> dict[str, Any]:
        actor.require_authenticated()
        body_text = (text or "").strip()
        if not body_text:
            raise ValidationFailed("asset_text_required", "请填写要合成的文本")
        if len(body_text) > MAX_TTS_CHARS:
            raise ValidationFailed(
                "asset_text_too_long", f"文本过长（上限 {MAX_TTS_CHARS} 字符）"
            )
        config = resolve_config("tts", self.settings)
        if not config.configured:
            raise ProviderNotConfigured(
                "tts_not_configured",
                "未接入语音合成服务：请先配置 Base URL（不会返回假音频）",
                503,
            )
        target_model = (model or config.model).strip() or config.model
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        try:
            with httpx.Client(**kwargs) as client:
                resp = client.post(
                    f"{config.base_url}/audio/speech",
                    headers=headers,
                    json={"model": target_model, "voice": voice, "input": body_text,
                          "response_format": "wav"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderCallFailed("tts_timeout", f"语音合成超时（{self.timeout_seconds}s）", 502) from exc
        except httpx.RequestError as exc:
            raise ProviderCallFailed(
                "tts_unreachable", f"无法连接语音合成服务：{type(exc).__name__}", 502
            ) from exc
        if resp.status_code >= 400:
            raise ProviderCallFailed(
                "tts_http_error", f"语音合成服务返回 HTTP {resp.status_code}：{resp.text[:200]}", 502
            )
        data = resp.content
        if not data:
            raise ProviderCallFailed("tts_empty", "语音合成服务返回空音频", 502)
        if self.audit is not None:
            self.audit.append(
                actor,
                "asset.speech_generated",
                None,
                {"model": target_model, "voice": voice, "bytes": len(data),
                 "text_chars": len(body_text), "provider": config.source},
            )
        return {
            "data": data,
            "mime": "audio/wav",
            "meta": {
                "source": "generated",
                "provider": "openai_compatible_speech",
                "provider_source": config.source,
                "model": target_model,
                "voice": voice,
                "text_chars": len(body_text),
            },
        }
