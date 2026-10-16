"""G7 出站配置面：Hub kind ``a2a_agent``（登记 → 探活 → 派发闭环）。

覆盖三条落点：
1. **登记面**：``HUB_KINDS`` / ``KIND_GROUP`` / ``DEFAULT_SECRET_FIELDS`` /
   DB CHECK 真的放行；缺地址与内网地址在登记阶段就被拦（SSRF 与 webhook/market-source
   同一套口径，不另立信任面）。
2. **探活面**：真 HTTP GET Agent Card + 真打一次 ``message/send`` 参数校验。
   **不许造假绿** —— 卡片取不到、方法未实现、卡片指向别的主机、未配置，四种都必须
   是 ``ok=False`` 且原因可区分。
3. **派发面**：复用 ``A2AClient``；信任 / 超时 / 未配置 / RPC 错误各给**不同**错误码。

不打真网：一律注入 ``httpx.MockTransport``（与 test_hub_adapters 同一套可注入约定）。

另含迁移回归：降级-升级往返后 ``ix_hub_connection_owner_kind`` 必须仍在
（漏声明复合索引会让索引静默消失，随后 0024 的 downgrade 直接 ``no such index``）。
"""

from __future__ import annotations

import json
import os
import tempfile

import httpx
import pytest
from sqlalchemy import create_engine, inspect

from find_yourself.db.hub_constants import GROUP_TOOL, HUB_KINDS, KIND_A2A_AGENT, KIND_GROUP
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub import KIND_A2A_AGENT as REEXPORTED_A2A
from find_yourself.services.hub import adapters as A
from find_yourself.services.hub.connections import DEFAULT_SECRET_FIELDS, HubService

BASE = "https://agent.example.com"
CARD_PATH = "/.well-known/agent.json"
RPC_PATH = "/a2a/v1/jsonrpc"

CARD = {
    "name": "Remote Planner",
    "version": "1.2.3",
    "protocolVersion": "0.3.0",
    "url": BASE + RPC_PATH,
    "capabilities": {"streaming": False, "pushNotifications": False},
    "skills": [
        {"id": "plan", "description": "生成执行计划", "tags": ["planning"]},
        {"id": "review", "description": "审阅方案", "tags": ["review", "qa"]},
    ],
}


def _rpc(req_id, result=None, error=None) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result if result is not None else {}
    return body


def a2a_transport(*, card=CARD, card_status=200, send_result=None, send_error=None,
                  record=None):
    """假 A2A 对端：Agent Card GET + JSON-RPC POST。

    ``send_error=(-32602, "Invalid params")`` 是**合规**服务端的真实回答
    （空消息在参数校验处被拒），也是探活判绿的那条路径。
    """
    sent: list[httpx.Request] = record if record is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        path = request.url.path
        if path == CARD_PATH:
            if card_status != 200:
                return httpx.Response(card_status, text="not found")
            return httpx.Response(200, json=card)
        if path == RPC_PATH:
            payload = json.loads(request.content.decode("utf-8"))
            if send_error is not None:
                code, message = send_error
                return httpx.Response(
                    200, json=_rpc(payload.get("id"), error={"code": code, "message": message}))
            return httpx.Response(200, json=_rpc(
                payload.get("id"),
                result=send_result if send_result is not None
                else {"id": "task-1", "status": {"state": "submitted"}},
            ))
        return httpx.Response(404, json={"error": "no such path"})

    return httpx.MockTransport(handler), sent


def adapter(config=None, **kwargs) -> A.A2AAgentAdapter:
    cfg = {"kind": KIND_A2A_AGENT, "base_url": BASE}
    cfg.update(config or {})
    return A.A2AAgentAdapter(cfg, **kwargs)


def timeout_transport(request: httpx.Request) -> httpx.Response:
    raise httpx.TimeoutException("too slow", request=request)


# --------------------------------------------------------------------------- #
# 1) 登记面：常量 / 分组 / 凭证字段 / DB CHECK
# --------------------------------------------------------------------------- #


def test_a2a_agent_is_in_kind_whitelist_and_re_exported():
    assert KIND_A2A_AGENT == "a2a_agent"
    assert KIND_A2A_AGENT in HUB_KINDS
    assert REEXPORTED_A2A == KIND_A2A_AGENT  # services.hub 重导出不能漏
    assert KIND_GROUP[KIND_A2A_AGENT] == GROUP_TOOL


