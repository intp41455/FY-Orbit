"""W3 知识库 · 导入管线单测（清洗 / 切片 / 解析 / 入库 / 级联删除）。

覆盖要点：
* 扩展名 / 空文件 / 超限文件在**建行之前**就抛 DomainError（不留脏文档）；
* 切片 ≤800 字符、带 100 字符重叠、保留标题上下文、**内容不丢失**；
* 损坏 PDF 落库为 ``status='failed'`` + 用户可见 error（诚实，不静默、不假成功）；
* 删除文档级联清空 chunks 并写审计事件；
* 跨 owner 删除/读取一律 404。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

import find_yourself.db.kb_models  # noqa: F401  (建表需要元数据已注册)
from find_yourself.db.kb_models import KBChunk
from find_yourself.db.models import AuditEvent
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.knowledge.ingest import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    MAX_FILE_BYTES,
    KnowledgeIngestService,
    chunk_text,
    clean_text,
    parse_bytes,
    split_sections,
)

MD = """# 数码小屋设计

## 家具布置
家具必须遵守碰撞规则，地板贴图按房间尺寸生成。

## 探险玩法
背景探险包含采集、事件与日常任务三类内容。
"""


def _ingest(session, owner_id="owner-1", name="design.md", data=None):
    return KnowledgeIngestService(session, AuditService(session)).ingest_bytes(
        Actor.owner(owner_id), owner_id=owner_id, name=name,
        data=MD.encode("utf-8") if data is None else data,
    )


# --- 清洗与切片 ------------------------------------------------------------- #

def test_clean_text_collapses_blank_lines_and_strips_nul():
    assert clean_text("a\x00b\r\n\r\n\r\n\r\nc  \n") == "ab\n\nc"


def test_split_sections_tracks_heading_path():
    sections = split_sections(clean_text(MD))
    headings = [h for h, _ in sections]
    assert headings == ["数码小屋设计 / 家具布置", "数码小屋设计 / 探险玩法"]


def test_chunk_text_respects_size_bound_and_overlap():
    long_body = "\n\n".join(f"第{i}段：这是一段用于测试切片长度与重叠的中文内容。" * 3 for i in range(40))
    chunks = chunk_text(long_body)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert all(c.strip() for c in chunks)
    # 相邻块之间存在重叠（后一块开头能在前一块尾部找到）
    assert any(
        chunks[i + 1].split("\n")[0] and chunks[i + 1].split("\n")[0] in chunks[i]
        for i in range(len(chunks) - 1)
    )


def test_chunk_text_keeps_heading_context_and_content():
    chunks = chunk_text(MD)
    assert chunks[0].startswith("数码小屋设计")
    joined = "".join(chunks)
    assert "碰撞规则" in joined and "日常任务" in joined


def test_chunk_text_of_empty_text_is_empty_list():
    assert chunk_text("   \n\n ") == []


# --- 解析 ------------------------------------------------------------------- #

def test_parse_bytes_rejects_unsupported_extension():
    with pytest.raises(ValidationFailed) as err:
        parse_bytes("photo.png", b"\x89PNG")
    assert err.value.code == "unsupported_extension"


def test_parse_bytes_rejects_empty_file():
    with pytest.raises(ValidationFailed) as err:
        parse_bytes("empty.md", b"")
    assert err.value.code == "empty_file"


def test_parse_bytes_rejects_oversize_file(monkeypatch):
    import find_yourself.services.knowledge.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "MAX_FILE_BYTES", 16)
    with pytest.raises(ValidationFailed) as err:
        parse_bytes("big.txt", b"x" * 32)
    assert err.value.code == "file_too_large"
    assert MAX_FILE_BYTES == 20 * 1024 * 1024


def test_parse_bytes_decodes_gb18030_fallback():
    parsed = parse_bytes("gbk.txt", "数码小屋".encode("gb18030"))
    assert parsed.text == "数码小屋"
    assert parsed.parser.startswith("text:")


def test_parse_bytes_corrupt_pdf_fails_with_code():
    with pytest.raises(ValidationFailed) as err:
        parse_bytes("broken.pdf", b"%PDF-1.4 not really a pdf")
    assert err.value.code == "pdf_parse_failed"


def test_parse_bytes_rejects_undetectable_text_encoding():
    with pytest.raises(ValidationFailed) as err:
        parse_bytes("weird.txt", bytes([0xFF, 0xFE, 0xFD]) + b"\x00" * 8)
    assert err.value.code == "decode_failed"


# --- 入库 / 状态 ------------------------------------------------------------ #

def test_ingest_markdown_document_becomes_ready(session):
    doc = _ingest(session)
    assert doc.status == "ready"
    assert doc.error == ""
    assert doc.chunk_count >= 1
    chunks = session.execute(select(KBChunk).where(KBChunk.doc_id == doc.id)).scalars().all()
    assert len(chunks) == doc.chunk_count
    assert all(c.owner_id == "owner-1" for c in chunks)
    assert all(len(c.content_hash) == 64 for c in chunks)


def test_ingest_broken_pdf_persists_failed_status_with_reason(session):
    doc = _ingest(session, name="broken.pdf", data=b"%PDF-1.4 garbage")
    assert doc.status == "failed"
    assert "pdf_parse_failed" in doc.error
    assert doc.chunk_count == 0
    chunks = session.execute(select(KBChunk).where(KBChunk.doc_id == doc.id)).scalars().all()
    assert chunks == []


def test_ingest_audit_event_has_no_original_content(session):
    doc = _ingest(session)
    events = session.execute(
        select(AuditEvent).where(AuditEvent.action == "kb.document.imported")
    ).scalars().all()
    assert len(events) == 1
    assert "碰撞规则" not in str(events[0].details)
    assert events[0].details["chunks"] == doc.chunk_count


# --- 删除 ------------------------------------------------------------------- #

def test_delete_document_cascades_chunks_and_audits(session):
    doc = _ingest(session)
    svc = KnowledgeIngestService(session, AuditService(session))
    result = svc.delete_document(Actor.owner("owner-1"), doc.id)
    assert result["deleted_chunks"] == doc.chunk_count
    assert session.execute(select(KBChunk).where(KBChunk.doc_id == doc.id)).scalars().all() == []
    actions = [
        e.action for e in session.execute(select(AuditEvent)).scalars().all()
    ]
    assert "kb.document.deleted" in actions


def test_delete_document_of_other_owner_is_not_found(session):
    doc = _ingest(session, owner_id="owner-1")
    svc = KnowledgeIngestService(session, AuditService(session))
    with pytest.raises(NotFound) as err:
        svc.delete_document(Actor.owner("owner-2"), doc.id)
    assert err.value.code == "kb_document_not_found"
    assert session.execute(select(KBChunk).where(KBChunk.doc_id == doc.id)).scalars().all() != []


def test_list_documents_is_owner_scoped(session):
    _ingest(session, owner_id="owner-1", name="a.md")
    _ingest(session, owner_id="owner-2", name="b.md")
    svc = KnowledgeIngestService(session, AuditService(session))
    mine = svc.list_documents(Actor.owner("owner-1"), owner_id="owner-1")
    assert [d.name for d in mine] == ["a.md"]


def test_ingest_requires_owner_id(session):
    with pytest.raises(ValidationFailed) as err:
        KnowledgeIngestService(session, AuditService(session)).ingest_bytes(
            Actor.owner("owner-1"), owner_id="", name="x.md", data=b"hi"
        )
    assert err.value.code == "owner_required"


def test_chunk_overlap_constant_is_documented_value():
    assert CHUNK_OVERLAP == 100
