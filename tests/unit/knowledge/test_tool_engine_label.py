"""kb.search 回传的 engine 标签必须与真实检索模式一致（防再次漂移）。

背景（2026-10-06 主控裁决项）：混合检索落地后 search() 默认模式变为 hybrid，
而 run_tool_search 的标签硬编码 ``"fts5+lexical"``——标签说谎。现两者共用
``_KB_TOOL_SEARCH_MODE`` 常量；本测试钉死「实际传给 search() 的模式 == 回传标签」。
"""

from unittest import mock

from find_yourself.services import knowledge as kb_mod


def test_kb_search_tool_engine_label_matches_actual_mode(monkeypatch):
    captured: dict = {}

    class FakeSearchService:
        def __init__(self, session):
            pass

        def search(self, actor, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(kb_mod, "KnowledgeSearchService", FakeSearchService)
    monkeypatch.setattr(kb_mod, "_open_session", lambda: mock.MagicMock())
    monkeypatch.setattr(kb_mod, "_local_owner_id", lambda: "owner-1")

    payload = kb_mod.run_tool_search({"query": "hello"})

    assert captured.get("mode") == kb_mod._KB_TOOL_SEARCH_MODE
    assert payload["engine"] == captured["mode"]