def test_a2a_agent_uses_existing_credential_field_convention():
    """复用 market_source/http_webhook 的 api_token / authorization，不新造一套。"""
    assert DEFAULT_SECRET_FIELDS[KIND_A2A_AGENT] == ("api_token", "authorization")
    assert DEFAULT_SECRET_FIELDS[KIND_A2A_AGENT] == DEFAULT_SECRET_FIELDS["market_source"]


def test_db_check_constraint_accepts_a2a_agent_row(session, owner):
    """CHECK 必须真的放行 —— 否则登记 INSERT 会被数据库层拒绝。"""
    hub = HubService(session)
    created = hub.create_connection(owner, {
        "name": "remote-planner",
        "kind": KIND_A2A_AGENT,
        "config": {"base_url": BASE},
    })
    assert created["kind"] == KIND_A2A_AGENT
    assert created["group"] == GROUP_TOOL
    # 真的落库并可按 kind 读回（不是只在内存里过了一遍校验）
    listed = hub.list_connections(owner, kind=KIND_A2A_AGENT)
    assert [c["name"] for c in listed] == ["remote-planner"]


def test_create_requires_base_url(session, owner):
    hub = HubService(session)
    with pytest.raises(ValidationFailed) as err:
        hub.create_connection(owner, {"name": "no-endpoint", "kind": KIND_A2A_AGENT,
                                      "config": {}})
    assert err.value.code == "hub_a2a_endpoint_required"


def test_private_endpoint_rejected_at_registration(session, owner):
    """SSRF 与其它 outbound HTTP kind 同一套口径，不给 A2A 开内网后门。"""
    with pytest.raises(ValidationFailed) as err:
        HubService(session).create_connection(owner, {
            "name": "local-a2a", "kind": KIND_A2A_AGENT,
            "config": {"base_url": "http://127.0.0.1:8080"},
        })
    assert err.value.code == "hub_ssrf_blocked"


def test_private_endpoint_allowed_with_env_override(session, owner, monkeypatch):
    monkeypatch.setenv("FY_HUB_ALLOW_PRIVATE", "1")
    conn = HubService(session).create_connection(owner, {
        "name": "local-a2a", "kind": KIND_A2A_AGENT,
        "config": {"base_url": "http://127.0.0.1:8080"},
    })
    assert conn["config"]["base_url"] == "http://127.0.0.1:8080"


def test_url_alias_resolves_to_same_endpoint():
    """``url`` 只是 ``base_url`` 的别名，必须经同一个归一函数，不各读一半。"""
    assert A.resolve_a2a_base_url({"base_url": BASE + "/", "url": "https://ignored.test"}) == BASE
    assert A.resolve_a2a_base_url({"url": BASE}) == BASE
    with pytest.raises(ValidationFailed) as err:
        A.resolve_a2a_base_url({})
    assert err.value.code == "hub_a2a_endpoint_required"


# --------------------------------------------------------------------------- #
# 2) 探活面：真调用，不造假绿
# --------------------------------------------------------------------------- #


def test_health_fetches_real_card_and_probes_message_send():
    transport, sent = a2a_transport(send_error=(-32602, "Invalid params"))
    report = adapter(transport=transport).health(timeout_seconds=3.0)

    assert report.ok is True
    # 真发过两个请求：一个 GET 卡片、一个 POST JSON-RPC
    assert [(r.method, r.url.path) for r in sent] == [
        ("GET", CARD_PATH), ("POST", RPC_PATH)]
    # 判绿依据是实测到的 -32602，而不是"配置里有 URL"
    assert "-32602" in report.detail
    # 能力来自卡片真实 skills（不是配置里手写的声明）
    names = [c.name for c in report.capabilities]
    assert "a2a.skill:plan" in names and "a2a.skill:review" in names
    assert any(c.description == "生成执行计划" for c in report.capabilities)


def test_health_probe_sends_empty_message_with_role():
    """探活发的是**空消息**：证明方法被实现，且不产生真实派发。"""
    transport, sent = a2a_transport(send_error=(-32602, "Invalid params"))
    adapter(transport=transport).health()
    payload = json.loads(sent[1].content.decode("utf-8"))
    assert payload["method"] == "message/send"
    assert payload["params"]["message"] == {"role": "user", "parts": []}


def test_health_reports_not_configured_instead_of_green():
    """未配置必须显式 BLOCKED_EXTERNAL 类错误，绝不返回假数据。"""
    report = A.A2AAgentAdapter({"kind": KIND_A2A_AGENT}).health()
    assert report.ok is False
    assert "hub_a2a_endpoint_required" in report.detail
    assert report.capabilities == []


