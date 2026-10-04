"""W3 文档导入管线：清洗 → 切片 → 入库（``kb_documents`` / ``kb_chunks``）。

纯 Python 解析：``.md/.txt`` 直接解码，``.pdf`` 用 ``pypdf``，``.docx`` 用
``python-docx``（依赖已在 pyproject 声明，走阿里云镜像安装）。**解析失败绝不
静默**：文档落库为 ``status='failed'`` 并带用户可见的 ``error``；扩展名不支持 /
超过 20MB / 空文件这类「请求本身就不合法」的情况直接抛 DomainError，不产生
脏文档行。

切片按「标题分段 → 段落合并 → 超限硬切」，chunk ≤ 800 字符、相邻块重叠 100
字符（重叠部分重新锚定到行/句边界，避免半个词）。所有切片都带 ``content_hash``
便于去重与溯源。

FTS5 影子索引由 ``services.knowledge.search`` 维护（SQLite 惰性建表）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.kb_models import KBDocument, KBChunk
from ...db.types import utcnow
from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, ValidationFailed
from ..hasher import content_hash

#: 允许导入的扩展名（任务书 §2.1）
SUPPORTED_EXTENSIONS = (".md", ".markdown", ".txt", ".pdf", ".docx")
#: 单文件上限 20MB（任务书 §4）
MAX_FILE_BYTES = 20 * 1024 * 1024
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
_BLANK_RE = re.compile(r"\n{3,}")
_SENT_END = "。！？!?;；\n"


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    parser: str


# --------------------------------------------------------------------------- #
# 文本清洗 / 切片
# --------------------------------------------------------------------------- #

def clean_text(raw: str) -> str:
    """Normalise whitespace while keeping paragraph and heading structure.

    * strip NUL bytes (PDF/docx 抽取常见噪声)
    * 去掉行尾空白、合并 3 行以上空行
    * 保留换行：段落边界是切片的主要语义锚点
    """
    text = raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return _BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown-ish text into ``(heading_path, body)`` pairs.

    A heading with no body still yields an entry so the following paragraphs keep
    their context label. Plain text (no ``#``) yields a single ``("", text)``.
    """
    sections: list[tuple[str, str]] = []
    stack: list[str] = []
    buf: list[str] = []
    heading = ""

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((heading, body))
        buf.clear()

    for line in text.split("\n"):
        m = _HEADING_RE.match(line.strip())
        if m:
            flush()
            level = len(m.group(1))
            stack[level - 1 :] = [m.group(2).strip()]
            heading = " / ".join(s for s in stack if s)
        else:
            buf.append(line)
    flush()
    return sections or [("", text.strip())]


