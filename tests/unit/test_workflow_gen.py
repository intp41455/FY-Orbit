"""W5 工作流工坊 · 后端单元测试（生成 + 导出 + 图/DSL 互转 + HTTP 契约）。

覆盖任务书 §5 验收清单的后端部分：

1. 合法 DSL 生成：模型输出过服务端复验（validate_dsl + compile_dsl）才算成功；
2. **非法输出自动重试一次**：第二次把真实校验错误回灌进提示词；
3. 两次都非法 → ``WorkflowGenerationFailed``（422），错误里带真实校验信息，
   **不返回任何占位 DSL**；
4. 无模型 key → ``ModelNotConfigured``（503，诚实失败，绝不伪造）；
5. 导出脚本：不含密钥、地址是 ``{{FY_BASE_URL}}`` 占位符、头部含来源 prompt
   与生成时间、非法 DSL / 有环 DSL 一律拒绝导出；
6. **导出的 .py 在干净环境真能跑**（子进程 exec 冒烟，含条件边与跳过语义）；
7. 图↔DSL 双向序列化：坐标永不进入 DSL，往返语义无损；
8. HTTP：未鉴权 401、缺 CSRF 403、无模型 503、非法 DSL 422 带行列级 details、
   ``GET /status`` 如实报告 ``model_configured``。
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.runtime.gateway import CallResult, ModelGateway
from find_yourself.services.actor import Actor
from find_yourself.services.workflow_gen import (
    BASE_URL_PLACEHOLDER,
    GEN_MAX_ATTEMPTS,
    FlowGraph,
    GraphEdge,
    GraphNode,
    WorkflowGenerationFailed,
    WorkflowGenService,
    build_generation_prompt,
    default_script_name,
    dsl_to_graph,
    export_script,
    extract_json_object,
    graph_to_dsl,
)

LOCAL_TOKEN = "dev-token-secret-w5"

VALID_DSL = {
    "version": "1",
    "nodes": [
        {"id": "in1", "type": "input", "params": {"kind": "literal", "value": [{"value": "示例行"}]}},
        {"id": "tf1", "type": "transform", "verb": "template",
         "params": {"template": "处理：{value}"}},
        {"id": "out1", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "in1", "to": "tf1"}, {"from": "tf1", "to": "out1"}],
}


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class _StubProvider:
    """按调用顺序返回预置回复的确定性 provider，并记录每次收到的提示词。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.last_call: dict = {}

    def complete(self, *, model: str, prompt: str, max_tokens: int = 1024,
                 timeout_seconds: float = 30.0):
        self.prompts.append(prompt)
        text = self.responses.pop(0) if self.responses else "{}"
        self.last_call = {"model": model, "max_tokens": max_tokens}
        return CallResult(
            text=text,
            usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            settled_amount=Decimal("0"),
            provider_request_id="stub-w5",
        )


def _service(provider: _StubProvider | None, *, responses: list[str] | None = None) -> WorkflowGenService:
    """构造一个带注入 provider 的生成服务（不依赖真实网络）。"""
    settings = Settings(
        session_secret="x" * 32,
        model_api_key="test-key",
        model_base_url="http://127.0.0.1:9",
        model_name="gpt-4o-mini",
    )
    stub = provider if provider is not None else _StubProvider(responses or [])
    gateway = ModelGateway(settings, provider=stub)
    return WorkflowGenService(settings=settings, model_gateway=gateway)


OWNER = Actor.owner("owner-w5", csrf_token="")


# =========================================================================== #
# 1. 生成：合法输出 + 服务端复验
# =========================================================================== #


def test_generate_valid_dsl_passes_server_validation():
    stub = _StubProvider([json.dumps(VALID_DSL, ensure_ascii=False)])
    res = _service(stub).generate(OWNER, prompt="每天读文件并发邮件")
    assert res["dsl"] == VALID_DSL
    assert res["attempts"] == [{"attempt": 1, "ok": True, "error": ""}]
    assert res["model"] == "gpt-4o-mini"


def test_generate_accepts_markdown_fenced_json():
    """模型常把 JSON 包在```json 里；剥代码块属于容错，不算伪造。"""
    fenced = f"```json\n{json.dumps(VALID_DSL, ensure_ascii=False)}\n```"
    stub = _StubProvider([fenced])
    res = _service(stub).generate(OWNER, prompt="测试")
    assert res["dsl"] == VALID_DSL