def test_health_fails_when_card_unreachable():
    transport, _ = a2a_transport(card_status=404)
    report = adapter(transport=transport).health()
    assert report.ok is False
    assert "404" in report.detail


def test_health_fails_when_message_send_not_implemented():
    """卡片能取到 ≠ 能接任务。-32601 必须红，且原因可区分。"""
    transport, _ = a2a_transport(send_error=(-32601, "Method not found"))
    report = adapter(transport=transport).health()
    assert report.ok is False
    assert "hub_a2a_method_unsupported" in report.detail


def test_health_fails_when_upstream_refuses():
    transport, _ = a2a_transport(send_error=(-32001, "upstream not configured"))
    report = adapter(transport=transport).health()
    assert report.ok is False
    assert "hub_a2a_upstream_refused" in report.detail
    assert "-32001" in report.detail


def test_health_fails_on_timeout_with_distinguishable_code():
    report = adapter(transport=httpx.MockTransport(timeout_transport)).health()
    assert report.ok is False
    assert "hub_a2a_timeout" in report.detail


def test_health_rejects_card_pointing_at_another_host():
    """Agent Card 是远程不可信输入：它不能把我们的任务指到别的主机。"""
    evil = {**CARD, "url": "https://evil.example.org" + RPC_PATH}
    transport, _ = a2a_transport(card=evil, send_error=(-32602, "Invalid params"))
    report = adapter(transport=transport).health()
    assert report.ok is False
    assert "hub_a2a_endpoint_mismatch" in report.detail
    assert "evil.example.org" in report.detail


def test_health_blocks_private_endpoint():
    report = adapter({"base_url": "http://127.0.0.1:9999"}).health()
    assert report.ok is False
    assert "hub_ssrf_blocked" in report.detail


def test_expected_name_pins_the_remote_identity():
    """expected_name 不匹配必须红 —— 防止地址被换成另一个 Agent。"""
    report = adapter({"expected_name": "Someone Else"},
                     transport=a2a_transport(send_error=(-32602, "x"))[0]).health()
    assert report.ok is False
    assert "mismatch" in report.detail.lower()

    report = adapter({"expected_name": "Remote Planner"},
                     transport=a2a_transport(send_error=(-32602, "x"))[0]).health()
    assert report.ok is True


def test_probe_can_be_disabled_explicitly_and_says_so():
    """关掉实测是有代价的，探活文案必须如实说明"本次未验证派发能力"。"""
    transport, sent = a2a_transport(send_error=(-32602, "Invalid params"))
    report = adapter({"probe_message_send": "false"}, transport=transport).health()
    assert report.ok is True
    assert "未验证派活能力" in report.detail or "未验证派发能力" in report.detail
    assert [r.url.path for r in sent] == [CARD_PATH]  # 确实没打 JSON-RPC


def test_capabilities_without_probe_are_declared_only():
    """没取到卡片就不编技能：能力里不能出现没真见过的 skill。"""
    caps = adapter().capabilities()
    assert [c.name for c in caps] == ["a2a.send"]


# --------------------------------------------------------------------------- #
# 3) 派发面：复用 A2AClient，错误码可区分
# --------------------------------------------------------------------------- #


def test_invoke_sends_message_and_returns_real_task():
    transport, sent = a2a_transport(
        send_result={"id": "task-42", "status": {"state": "submitted"}})
    result = adapter(transport=transport).invoke(
        A.InvokeCall(action="invoke", params={"prompt": "帮我排个期"}, timeout_seconds=5.0))

    assert result.ok is True
    assert result.output["id"] == "task-42"
    assert result.meta["task_id"] == "task-42"
    assert result.meta["method"] == "message/send"
    # 派发**不**先拉卡片：卡片是探活的活，派发直接打 JSON-RPC（sent[0] 就是它）。
    assert [r.url.path for r in sent] == [RPC_PATH]
    payload = json.loads(sent[0].content.decode("utf-8"))
    assert payload["params"]["message"]["parts"][0]["text"] == "帮我排个期"


def test_invoke_rejects_empty_prompt():
    transport, sent = a2a_transport()
    result = adapter(transport=transport).invoke(A.InvokeCall(action="send", params={}))
    assert result.ok is False
    assert "hub_a2a_missing_prompt" in result.error
    assert sent == []  # 空消息绝不真发出去


def test_invoke_rejects_unknown_action():
    result = adapter(transport=httpx.MockTransport(timeout_transport)).invoke(
        A.InvokeCall(action="explode"))
    assert result.ok is False
    assert "hub_unsupported_action" in result.error


