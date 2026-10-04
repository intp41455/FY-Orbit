"""W3 知识源适配器协议（对齐 knowledge-source-adapter-design-2026-10-03.md §1）。

契约要点：

* ``KnowledgeSource`` ABC：``health_check`` / ``list_sources`` / ``fetch_document``
  / ``search_metadata``，其中 ``search_metadata`` 无能力时抛
  :class:`UnsupportedCapability`（**不返回空列表冒充「没有结果」**）。
* ``SourceCapabilities`` 显式声明 searchable / full_text / incremental / retryable，
  由注册中心与前端「适配器管理卡」读取并如实展示。
* :func:`retry_call` 实现令牌桶外的退避重试：只对 ``retryable`` 且 429/5xx 的
  网络错误重试，指数退避 + 抖动，上限由调用方给出。

任何适配器都**不允许**返回构造出来的假条目：拿不到就抛 DomainError 或让
``health_check`` 报 ``available=False``。
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generator, Iterable

from ...errors import DomainError, ValidationFailed


class UnsupportedCapability(DomainError):
    """该源不提供此能力——调用方必须显式处理，绝不能当成「查不到」。"""

    http_status = 501
    default_code = "unsupported_capability"


@dataclass(frozen=True)
class SourceCapabilities:
    searchable: bool = False
    full_text: bool = True
    incremental: bool = False
    retryable: bool = True

    def as_dict(self) -> dict[str, bool]:
        return {
            "searchable": self.searchable,
            "full_text": self.full_text,
            "incremental": self.incremental,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class SourceRef:
    """A knowledge base / folder / collection inside a remote source."""

    source_id: str
    external_id: str
    name: str
    kind: str = "collection"
    updated_at: str | None = None


@dataclass(frozen=True)
class RawDocument:
    """One fetched document. ``text`` is the FULL extracted body (no preview cap)."""

    source_id: str
    external_id: str
    name: str
    text: str
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    should_retry: Callable[[Exception], bool] | None = None,
) -> Any:
    """Exponential backoff with jitter for retryable failures.

    ``should_retry`` defaults to "HTTP 429/5xx or transport error"; a
    :class:`ValidationFailed` (bad credentials, 4xx other than 429) is never
    retried — retrying a wrong API key is pointless noise.
    """
    rand = rng or random.Random(0)
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except ValidationFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if should_retry is not None and not should_retry(exc):
                raise
            if i == max(1, attempts) - 1:
                raise
            delay = min(base_delay * (2 ** i), max_delay)
            sleep(delay * (0.5 + rand.random() / 2))
    raise last if last else RuntimeError("unreachable")


class KnowledgeSource(ABC):
    """Read-only external knowledge source adapter."""

    source_id: str = "base"
    display_name: str = "knowledge source"
    capabilities: SourceCapabilities = SourceCapabilities()

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def health_check(self, *, probe: bool = False) -> dict[str, Any]:
        """Return ``{available, configured, latency_ms, degraded, detail}``; never raise."""

    @abstractmethod
    def list_sources(self) -> list[SourceRef]: ...

    @abstractmethod
    def fetch_document(self, ref: SourceRef) -> Generator[RawDocument, None, None]: ...

    def search_metadata(self, ref: SourceRef, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise UnsupportedCapability(
            "source_not_searchable",
            f"知识源 {self.source_id} 不支持服务端检索，只能拉取到本地索引后检索",
        )

    # -- shared helpers ------------------------------------------------------ #
    def require_configured(self) -> None:
        if not self.is_configured():
            raise ValidationFailed(
                f"{self.source_id}_not_configured",
                f"{self.display_name} 未接入：缺少 API Key 或 Base URL，请在知识库适配器卡片中填写",
            )

    def status(self, *, probe: bool = False) -> dict[str, Any]:
        info = self.health_check(probe=probe)
        info.setdefault("source_id", self.source_id)
        info.setdefault("display_name", self.display_name)
        info["capabilities"] = self.capabilities.as_dict()
        return info


def merge_refs(refs: Iterable[SourceRef]) -> list[SourceRef]:
    """Deduplicate by ``(source_id, external_id)`` keeping first occurrence."""
    seen: set[tuple[str, str]] = set()
    out: list[SourceRef] = []
    for ref in refs:
        key = (ref.source_id, ref.external_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def utc_stamp() -> str:
    from ...db.types import utcnow

    return utcnow().isoformat()


def as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None