def test_generation_prompt_constrains_to_restricted_verbs():
    prompt = build_generation_prompt("每天读文件并发邮件")
    # 提示词必须写清受限动词集与「只输出 JSON」的硬性要求。
    for token in ("input", "transform", "output", "template", "filter", "map",
                  "只输出一个 JSON 对象", "不得自创"):
        assert token in prompt, token


# =========================================================================== #
# 2. 非法输出 → 重试一次 → 错误回灌
# =========================================================================== #


def test_invalid_first_attempt_retries_once_with_real_validation_error():
    """第一轮 verb 非法 → 自动重试；第二轮合法则成功，且第二轮提示词带真实错误。"""
    bad = json.loads(json.dumps(VALID_DSL))
    bad["nodes"][1]["verb"] = "translate"  # 不在受限动词集内
    stub = _StubProvider([json.dumps(bad, ensure_ascii=False),
                          json.dumps(VALID_DSL, ensure_ascii=False)])
    res = _service(stub).generate(OWNER, prompt="测试")

    assert len(stub.prompts) == 2
    # 第二次提示词必须包含第一次的真实校验错误
    assert "上一轮输出的真实校验错误" in stub.prompts[1]
    assert "translate" in stub.prompts[1]
    assert res["dsl"] == VALID_DSL
    assert [a["ok"] for a in res["attempts"]] == [False, True]