def test_invoke_task_and_cancel_actions():
    transport, sent = a2a_transport(send_result={"id": "t1", "status": {"state": "completed"}})
    ad = adapter(transport=transport)
    got = ad.invoke(A.InvokeCall(action="get_task", params={"task_id": "t1"}))
    assert got.ok is True and got.output["id"] == "t1"
    cancelled = ad.invoke(A.InvokeCall(action="cancel", params={"task_id": "t1"}))
    assert cancelled.ok is True
    methods = [json.loads(r.content.decode("utf-8"))["method"] for r in sent]
    assert methods == ["tasks/get", "tasks/cancel"]


def test_invoke_task_action_requires_task_id():
    result = adapter(transport=httpx.MockTransport(timeout_transport)).invoke(
        A.InvokeCall(action="get_task", params={}))
    assert result.ok is False
    assert "hub_a2a_task_id_required" in result.error


def test_invoke_not_configured_code():
    result = A.A2AAgentAdapter({"kind": KIND_A2A_AGENT}).invoke(
        A.InvokeCall(action="invoke", params={"prompt": "hi"}))
    assert result.ok is False
    assert "hub_a2a_endpoint_required" in result.error


def test_invoke_timeout_code():
    result = adapter(transport=httpx.MockTransport(timeout_transport)).invoke(
        A.InvokeCall(action="send", params={"prompt": "hi"}))
    assert result.ok is False
    assert "hub_a2a_timeout" in result.error


def test_invoke_rpc_error_keeps_code():
    transport, _ = a2a_transport(send_error=(-32002, "Agent is draining"))
    result = adapter(transport=transport).invoke(
        A.InvokeCall(action="send", params={"prompt": "hi"}))
    assert result.ok is False
    assert "hub_a2a_rpc_error" in result.error
    assert result.meta["rpc_code"] == -32002


def test_invoke_untrusted_endpoint_code():
    """信任注册表不含该端点时必须报 hub_a2a_untrusted_endpoint，不能静默放行。"""
    from find_yourself.adapters.a2a import A2AClient, TrustedEndpointRegistry

    ad = adapter()
    empty_client = A2AClient(
        registry=TrustedEndpointRegistry(),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=_rpc(None, result={})))),
    )
    ad._client = lambda timeout=None: (  # type: ignore[method-assign]
        empty_client, type("EP", (), {"base_url": BASE})())
    result = ad.invoke(A.InvokeCall(action="send", params={"prompt": "hi"}))
    assert result.ok is False
    assert "hub_a2a_untrusted_endpoint" in result.error


def test_invoke_sends_bearer_token_from_hub_credential_field():
    """Hub 的 api_token / authorization 映射成 A2A 的 Authorization: Bearer。

    注意：``A2AClient.get_agent_card`` 不带认证头（既有实现），只有 JSON-RPC
    调用带 —— 所以断言落在 POST 上。卡片需要鉴权的对端因此仍会探活失败，
    这是 A2AClient 的既有边界，不在本适配器里另造一套。
    """
    transport, sent = a2a_transport(send_error=(-32602, "Invalid params"))
    adapter({"api_token": "hub-token"}, transport=transport).invoke(
        A.InvokeCall(action="send", params={"prompt": "hi"}))
    assert sent[0].headers.get("authorization") == "Bearer hub-token"


# --------------------------------------------------------------------------- #
# 4) 闭环：登记 → 探活 → 派发（走真实 HubService，注入假传输层）
# --------------------------------------------------------------------------- #


def test_full_loop_register_probe_dispatch(session, owner):
    hub = HubService(session)
    transport, _ = a2a_transport(send_error=(-32602, "Invalid params"))

    created = hub.create_connection(owner, {
        "name": "remote-planner",
        "kind": KIND_A2A_AGENT,
        "description": "外部规划 Agent",
        "config": {"base_url": BASE, "expected_name": "Remote Planner"},
        "credentials": {"api_token": "tok-123"},
        "secret_fields": ["api_token"],
    })
    assert created["state"] == "active"

    probe = hub.health_check(owner, created["id"], transport=transport)
    assert probe["report"]["ok"] is True
    assert probe["connection"]["health"]["ok"] is True
    assert probe["connection"]["state"] == "active"
    # 能力真落库了（来自卡片）
    assert "a2a.skill:plan" in {c["name"] for c in probe["connection"]["capabilities"]}

    transport2, _ = a2a_transport(
        send_result={"id": "task-7", "status": {"state": "submitted"}})
    called = hub.invoke(owner, created["id"], action="send", params={"prompt": "排个期"},
                        transport=transport2)
    assert called["result"]["ok"] is True
    assert called["result"]["output"]["id"] == "task-7"
    # 凭证明文永不出现在对外结构里
    assert "tok-123" not in str(called)