def _paragraphs(body: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if para:
            out.append(para)
    return out


def _hard_split(piece: str, size: int) -> list[str]:
    """Split an over-long piece on sentence boundaries, then hard-wrap."""
    if len(piece) <= size:
        return [piece]
    parts = re.split(f"(?<=[{re.escape(_SENT_END)}])", piece)
    out: list[str] = []
    cur = ""
    for part in parts:
        while len(part) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(part[:size])
            part = part[size:]
        if len(cur) + len(part) <= size:
            cur += part
        elif cur:
            out.append(cur)
            cur = part
        else:
            cur = part
    if cur:
        out.append(cur)
    return out


def _overlap_tail(chunk: str, overlap: int) -> str:
    """Last ``overlap`` chars, re-anchored to a line/sentence start.

    只在前 60% 的窗口里找切点：否则当块尾刚好是句号时切点会落在窗口末尾，
    重叠就退化成空串（实测过）。
    """
    if overlap <= 0 or not chunk:
        return ""
    tail = chunk[-overlap:]
    limit = max(1, int(len(tail) * 0.6))
    cut = max(tail.rfind("\n", 0, limit),
              max((tail.rfind(c, 0, limit) for c in _SENT_END), default=-1))
    if cut >= 0:
        tail = tail[cut + 1 :]
    return tail.strip()


def chunk_text(
    text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Chunk cleaned text by heading/paragraph, ≤``size`` chars, ``overlap`` tail carry-over."""
    text = clean_text(text)
    if not text:
        return []

    units: list[str] = []
    for heading, body in split_sections(text):
        prefix = f"{heading}\n" if heading else ""
        for para in _paragraphs(body):
            piece = (prefix + para).strip()
            if piece:
                units.extend(_hard_split(piece, size))
    if not units:
        return []

    chunks: list[str] = []
    queue = list(units)
    cur = ""
    while queue:
        unit = queue.pop(0)
        if not cur:
            cur = unit
            continue
        if len(cur) + 1 + len(unit) <= size:
            cur = f"{cur}\n{unit}"
            continue
        chunks.append(cur)
        tail = _overlap_tail(cur, overlap)
        room = size - len(tail) if tail else size
        head, rest = (unit[:room], unit[room:]) if len(unit) > room else (unit, "")
        if rest:
            queue.insert(0, rest)
        cur = (f"{tail}\n{head}" if tail else head).strip()
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def _parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover — dependency is declared
        raise ValidationFailed(
            "pdf_parser_unavailable", "pypdf 未安装，无法解析 PDF（请安装 pypdf）"
        ) from exc
    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def _parse_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover — dependency is declared
        raise ValidationFailed(
            "docx_parser_unavailable", "python-docx 未安装，无法解析 DOCX（请安装 python-docx）"
        ) from exc
    document = docx.Document(BytesIO(data))
    blocks = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def validate_request(name: str, data: bytes) -> None:
    """Pre-flight request validation (before any row is created).

    Unsupported extension / empty file / oversize are *request* problems → 422
    DomainError, no dirty row. Content problems (corrupt PDF, no text) are
    handled inside :meth:`KnowledgeIngestService.ingest_bytes` as a ``failed`` row.
    """
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationFailed(
            "unsupported_extension",
            f"暂不支持的文件类型 {ext or '(无扩展名)'}；支持：{', '.join(SUPPORTED_EXTENSIONS)}",
        )
    if not data:
        raise ValidationFailed("empty_file", "文件内容为空，没有可索引的文本")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationFailed(
            "file_too_large",
            f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 上限（实际 {len(data)} 字节）",
        )


def parse_bytes(name: str, data: bytes) -> ParsedDocument:
    """Extract plain text from a supported document. Raises DomainError honestly."""
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationFailed(
            "unsupported_extension",
            f"暂不支持的文件类型 {ext or '(无扩展名)'}；支持：{', '.join(SUPPORTED_EXTENSIONS)}",
        )
    if not data:
        raise ValidationFailed("empty_file", "文件内容为空，没有可索引的文本")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationFailed(
            "file_too_large",
            f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 上限（实际 {len(data)} 字节）",
        )

    if ext in (".md", ".markdown", ".txt"):
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return ParsedDocument(text=data.decode(encoding), parser=f"text:{encoding}")
            except UnicodeDecodeError:
                continue
        raise ValidationFailed("decode_failed", "无法按 UTF-8/GB18030 解码该文本文件")
    if ext == ".pdf":
        try:
            return ParsedDocument(text=_parse_pdf(data), parser="pypdf")
        except ValidationFailed:
            raise
        except Exception as exc:  # noqa: BLE001 — 损坏 PDF 必须显式失败
            raise ValidationFailed("pdf_parse_failed", f"PDF 解析失败：{type(exc).__name__}") from exc
    try:
        return ParsedDocument(text=_parse_docx(data), parser="python-docx")
    except ValidationFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed("docx_parse_failed", f"DOCX 解析失败：{type(exc).__name__}") from exc


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #

class KnowledgeIngestService:
    """Document ingest / list / cascade-delete with audit trail."""

    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    # -- read ---------------------------------------------------------------- #
    def list_documents(self, actor: Actor, *, owner_id: str) -> list[KBDocument]:
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        return list(
            self.s.execute(
                select(KBDocument)
                .where(KBDocument.owner_id == owner_id)
                .order_by(KBDocument.created_at.desc(), KBDocument.id)
            ).scalars()
        )

    # -- write --------------------------------------------------------------- #
    def ingest_bytes(
        self,
        actor: Actor,
        *,
        owner_id: str,
        name: str,
        data: bytes,
        source: str = "local",
        external_id: str = "",
    ) -> KBDocument:
        """Ingest one document. Returns the row (ready **or** failed-with-reason).

        Request-level problems (bad extension / empty / oversize) raise before any
        row is created. Content-level problems (corrupt PDF, no extractable text)
        persist ``status='failed'`` + ``error`` so the UI can show the truth.
        """
        actor.require_authenticated()
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        if not name.strip():
            raise ValidationFailed("name_required", "文档名不能为空")

        from .search import ensure_fts_table, index_chunks_fts  # local: avoid import cycle

        validate_request(name, data)

        doc = KBDocument(
            id=uuid4().hex,
            owner_id=owner_id,
            name=name.strip()[:500],
            source=source,
            external_id=external_id[:300],
            size=len(data),
            status="indexing",
            chunk_count=0,
        )
        self.s.add(doc)
        self.s.flush()

        try:
            parsed = parse_bytes(doc.name, data)
            chunks = chunk_text(parsed.text)
            if not chunks:
                raise ValidationFailed(
                    "empty_content", "文档解析后没有可索引的文本（可能是扫描件 PDF 或空文档）"
                )
        except ValidationFailed as exc:
            doc.status = "failed"
            doc.error = f"{exc.code}: {exc.message}"[:1000]
            doc.updated_at = utcnow()
            self.s.flush()
            self.audit.append(
                actor, "kb.document.import_failed", doc.id,
                {"source": source, "error": doc.error, "size": doc.size},
            )
            return doc

        rows: list[KBChunk] = []
        for seq, content in enumerate(chunks):
            chunk = KBChunk(
                id=uuid4().hex,
                doc_id=doc.id,
                owner_id=owner_id,
                seq=seq,
                content=content,
                content_hash=content_hash(content),
            )
            self.s.add(chunk)
            rows.append(chunk)
        self.s.flush()

        if ensure_fts_table(self.s):
            index_chunks_fts(self.s, rows)

        doc.status = "ready"
        doc.error = ""
        doc.chunk_count = len(rows)
        doc.updated_at = utcnow()
        self.s.flush()
        # 审计只记元数据，绝不记原文（FROZEN_CONTRACT §13）。
        self.audit.append(
            actor, "kb.document.imported", doc.id,
            {"source": source, "chunks": len(rows), "size": doc.size,
             "parser": parsed.parser, "name": doc.name},
        )
        return doc

    def ingest_text(
        self, actor: Actor, *, owner_id: str, name: str, text: str,
        source: str = "local", external_id: str = "",
    ) -> KBDocument:
        """Text entry point used by source adapters (already-extracted plain text)."""
        return self.ingest_bytes(
            actor, owner_id=owner_id, name=name,
            data=(text or "").encode("utf-8"), source=source, external_id=external_id,
        )

    def delete_document(self, actor: Actor, doc_id: str) -> dict[str, Any]:
        """Cascade delete chunks (+ FTS shadow rows) and write an audit event."""
        actor.require_authenticated()
        owner_id = actor.owner_id
        if not owner_id:
            raise ValidationFailed("owner_required", "owner_id is required")
        doc = self.s.execute(
            select(KBDocument).where(
                KBDocument.id == doc_id, KBDocument.owner_id == owner_id
            )
        ).scalar_one_or_none()
        if doc is None:
            # 跨 owner 一律 404：不泄露「该文档存在但不属于你」。
            raise NotFound("kb_document_not_found", "文档不存在")

        from .search import delete_chunks_fts  # local: avoid import cycle

        chunks = list(
            self.s.execute(select(KBChunk).where(KBChunk.doc_id == doc_id)).scalars()
        )
        delete_chunks_fts(self.s, [c.id for c in chunks])
        self.s.execute(
            KBChunk.__table__.delete().where(KBChunk.doc_id == doc_id)
        )
        self.s.delete(doc)
        self.s.flush()
        self.audit.append(
            actor, "kb.document.deleted", doc_id,
            {"chunks_deleted": len(chunks), "source": doc.source, "name": doc.name},
        )
        return {"id": doc_id, "deleted_chunks": len(chunks), "status": "deleted"}