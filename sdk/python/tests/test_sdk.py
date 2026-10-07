"""Python SDK 单元测试（A-生态兼容-01 · P12）。"""

import pytest
from unittest.mock import MagicMock, patch

from find_yourself_sdk import (
    FindYourselfClient,
    generate_iframe_embed_url,
    verify_embed_signature,
)


def test_embed_url_generation_and_signature():
    secret = "corporate-secret-key-12345"
    url = generate_iframe_embed_url(
        "http://localhost:8000",
        page="marketplace",
        tenant_id="tenant-acme",
        user_id="user-42",
        secret_key=secret,
        theme="dark",
    )
    assert "http://localhost:8000/plugins?" in url
    assert "tenant_id=tenant-acme" in url
    assert "user_id=user-42" in url
    assert "theme=dark" in url
    assert "sig=" in url

    # 从 URL 提取参数测试验证函数
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    valid = verify_embed_signature(
        tenant_id=qs["tenant_id"][0],
        user_id=qs["user_id"][0],
        timestamp=qs["ts"][0],
        signature=qs["sig"][0],
        secret_key=secret,
    )
    assert valid is True

    # 错误秘钥校验应失败
    invalid = verify_embed_signature(
        tenant_id=qs["tenant_id"][0],
        user_id=qs["user_id"][0],
        timestamp=qs["ts"][0],
        signature=qs["sig"][0],
        secret_key="wrong-secret",
    )
    assert invalid is False


@patch("requests.Session.request")
def test_client_marketplace_methods(mock_request):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "items": [{"skill_id": "s-1", "name": "tool", "rating": {"score": 4.8}}],
        "total": 1,
    }
    mock_request.return_value = mock_resp

    client = FindYourselfClient("http://api.example.com", token="tok-123")
    res = client.list_plugins(query="test", sort_by="score")

    assert res["total"] == 1
    mock_request.assert_called_once()
    args, kwargs = mock_request.call_args
    assert args[0] == "GET"
    assert "http://api.example.com/api/plugins/marketplace" in args[1]
    assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
    assert kwargs["params"]["sort_by"] == "score"


@patch("requests.Session.request")
def test_client_template_methods(mock_request):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "magic": "FYTEMPLATE_V1",
        "checksum": "abc1234",
    }
    mock_request.return_value = mock_resp

    client = FindYourselfClient(csrf_token="csrf-999")
    exp = client.export_template("writing-pipeline")
    assert exp["magic"] == "FYTEMPLATE_V1"

    # 模拟导入
    client.import_template({"magic": "FYTEMPLATE_V1"}, confirmed_tools=["text_search"])
    last_call = mock_request.call_args
    assert last_call[1]["headers"]["X-CSRF-Token"] == "csrf-999"


@patch("requests.Session.request")
def test_client_code_context_methods(mock_request):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "prompt_context": "### 📌 Cursor-Grade Context",
        "total_chars": 120,
    }
    mock_request.return_value = mock_resp

    client = FindYourselfClient()
    res = client.assemble_code_context("query", {"test.py": "def foo(): pass"})
    assert "Cursor-Grade" in res["prompt_context"]
