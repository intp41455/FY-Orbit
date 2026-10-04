"""Personal-space butler agent (数码小屋专属管家) — real-model dialogue channel.

Wires the cabin frontend's ``setDialogueProvider`` seam (cabinConfig.ts) to a
real model via the governed :class:`ModelGateway`, following the same honesty
semantics as ``CompanionService._model_reply``:

* With a configured provider the line comes from the model and is returned as
  ``{"line": ..., "source": "model", "model": ...}``.
* Without one, :class:`ModelNotConfigured` is raised (HTTP 503 upstream). This
  service NEVER fabricates a line — falling back to the local pre-generated
  pool, labelled 「预生成台词池」, is the frontend's responsibility.

Privacy boundary: the butler does NOT read personal profile or memory data.
Only the non-sensitive scenario context supplied by the caller (user name /
pet name / house / background) may enter the prompt, and the prompt states
honestly that profile enrichment is deferred to a future RAG adapter.

Budget guard: a simple process-local rate limit (≤ 10 admitted calls per owner
per rolling minute) protects the model budget; over-limit calls raise a 429.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any

from ..runtime.gateway import ModelGateway, ModelNotConfigured
from .actor import Actor
from .errors import DomainError, ValidationFailed

# --- Rate limiting (process-local; multi-process deployments need a shared
# store — recorded as a known limitation, see docs/接线报告). ------------------
BUTLER_RATE_LIMIT = 10  # admitted dialogue calls per owner ...
BUTLER_RATE_WINDOW_SECONDS = 60.0  # ... per rolling window of this many seconds

_process_rate_store: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()


class ButlerRateLimited(DomainError):
    """Too many butler dialogue calls from one owner in the rate window (429)."""

    http_status = 429
    default_code = "butler_rate_limited"


# Speaker ids mirror the frontend cabinConfig.DialogueSpeaker union.
BUTLER_SPEAKERS = {"person", "pet"}

# Tone descriptions keyed by the frontend PERSONALITIES ids; unknown values
# fall back to a neutral tone rather than erroring (defensive by design).
PERSONALITY_TONE = {
    "lively": "活泼元气、爱用感叹号、充满活力",
    "cool": "高冷简短、惜字如金、淡淡的",
    "melancholy": "忧郁安静、带一点诗意与怀念",
    "chatty": "话痨、絮絮叨叨、信息量大且爱跑题",
}
_DEFAULT_TONE = PERSONALITY_TONE["lively"]

HOUSE_LABELS = {
    "villa": "别墅",
    "cabin": "小木屋",
    "cave": "山洞",
    "snowcave": "雪洞",
    "bunker": "地堡",
    "castle": "城堡",
}
BACKGROUND_LABELS = {
    "forest": "树林",
    "garden": "花园",
    "stream": "小溪旁",
    "field": "田野",
    "planet": "宇宙星球",
}

# Non-sensitive scenario keys the frontend may inject. Anything else is
# dropped — profile/memory data must never reach the butler prompt.
_CONTEXT_KEY_MAX = 24  # per-value truncation (chars)
_ALLOWED_CONTEXT_KEYS = {"user_name", "pet_name", "house", "background"}

SPEAKER_LABELS = {"person": "小人", "pet": "宠物"}


def check_butler_rate_limit(
    owner_key: str,
    *,
    store: dict[str, deque[float]] | None = None,
    limit: int = BUTLER_RATE_LIMIT,
    window_seconds: float = BUTLER_RATE_WINDOW_SECONDS,
) -> None:
    """Admit or reject one dialogue call for ``owner_key`` (rolling window).

    Admitted attempts are recorded immediately (whether or not the subsequent
    model call succeeds) so failures cannot be used to bypass the budget cap.
    """
    target = store if store is not None else _process_rate_store
    now = time.monotonic()
    with _rate_lock:
        # setdefault 同时兼容 defaultdict 与注入测试用的普通 dict
        hits = target.setdefault(owner_key, deque())
        cutoff = now - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            raise ButlerRateLimited(
                "butler_rate_limited",
                f"Butler dialogue is rate limited to {limit} calls per {int(window_seconds)}s per owner",
                429,
            )
        hits.append(now)


class ButlerService:
    """Speaks for the cabin inhabitants through the governed model gateway."""

    def __init__(
        self,
        *,
        settings: Any = None,
        budget: Any = None,
        model_gateway: ModelGateway | None = None,
        rate_store: dict[str, deque[float]] | None = None,
    ):
        self._settings = settings
        self._budget = budget
        self._model_gateway = model_gateway
        self._rate_store = rate_store

    # -- gateway plumbing (mirrors CompanionService) -------------------------
    def _gateway(self) -> ModelGateway:
        if self._model_gateway is not None:
            return self._model_gateway
        if self._settings is None:
            raise ModelNotConfigured(
                "No model provider is configured for butler dialogue. "
                "Set FY_MODEL_API_KEY and FY_MODEL_BASE_URL (OpenAI-compatible endpoint, "
                "e.g. DeepSeek or a local Ollama) to enable real lines."
            )
        self._model_gateway = ModelGateway(self._settings, self._budget)
        return self._model_gateway

    def _model_name(self) -> str:
        return getattr(self._settings, "model_name", None) or "gpt-4o-mini"

    def model_configured(self) -> bool:
        """True only when a real provider is reachable (drives GET /status)."""
        try:
            gateway = self._gateway()
        except ModelNotConfigured:
            return False
        return bool(gateway.configured or gateway.provider is not None)

    # -- prompt assembly ------------------------------------------------------
    def sanitize_context(self, context: dict | None) -> dict[str, str]:
        """Whitelist + truncate the non-sensitive scenario fields only."""
        clean: dict[str, str] = {}
        if not isinstance(context, dict):
            return clean
        for key in _ALLOWED_CONTEXT_KEYS:
            value = context.get(key)
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value:
                continue
            clean[key] = value[:_CONTEXT_KEY_MAX]
        return clean

    def build_dialogue_prompt(
        self,
        *,
        speaker: str,
        personality: str,
        context: dict | None = None,
    ) -> str:
        """Assemble the butler persona prompt for one cabin dialogue line."""
        ctx = self.sanitize_context(context)
        role = SPEAKER_LABELS.get(speaker, speaker)
        tone = PERSONALITY_TONE.get(personality, _DEFAULT_TONE)
        house = HOUSE_LABELS.get(ctx.get("house", ""), "")
        background = BACKGROUND_LABELS.get(ctx.get("background", ""), "")
        scene_parts = [p for p in (house, background) if p]

        lines = [
            "[场景设定]",
            "你是用户个人空间（数码小屋）里的专属管家。现在请为小屋里的角色生成一句台词。",
            f"角色：{role}。",
            f"环境：{'、'.join(scene_parts) if scene_parts else '未指定'}。",
            f"语气基调：{tone}。",
            "",
            "[硬性要求]",
            "1. 只输出这一句台词本身：不要引号、旁白、动作说明或任何解释。",
            "2. 台词不超过 30 个字，必须符合语气基调。",
            "3. 台词属于虚拟演绎内容，不得当作对用户的真实承诺或事实陈述。",
            (
                "4. 你目前没有接入用户的个人画像与私人记忆数据（画像注入留待 RAG 适配器），"
                "绝不得编造用户的姓名、经历、日程或任何隐私信息；可用的只有下方提供的非敏感场景信息。"
            ),
            "",
            "[非敏感场景信息]",
        ]
        if ctx.get("user_name"):
            lines.append(f"主人名字：{ctx['user_name']}")
        if ctx.get("pet_name"):
            lines.append(f"宠物名字：{ctx['pet_name']}")
        if not (ctx.get("user_name") or ctx.get("pet_name")):
            lines.append("（无）")
        lines += [
            "",
            f"[本次生成目标] 角色={speaker}；性格={personality}",
        ]
        return "\n".join(lines)

    # -- dialogue -------------------------------------------------------------
    def dialogue(
        self,
        actor: Actor,
        *,
        speaker: str,
        personality: str,
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Return one butler line as {line, source: "model", model}.

        Honest failure contract: unconfigured provider -> ModelNotConfigured
        (503); gateway/transport failure -> 503 model_unavailable; over-rate ->
        429. There is deliberately no server-side fallback line.
        """
        actor.require_authenticated()
        if speaker not in BUTLER_SPEAKERS:
            raise ValidationFailed(
                "butler_invalid_speaker", "speaker must be one of: person, pet"
            )

        owner_key = actor.owner_id or actor.service_id or "anonymous"
        check_butler_rate_limit(owner_key, store=self._rate_store)

        prompt = self.build_dialogue_prompt(
            speaker=speaker, personality=personality, context=context
        )
        gateway = self._gateway()
        try:
            result = gateway.complete(
                actor,
                task_id=f"butler:{owner_key}",
                model=self._model_name(),
                prompt=prompt,
                target_domain="personal",
                max_tokens=64,
            )
        except ModelNotConfigured:
            raise ModelNotConfigured(
                "No model provider is configured for butler dialogue. "
                "Set FY_MODEL_API_KEY and FY_MODEL_BASE_URL (OpenAI-compatible endpoint, "
                "e.g. DeepSeek or a local Ollama) to enable real lines."
            ) from None
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "model_unavailable", f"Model call failed: {type(exc).__name__}", 503
            ) from exc

        line = self._clean_line(result.text)
        if not line:
            raise DomainError("butler_empty_reply", "Model returned an empty butler line", 503)
        return {"line": line, "source": "model", "model": self._model_name()}

    @staticmethod
    def _clean_line(text: str) -> str:
        """First non-empty line, whitespace-collapsed, wrapping quotes removed."""
        for raw in (text or "").splitlines():
            line = " ".join(raw.split())
            if not line:
                continue
            if len(line) >= 2 and line[0] in "\"'“「『":
                closing = {"“": "”", "「": "」", "『": "』"}.get(line[0], line[0])
                if line.endswith(closing):
                    line = line[1:-1].strip()
            return line
        return ""
