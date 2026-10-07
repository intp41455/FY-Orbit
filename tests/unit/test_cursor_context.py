"""Cursor 级代码上下文极致理解与切片引擎测试（A-生态兼容-04 · P12）。

验证项：
1. Python AST 符号提取：精确提取类、方法、独立函数、参数签名与 Docstring。
2. TypeScript / JS 与 Java 符号提取：支持 interface、class 与方法识别。
3. 相关性评分打分：精确命中符号名获高分，Token 重叠与 Docstring 命中加分。
4. 多文件代码切片搜索：按相关度从高到低排序。
5. 预算自适应高密度装配：在字符上限内紧凑装配代码块，长函数智能折叠。
"""

import pytest

from find_yourself.services.templates.cursor_context import CursorContextEngine


@pytest.fixture()
def engine():
    return CursorContextEngine()


PYTHON_SAMPLE = """
import os
from typing import Optional

class MarketplaceService:
    \"\"\"插件市场核心管理服务。\"\"\"
    def __init__(self, db_path: str):
        self.db_path = db_path

    def rate_package(self, package_id: str, rating: float) -> dict:
        \"\"\"为插件包评定 1-5 星分值。\"\"\"
        if rating < 1.0 or rating > 5.0:
            raise ValueError("Invalid rating")
        return {"id": package_id, "rating": rating}

def calculate_average(scores: list[float]) -> float:
    \"\"\"计算平均得分。\"\"\"
    return sum(scores) / len(scores) if scores else 0.0
"""

TS_SAMPLE = """
export interface PluginCard {
  skillId: string;
  name: string;
  rating: number;
}

export class MarketplaceClient {
  async getPackage(id: string): Promise<PluginCard> {
    return { skillId: id, name: "demo", rating: 5.0 };
  }
}
"""

JAVA_SAMPLE = """
package com.findyourself.sdk;

public class FindYourselfClient {
    private String endpoint;

    public PluginCard getPackage(String id) {
        return new PluginCard(id);
    }
}
"""


def test_python_ast_symbol_extraction(engine):
    symbols = engine.extract_symbols("services/marketplace.py", PYTHON_SAMPLE)
    names = {s.name: s for s in symbols}
    assert "MarketplaceService" in names
    assert "MarketplaceService.rate_package" in names
    assert "calculate_average" in names

    cls_sym = names["MarketplaceService"]
    assert cls_sym.kind == "class"
    assert "插件市场核心管理服务" in cls_sym.docstring

    method_sym = names["MarketplaceService.rate_package"]
    assert method_sym.kind == "method"
    assert "package_id: str, rating: float" in method_sym.signature
    assert "1-5 星" in method_sym.docstring


def test_ts_and_java_symbol_extraction(engine):
    ts_symbols = engine.extract_symbols("src/client.ts", TS_SAMPLE)
    ts_names = {s.name: s for s in ts_symbols}
    assert "PluginCard" in ts_names
    assert ts_names["PluginCard"].kind == "interface"
    assert "MarketplaceClient" in ts_names

    java_symbols = engine.extract_symbols("FindYourselfClient.java", JAVA_SAMPLE)
    java_names = {s.name: s for s in java_symbols}
    assert "FindYourselfClient" in java_names
    assert java_names["FindYourselfClient"].kind == "class"


def test_relevance_scoring_and_ranking(engine):
    files = {
        "services/marketplace.py": PYTHON_SAMPLE,
        "src/client.ts": TS_SAMPLE,
    }
    # 搜索 "rate_package"
    slices = engine.search_context_slices(files, query="rate_package", limit=5)
    assert slices
    top = slices[0]
    assert top.symbol_name == "MarketplaceService.rate_package"
    assert top.relevance_score >= 40.0
    assert any("symbol_name_match" in r for r in top.reasons)


def test_context_prompt_assembly_with_budget(engine):
    files = {"sample.py": PYTHON_SAMPLE}
    slices = engine.search_context_slices(files, query="MarketplaceService rate_package")
    assembly = engine.assemble_context_prompt(slices, max_chars=1500)

    assert "### 📌 Cursor-Grade Codebase Context" in assembly["prompt_context"]
    assert "MarketplaceService" in assembly["prompt_context"]
    assert assembly["total_chars"] <= 1500
    assert assembly["slice_count"] >= 1
    assert assembly["included_slices"]


def test_long_body_compression_in_assembler(engine):
    long_code = "def huge_function():\n" + "\n".join(f"    x_{i} = {i}" for i in range(50))
    files = {"huge.py": long_code}
    slices = engine.search_context_slices(files, query="huge_function")
    assembly = engine.assemble_context_prompt(slices, max_chars=4000, compress_long_bodies=True)

    assert "Implementation details omitted by Cursor Context Engine" in assembly["prompt_context"]