def test_cyclic_model_output_is_rejected_and_retried():
    """环检测在服务端复验里：模型给的成环 DSL 不能被接受。"""
    cyclic = {
        "version": "1",
        "nodes": [
            {"id": "a", "type": "input", "params": {"kind": "literal", "value": 1}},
            {"id": "b", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    stub = _StubProvider([json.dumps(cyclic), json.dumps(VALID_DSL, ensure_ascii=False)])
    res = _service(stub).generate(OWNER, prompt="测试")
    assert res["dsl"] == VALID_DSL
    assert "存在环" in stub.prompts[1]


def test_two_invalid_attempts_raise_422_without_fabricating_dsl():
    bad = json.loads(json.dumps(VALID_DSL))
    bad["nodes"][1]["verb"] = "translate"
    stub = _StubProvider([json.dumps(bad), json.dumps(bad)])
    with pytest.raises(WorkflowGenerationFailed) as exc:
        _service(stub).generate(OWNER, prompt="测试")
    # 恰好两次尝试（任务书：自动重试一次，仍非法则如实返回）
    assert len(stub.prompts) == GEN_MAX_ATTEMPTS
    assert exc.value.http_status == 422
    assert "translate" in exc.value.message


def test_non_json_model_output_fails_honestly():
    stub = _StubProvider(["抱歉，我无法完成这个任务。", "还是不行"])
    with pytest.raises(WorkflowGenerationFailed):
        _service(stub).generate(OWNER, prompt="测试")


def test_extract_json_object_rejects_garbage():
    with pytest.raises(Exception):
        extract_json_object("not json at all")
    with pytest.raises(Exception):
        extract_json_object("")


# =========================================================================== #
# 3. 无模型 key → 诚实 503
# =========================================================================== #


def test_generate_without_provider_raises_503_not_configured():
    """没有 settings / provider 时必须抛 ModelNotConfigured（503），绝不返回兜底 DSL。"""
    from find_yourself.runtime.gateway import ModelNotConfigured

    svc = WorkflowGenService()  # 既无 settings 也无 gateway
    assert svc.model_configured() is False
    with pytest.raises(ModelNotConfigured) as exc:
        svc.generate(OWNER, prompt="每天读文件并发邮件")
    assert exc.value.http_status == 503


def test_generate_rejects_empty_and_overlong_prompt():
    from find_yourself.services.errors import ValidationFailed

    svc = _service(_StubProvider([]))
    with pytest.raises(ValidationFailed):
        svc.generate(OWNER, prompt="   ")
    with pytest.raises(ValidationFailed):
        svc.generate(OWNER, prompt="x" * 5000)


# =========================================================================== #
# 4. 导出脚本
# =========================================================================== #


def test_export_script_has_placeholder_and_no_secret():
    out = export_script(VALID_DSL, prompt="每天读文件并发邮件")
    assert out["filename"].endswith(".py")
    assert out["base_url_placeholder"] == BASE_URL_PLACEHOLDER
    assert BASE_URL_PLACEHOLDER in out["script"]
    # 头部注释含来源 prompt 与生成时间
    assert "每天读文件并发邮件" in out["script"]
    assert out["generated_at"] in out["script"]
    # 不得出现任何疑似密钥的赋值
    for line in out["script"].splitlines():
        low = line.lower()
        assert "api_key" not in low
        assert "secret" not in low
        assert "bearer " not in low


def test_export_without_prompt_states_it_honestly():
    out = export_script(VALID_DSL)
    assert "未记录" in out["script"]


def test_export_rejects_invalid_and_cyclic_dsl():
    from find_yourself.services.dsl_canvas import DslValidationError

    bad = json.loads(json.dumps(VALID_DSL))
    bad["nodes"][1]["verb"] = "translate"
    with pytest.raises(DslValidationError):
        export_script(bad)

    cyclic = {
        "version": "1",
        "nodes": [
            {"id": "a", "type": "input", "params": {"kind": "literal", "value": 1}},
            {"id": "b", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    with pytest.raises(DslValidationError):
        export_script(cyclic)


def test_default_script_name_is_filesystem_safe():
    assert default_script_name("每天读文件并发邮件") == "fy_workflow.py"
    assert default_script_name("a/b\\c:d*e?f") == "fy_workflow_a_b_c_d_e_f.py"
    assert default_script_name(None) == "fy_workflow.py"


# =========================================================================== #
# 5. 导出脚本真能跑（干净环境子进程冒烟）
# =========================================================================== #


def _run_exported(tmp_path, dsl, *args: str, prompt: str = "冒烟"):
    """把导出的脚本落到临时目录并用**当前解释器**跑一遍。

    ``prompt`` 是 keyword-only：否则调用方传的第一个 CLI 开关会被当成 prompt 吞掉。
    """
    out = export_script(dsl, prompt=prompt)
    path = tmp_path / out["filename"]
    path.write_text(out["script"], encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(path), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(tmp_path), env={"PATH": "", "SYSTEMROOT": "", "PYTHONIOENCODING": "utf-8"},
    )


def test_exported_script_runs_standalone_minimal_workflow(tmp_path):
    """最小工作流：input → template → output，在干净环境（清空环境变量）可运行。"""
    proc = _run_exported(tmp_path, VALID_DSL)
    assert proc.returncode == 0, proc.stderr
    assert "处理：示例行" in proc.stdout
    assert "[succeeded] in1" in proc.stdout


def test_exported_script_honours_conditional_edges_and_skips(tmp_path):
    """条件边语义：filter 使下游不满足条件时记 skipped（与平台一致）。"""
    dsl = {
        "version": "1",
        "nodes": [
            {"id": "in1", "type": "input", "params": {
                "kind": "literal", "value": [{"name": "甲", "age": 34},
                                             {"name": "乙", "age": 20}]}},
            {"id": "f1", "type": "transform", "verb": "filter",
             "params": {"field": "age", "op": "gt", "value": 30}},
            {"id": "t1", "type": "transform", "verb": "template",
             "params": {"template": "{name} 合格"}},
            {"id": "out1", "type": "output", "params": {"format": "text"}},
        ],
        "edges": [{"from": "in1", "to": "f1"}, {"from": "f1", "to": "t1"},
                  {"from": "t1", "to": "out1"}],
    }
    proc = _run_exported(tmp_path, dsl)
    assert proc.returncode == 0, proc.stderr
    assert "甲 合格" in proc.stdout
    assert "乙" not in proc.stdout


def test_exported_script_json_output_and_payload_override(tmp_path):
    """--payload 覆盖 input 字面量；--json 输出机器可读结果。"""
    proc = _run_exported(tmp_path, VALID_DSL, "--json",
                         "--payload", '[{"value": "外部注入"}]')
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body["output"] == "处理：外部注入"
    assert [l["status"] for l in body["logs"]] == ["succeeded"] * 3


def test_exported_script_refuses_via_platform_with_placeholder(tmp_path):
    """占位符未替换时--via-platform 必须诚实失败（不静默发请求到假地址）。"""
    proc = _run_exported(tmp_path, VALID_DSL, "--via-platform")
    assert proc.returncode == 2
    assert "BASE_URL" in proc.stderr


def test_exported_script_rejects_bad_payload_json(tmp_path):
    proc = _run_exported(tmp_path, VALID_DSL, "--payload", "{not json")
    assert proc.returncode == 2
    assert "JSON" in proc.stderr


# =========================================================================== #
# 6. 图↔DSL 双向序列化
# =========================================================================== #


def test_graph_to_dsl_drops_coordinates():
    graph = FlowGraph(
        nodes=[
            GraphNode(id="in1", type="input", params={"kind": "literal", "value": 1}, x=10, y=20),
            GraphNode(id="out1", type="output", params={"format": "json"}, x=300, y=40),
        ],
        edges=[GraphEdge(from_id="in1", to_id="out1")],
    )
    doc = graph_to_dsl(graph)
    assert doc == {
        "version": "1",
        "nodes": [
            {"id": "in1", "type": "input", "params": {"kind": "literal", "value": 1}},
            {"id": "out1", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [{"from": "in1", "to": "out1"}],
    }
    assert '"x"' not in json.dumps(doc)
    assert '"y"' not in json.dumps(doc)


def test_dsl_to_graph_roundtrip_is_lossless_semantically():
    graph = dsl_to_graph(VALID_DSL)
    assert [n.id for n in graph.nodes] == ["in1", "tf1", "out1"]
    assert graph.nodes[1].verb == "template"
    # 无layout 时自动网格排布，节点坐标互不重叠
    assert len({(n.x, n.y) for n in graph.nodes}) == 3
    # 往返后语义一致
    assert graph_to_dsl(graph) == VALID_DSL


def test_dsl_to_graph_uses_provided_layout():
    graph = dsl_to_graph(VALID_DSL, {"in1": {"x": 11, "y": 22}})
    pos = {n.id: (n.x, n.y) for n in graph.nodes}
    assert pos["in1"] == (11.0, 22.0)


def test_dsl_to_graph_preserves_edge_conditions():
    doc = json.loads(json.dumps(VALID_DSL))
    doc["edges"][0]["condition"] = {"field": "kind", "op": "eq", "value": "literal"}
    graph = dsl_to_graph(doc)
    assert graph.edges[0].condition == {"field": "kind", "op": "eq", "value": "literal"}
    assert graph_to_dsl(graph) == doc


# =========================================================================== #
# 7. HTTP 契约
# ============================================================================ #


def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=__import__("datetime").timezone.utc)
    return value


from find_yourself.db.types import TZDateTime  # noqa: E402

TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)
    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool, future=True)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        owner_id="owner",
        # 不配模型：生成必须诚实 503
    )
    return create_app(session_maker=session_maker, settings=settings)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def test_endpoints_require_authentication(client):
    assert client.get("/api/workflow/status").status_code == 401
    assert client.post("/api/workflow/generate", json={"prompt": "x"}).status_code == 401


def test_generate_requires_csrf(client, headers):
    r = client.post("/api/workflow/generate", json={"prompt": "每天读文件并发邮件"})
    assert r.status_code == 403, r.text


def test_status_reports_model_not_configured_honestly(client, headers):
    body = client.get("/api/workflow/status").json()
    assert body["model_configured"] is False


def test_generate_returns_503_when_no_model_key(client, headers):
    """验收：无模型 key 时生成按钮诚实 503 并提示配置。"""
    r = client.post("/api/workflow/generate", json={"prompt": "每天读文件并发邮件"},
                    headers=headers)
    assert r.status_code == 503, r.text
    err = r.json()["error"]
    assert err["code"] == "model_not_configured"
    assert "FY_MODEL_API_KEY" in err["message"]


def test_export_endpoint_returns_runnable_script(client, headers):
    r = client.post("/api/workflow/export",
                    json={"dsl": VALID_DSL, "prompt": "每天读文件并发邮件"},
                    headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert BASE_URL_PLACEHOLDER in body["script"]
    # 纯中文需求派生不出 ASCII slug，如实回落到默认文件名
    assert body["filename"] == "fy_workflow.py"
    assert "每天读文件并发邮件" in body["script"]


def test_export_endpoint_derives_ascii_filename(client, headers):
    r = client.post("/api/workflow/export",
                    json={"dsl": VALID_DSL, "prompt": "daily file mail"},
                    headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "fy_workflow_daily_file_mail.py"


def test_export_endpoint_rejects_invalid_dsl_with_line_column(client, headers):
    """手改非法 DSL → 行列级错误（JSON 语法错误带真实行列）。"""
    broken = '{"version": "1", "nodes": [ , "edges": []}'
    r = client.post("/api/workflow/export", json={"dsl_text": broken}, headers=headers)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "workflow_dsl_invalid"
    assert isinstance(err["details"]["line"], int)
    assert isinstance(err["details"]["column"], int)


def test_export_endpoint_locates_semantic_error_line(client, headers):
    """语义错误也尽量定位到出错节点所在行。"""
    raw = json.dumps(
        {"version": "1",
         "nodes": [{"id": "a", "type": "input", "params": {"kind": "literal", "value": 1}},
                   {"id": "b", "type": "transform", "verb": "translate",
                    "params": {"template": "x"}}],
         "edges": [{"from": "a", "to": "b"}]},
        ensure_ascii=False, indent=2)
    r = client.post("/api/workflow/export", json={"dsl_text": raw}, headers=headers)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    # 平台校验器只说「必须是 ('map','filter','template')」，必须附上模型实际写出的
    # 非法取值 translate，否则用户无从下手改。
    assert "translate" in err["message"]
    # 真实定位：raw 第 13 行是 '      "id": "b",'，0-based 索引 12 → 第 13 列。
    # 这是实测值，不是拍脑袋的数。
    assert raw.splitlines()[err["details"]["line"] - 1].strip() == '"id": "b",'
    assert err["details"]["column"] == 13


def test_graph_from_dsl_and_to_dsl_roundtrip(client, headers):
    r = client.post("/api/workflow/graph/from-dsl", json={"dsl": VALID_DSL})
    assert r.status_code == 200, r.text
    graph = r.json()["graph"]
    assert [n["id"] for n in graph["nodes"]] == ["in1", "tf1", "out1"]

    r2 = client.post("/api/workflow/graph/to-dsl", json=graph)
    assert r2.status_code == 200, r2.text
    assert r2.json()["dsl"] == VALID_DSL


def test_graph_from_dsl_rejects_invalid_without_partial_parse(client, headers):
    """非法 DSL 整份拒绝，不返回半张图。"""
    bad = json.loads(json.dumps(VALID_DSL))
    bad["nodes"][1]["verb"] = "translate"
    r = client.post("/api/workflow/graph/from-dsl", json={"dsl": bad})
    assert r.status_code == 422, r.text
    assert "graph" not in r.json()


def test_graph_to_dsl_rejects_cycles(client, headers):
    cyclic = {
        "nodes": [
            {"id": "a", "type": "input", "params": {"kind": "literal", "value": 1}},
            {"id": "b", "type": "output", "params": {"format": "json"}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    r = client.post("/api/workflow/graph/to-dsl", json=cyclic)
    assert r.status_code == 422, r.text
    assert "存在环" in r.json()["error"]["message"]


def test_export_requires_exactly_one_dsl_source(client, headers):
    both = client.post("/api/workflow/export",
                       json={"dsl": VALID_DSL, "dsl_text": "{}"}, headers=headers)
    assert both.status_code == 422
    assert both.json()["error"]["code"] == "workflow_dsl_ambiguous"

    neither = client.post("/api/workflow/export", json={}, headers=headers)
    assert neither.status_code == 422
    assert neither.json()["error"]["code"] == "workflow_dsl_missing"


def test_dsl_canvas_tests_remain_green():
    """W5 只做追加，现有 DSL 画布接口语义未被改动（冒烟确认）。"""
    from find_yourself.services.dsl_canvas import run_dsl
    result = run_dsl(VALID_DSL)
    assert result.status == "succeeded"
    assert result.output == "处理：示例行"
