"""P11 测试：内置模块规范包（A-内置模块-02/08/10/05b）+ skill-scout 门禁登记。"""

from __future__ import annotations

from pathlib import Path

from find_yourself.services.prompt import compress_long_document, extract_anchors

CONVENTIONS = Path("skills_packages/_conventions/CONVENTIONS.md")
DEFAULTS = Path("prompts_packages/_defaults/product_defaults.md")


def test_conventions_doc_exists_with_required_sections():
    text = CONVENTIONS.read_text(encoding="utf-8")
    assert "规范技能根目录集" in text and "标签" in text and "交付惯例" in text


def test_product_defaults_exists_and_covers_honesty():
    text = DEFAULTS.read_text(encoding="utf-8")
    assert "诚实原则" in text and "出厂默认指令" in text


def test_extract_anchors_finds_all_kinds():
    doc = """# 系统设计
def build_graph(checkpointer=None):
    ...
## 性能数据
吞吐量 1200 ms
结论：采用 sqlite 落盘方案。
"""
    anchors = extract_anchors(doc)
    kinds = {a["kind"] for a in anchors}
    assert {"heading", "code_signature", "data_line", "conclusion"} <= kinds


def test_compress_long_document_never_copies_body():
    doc = "标题行\n" + "正文" * 500
    out = compress_long_document(doc)
    assert out["original_length"] == len(doc)
    blob = str(out)
    assert "正文" * 20 not in blob            # 压缩结果不含原文副本（不搬运铁律）


def test_skill_scout_gate_item_honest_disposition():
    """门禁项 skill-scout 路径修正：全仓无该配置对象（2026-10-07 实测），
    诚实处置=登记无可修正对象，等待需求方给出实际路径。"""
    assert CONVENTIONS.exists()               # 惯例文档已落，作为登记载体
