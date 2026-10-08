"""P3 · 备轨知识源：本地文件目录（``local_files``）。

选型理由（工单授权自定）： ima 是**云订阅源**、百度网盘 v1 是骨架——双轨
之外需要一条**可离线自测、不依赖任何外部服务**的备轨。本地文件目录源
读工作机上用户显式授权的目录中的 ``.md/.markdown/.txt/.html`` 文件，
可完全离线运行，是最诚实的第三源。

诚实性边界（与 ima/baidu_pan 同一铁律）：

* 根目录未配置或不存在 → ``health_check.available=False`` 并说明原因；
  ``require_configured`` 抛 ``ValidationFailed``——绝不返回空列表冒充「没内容」。
* ``fetch_document`` 里的文件消失 / 越界路径 / 非文本 → 显式失败，
  绝不构造假条目。
* ``search_metadata`` 继承基类 → ``UnsupportedCapability``（本地源不提供
  服务端检索；检索应发生在拉取入库后的本地 FTS 上）。
* 路径安全：``external_id`` 是根目录内的相对 posix 路径，解析后必须仍位于
  根目录之下（拒绝 ``..`` 与绝对路径穿越）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

from ...errors import ValidationFailed
from .base import KnowledgeSource, RawDocument, SourceCapabilities, SourceRef

#: 允许抓取的扩展名 = ingest 管线的文本段（``SUPPORTED_EXTENSIONS`` 里的
#: 纯文本子集）。**刻意不含 .html/.htm**：ingest 白名单里没有它们，列出来
#: 只会「能列出却导入必失败」——那是不诚实的状态。pdf/docx 走导入管线而非本源。
TEXT_EXTENSIONS: tuple[str, ...] = (".md", ".markdown", ".txt")
#: 单文件读取上限（对齐 ingest 管线的 20MB）
MAX_FILE_BYTES = 20 * 1024 * 1024


class LocalFilesSource(KnowledgeSource):
    source_id = "local_files"
    display_name = "本地文件知识库"
    capabilities = SourceCapabilities(
        searchable=False, full_text=True, incremental=False, retryable=False
    )

    def __init__(self, *, root_dir: str = ""):
        self.root_dir = (root_dir or "").strip()

    # -- config / health ----------------------------------------------------- #
    def is_configured(self) -> bool:
        return bool(self.root_dir) and os.path.isdir(self.root_dir)

    def health_check(self, *, probe: bool = False) -> dict[str, Any]:
        if not self.root_dir:
            return {
                "available": False,
                "configured": False,
                "degraded": False,
                "latency_ms": None,
                "detail": "未接入：请配置本地知识库根目录（凭证字段 root_dir 或环境变量 FY_LOCAL_KB_ROOT）",
            }
        if not os.path.isdir(self.root_dir):
            return {
                "available": False,
                "configured": True,
                "degraded": True,
                "latency_ms": None,
                "detail": f"配置的根目录不存在或不是目录：{self.root_dir}",
            }
        return {
            "available": True,
            "configured": True,
            "degraded": False,
            "latency_ms": None,
            "detail": f"本地目录可用：{self.root_dir}",
        }

    # -- helpers ------------------------------------------------------------- #
    def _root(self) -> Path:
        if not self.root_dir or not os.path.isdir(self.root_dir):
            raise ValidationFailed(
                "local_files_not_configured",
                f"{self.display_name} 根目录未配置或不存在：{self.root_dir!r}",
            )
        return Path(self.root_dir).resolve()

    def _resolve(self, root: Path, external_id: str) -> Path:
        """把 external_id（相对 posix 路径）安全解析到根目录之下。

        任何 ``..`` / 绝对路径 / 盘符穿越都会被解析后拒收——本源读的是
        用户显式授权的目录，绝不越界。
        """
        raw = (external_id or "").strip()
        if not raw:
            raise ValidationFailed("local_files_bad_ref", "external_id 不能为空")
        # external_id 约定为 posix 相对路径。必须**先**把反斜杠视为分隔符做穿越
        # 检测：在 Linux 上 Path("..\\..\\x").parts 只有一段，反斜杠不参与分词，
        # 于是 "..\\..\\x" 会被当成普通文件名放行（漏检）；反之 Windows 上
        # "a/../../b" 的 "/" 也不参与分词。故先归一化再判定。
        normalized = raw.replace("\\", "/")
        candidate = Path(normalized)
        if (
            candidate.is_absolute()
            or candidate.drive
            or Path(raw).drive
            or ".." in normalized.split("/")
        ):
            raise ValidationFailed(
                "local_files_bad_ref",
                f"external_id 必须是根目录内的相对路径：{raw!r}",
            )
        resolved = (root / candidate).resolve()
        if root != resolved and root not in resolved.parents:
            raise ValidationFailed(
                "local_files_bad_ref",
                f"external_id 解析后越出根目录：{raw!r}",
            )
        return resolved

    # -- protocol ------------------------------------------------------------ #
    def list_sources(self) -> list[SourceRef]:
        root = self._root()
        refs: list[SourceRef] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue  # 超限文件不列出，交由导入管线路径处理（诚实：不冒充可拉取）
            rel = path.relative_to(root).as_posix()
            refs.append(
                SourceRef(
                    source_id=self.source_id,
                    external_id=rel,
                    name=path.name,
                    kind="file",
                )
            )
        return refs

    def fetch_document(self, ref: SourceRef) -> Iterator[RawDocument]:
        root = self._root()
        path = self._resolve(root, ref.external_id)
        if not path.is_file():
            raise ValidationFailed(
                "local_files_document_missing",
                f"文件不存在或已被移走：{ref.external_id}",
            )
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValidationFailed(
                "local_files_document_too_large",
                f"文件超过 {MAX_FILE_BYTES} 字节上限：{ref.external_id}（{size} 字节）",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailed(
                "local_files_document_not_text",
                f"文件不是 UTF-8 文本：{ref.external_id}（{exc}）",
            ) from exc
        except OSError as exc:
            raise ValidationFailed(
                "local_files_document_unreadable",
                f"文件不可读：{ref.external_id}（{exc}）",
            ) from exc
        yield RawDocument(
            source_id=self.source_id,
            external_id=ref.external_id,
            name=path.name,
            text=text,
            metadata={"path": ref.external_id, "bytes": size},
        )