def test_failed_probe_marks_connection_error(session, owner):
    hub = HubService(session)
    created = hub.create_connection(owner, {
        "name": "dead-agent", "kind": KIND_A2A_AGENT,
        "config": {"base_url": BASE},
    })
    probe = hub.health_check(owner, created["id"],
                             transport=a2a_transport(card_status=500)[0])
    assert probe["report"]["ok"] is False
    assert probe["connection"]["state"] == "error"
    # 失败探测不得写进能力（不能出现没真见过的 skill）
    assert probe["connection"]["capabilities"] == []


def test_build_adapter_dispatches_to_a2a_agent():
    got = A.build_adapter(KIND_A2A_AGENT, {"base_url": BASE})
    assert isinstance(got, A.A2AAgentAdapter)
    assert got.kind == KIND_A2A_AGENT


# --------------------------------------------------------------------------- #
# 5) 迁移回归：降级-升级往返后复合索引必须仍在
# --------------------------------------------------------------------------- #


@pytest.fixture()
def temp_db_url():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield f"sqlite:///{path}"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _alembic(url: str):
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _indexes(url: str) -> set[str]:
    eng = create_engine(url)
    try:
        return {ix["name"] for ix in inspect(eng).get_indexes("hub_connections")}
    finally:
        eng.dispose()


def test_migration_roundtrip_keeps_composite_index(temp_db_url):
    """上一版翻车点：copy_from 的 _table() 漏声明 ix_hub_connection_owner_kind →
    降级后索引静默消失，0024 的 downgrade 直接 no such index。"""
    from alembic import command

    cfg = _alembic(temp_db_url)
    command.upgrade(cfg, "head")
    assert "ix_hub_connection_owner_kind" in _indexes(temp_db_url)

    # 一路降到 0048：0050 与 0049 都要能把索引原样重建回来
    command.downgrade(cfg, "0048_dsl_draft_version")
    assert "ix_hub_connection_owner_kind" in _indexes(temp_db_url)

    command.upgrade(cfg, "head")
    assert "ix_hub_connection_owner_kind" in _indexes(temp_db_url)

    eng = create_engine(temp_db_url)
    try:
        checks = " ".join(
            c.get("sqltext") or ""
            for c in inspect(eng).get_check_constraints("hub_connections"))
        assert "a2a_agent" in checks
    finally:
        eng.dispose()


def test_migration_skips_when_check_already_has_new_value(temp_db_url):
    """幂等护栏 A：全新库由 create_all 建表（CHECK 已含 8 值）→ 迁移必须跳过。"""
    from alembic import command

    import find_yourself.db.workbench_models  # noqa: F401
    from find_yourself.db.base import Base

    eng = create_engine(temp_db_url)
    Base.metadata.create_all(eng)
    eng.dispose()
    command.stamp(_alembic(temp_db_url), "0049_hub_market_source")

    cfg = _alembic(temp_db_url)
    command.upgrade(cfg, "head")  # 不抛异常即通过
    assert "ix_hub_connection_owner_kind" in _indexes(temp_db_url)


def test_migration_skips_when_table_absent(temp_db_url):
    """幂等护栏 B：表不存在直接跳过（不是炸掉）。"""
    from alembic import command

    cfg = _alembic(temp_db_url)
    command.stamp(cfg, "0049_hub_market_source")
    command.upgrade(cfg, "head")  # 空库上只有 0050 会跑，表不存在 → 直接 return


def test_downgrade_refuses_while_a2a_rows_exist(temp_db_url):
    """删用户数据不如不降级：残留 a2a_agent 行时降级必须明确拒绝。"""
    from alembic import command

    cfg = _alembic(temp_db_url)
    command.upgrade(cfg, "head")
    eng = create_engine(temp_db_url)
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO hub_connections (id, owner_id, name, kind, state, version,"
                " created_at, updated_at) VALUES"
                " ('c1', 'owner-1', 'a2a-peer', 'a2a_agent', 'active', 1,"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    finally:
        eng.dispose()
    with pytest.raises(RuntimeError) as err:
        command.downgrade(cfg, "0048_dsl_draft_version")
    assert "a2a_agent" in str(err.value)
