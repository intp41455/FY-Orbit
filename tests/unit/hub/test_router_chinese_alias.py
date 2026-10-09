"""守住 hub 中文路由：Capability.aliases 走子串匹配。

为什么需要这个文件
------------------
`router._matches` 判定命中的两条路是「标签 token 命中」或「标签作为子串出现在原句里」。
英文标签（math/calc/search）永远不会作为子串出现在中文句子里，而中文分词产生的
token 是「数学」「加法」这类中文二元组，与英文标签之间没有任何映射 ——
于是「帮我算一下数学加法」匹配不到 tags=["math","calc"] 的能力，路由返回空数组。

对多Agent 协同来说这是卡在主链路上的问题：中枢靠路由决定派给哪个 Agent，
路由恒空就等于没有派单。所以这里把行为钉死。

参考：src/find_yourself/services/hub/router.py::_matches
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub.adapters import Capability
from find_yourself.services.hub.router import _matches, tokenize

# --------------------------------------------------------------------------- #
# Capability.aliases 的序列化往返
# --------------------------------------------------------------------------- #


def test_aliases_roundtrip_through_public_dict() -> None:
    """aliases 必须能经 to_public/from_public 往返，否则重启后中文路由能力丢失。"""
    cap = Capability.from_public(
        {
            "name": "tool:add",
            "tags": ["math", "calc"],
            "aliases": ["加法", "求和"],
            "description": "两数相加",
        }
    )
    assert cap.aliases == ("加法", "求和")

    again = Capability.from_public(cap.to_public())
    assert again.aliases == ("加法", "求和")
    assert again.tags == ("math", "calc")


def test_aliases_default_empty_for_backward_compat() -> None:
    """旧数据没有 aliases 键时必须正常解析，不能因为新增字段炸掉。"""
    cap = Capability.from_public({"name": "chat", "tags": ["llm"]})
    assert cap.aliases == ()


def test_from_public_rejects_missing_name() -> None:
    with pytest.raises(ValidationFailed):
        Capability.from_public({"tags": ["math"]})


# --------------------------------------------------------------------------- #
# 核心：中文任务描述命中英文标签的能力（靠别名）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "hint",
    [
        "帮我算一下数学加法",
        "加法计算",
        "求和一下",
        "帮我把这两份文档的数字加起来",
    ],
)
def test_chinese_hint_hits_english_tagged_capability_via_alias(hint: str) -> None:
    """这是修复前必然失败、修复后必须通过的用例。

    tags=["math","calc"] 的能力在中文句子里匹配不到，只能靠 aliases 里的中文
    字面走子串匹配命中。注意 aliases 必须覆盖句中真正出现的那个词 ——
    「加法」和「加起来」语义相同但字面不同，别名少写一个就漏一个。
    """
    cap = Capability.from_public(
        {
            "name": "tool:add",
            "tags": ["math", "calc", "tool"],
            "aliases": ["加法", "求和", "合计", "加起来"],
        }
    )
    hits = _matches(cap, tokenize(hint), hint)
    assert hits, f"中文描述 {hint!r} 应命中带中文别名的能力"


def test_single_char_alias_is_allowed() -> None:
    """别名放宽到长度 >= 1，让「查」「写」「算」这类单字也能用。

    tags 仍要求长度 >= 2（英文短词在长句里子串误命中率高），aliases 是用户
    显式声明的精确意图，不受此限制。
    """
    cap = Capability.from_public({"name": "kb.search", "tags": ["search"], "aliases": ["查"]})
    assert _matches(cap, tokenize("帮我查一下资料"), "帮我查一下资料")


def test_alias_absent_still_uses_tag_token_path() -> None:
    """没有别名时，英文 token 路径仍须工作（英文任务描述不受影响）。"""
    cap = Capability.from_public({"name": "tool:add", "tags": ["math", "calc"]})
    hits = _matches(cap, tokenize("math calc"), "math calc")
    assert "math" in hits and "calc" in hits


def test_no_match_returns_empty_not_fallback() -> None:
    """诚实原则：匹配不上就返回空，绝不随便挑一个凑数。"""
    cap = Capability.from_public(
        {
            "name": "tool:add",
            "tags": ["math", "calc"],
            "aliases": ["加法", "求和"],
        }
    )
    hint = "帮我把这个视频转成字幕"
    assert _matches(cap, tokenize(hint), hint) == []


def test_capability_name_substring_still_counts() -> None:
    """能力名本身出现在句子里也算命中（原有行为不能回归）。

    同时注意 tags 里的 "knowledge" 是 "knowledge.search" 的子串，所以两者都会
    命中 —— `_matches` 返回的是全部命中项而非单项，这里断言包含关系。
    """
    cap = Capability.from_public({"name": "knowledge.search", "tags": ["knowledge"]})
    hits = _matches(cap, tokenize("随便什么话"), "knowledge.search 能用吗")
    assert "knowledge.search" in hits
