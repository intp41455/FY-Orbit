"""P1-08 SSE streaming channel tests (wire protocol, determinism, auth)."""

from __future__ import annotations

import json

import pytest

from find_yourself.services.streaming import StreamingService, stub_text
from find_yourself.runtime.gateway import MockModelProvider, ModelGateway

from helpers import login_owner


def parse_sse(raw: str) -> list[dict]:
    """Parse a raw text/event-stream payload into event dicts."""
    events: list[dict] = []
    heartbeats = 0
    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event_name = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                heartbeats += 1
                continue
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if event_name is None and not data_lines:
            continue
        events.append({
            "event": event_name,
            "data": json.loads(data_lines[0]) if data_lines else None,
            "_heartbeats": heartbeats,
        })
    return events


@pytest.fixture()
def owner(client):
    """Authenticated owner: session cookie on the client + CSRF headers."""
    return login_owner(client)


def test_sse_stream_shape_order_and_content(client, owner):
    prompt = "hello streaming world"
    with client.stream("POST", "/api/streaming/chat", json={"prompt": prompt},
                       headers=owner) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())

    events = parse_sse(raw)
    names = [e["event"] for e in events]

    assert names[0] == "message_start"
    assert names[-1] == "message_end"
    assert names[1:-1] and all(n == "delta" for n in names[1:-1])

    start = events[0]["data"]
    assert start["mode"] == "stub"
    assert start["model"] == "default"
    assert start["stream_id"]

    # delta chunks are ordered and reassemble to the deterministic stub text
    deltas = [e["data"] for e in events[1:-1]]
    assert [d["index"] for d in deltas] == list(range(len(deltas)))
    joined = "".join(d["text"] for d in deltas)
    assert joined == stub_text(prompt)

    end = events[-1]["data"]
    assert end["finish_reason"] == "stop"
    assert end["usage"]["total_tokens"] > 0
    assert end["stream_id"] == start["stream_id"]


def test_sse_contains_heartbeat_frames(client, owner):
    prompt = "heartbeat check " * 8  # long enough for >5 delta chunks
    with client.stream("POST", "/api/streaming/chat", json={"prompt": prompt},
                       headers=owner) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    assert ": heartbeat" in raw


def test_sse_stream_is_deterministic(client, owner):
    prompt = "determinism probe"
    bodies = []
    for _ in range(2):
        with client.stream("POST", "/api/streaming/chat", json={"prompt": prompt},
                           headers=owner) as resp:
            raw = "".join(resp.iter_text())
        events = parse_sse(raw)
        bodies.append("".join(e["data"]["text"] for e in events if e["event"] == "delta"))
    assert bodies[0] == bodies[1] == stub_text(prompt)


def test_streaming_requires_auth(client):
    r = client.post("/api/streaming/chat", json={"prompt": "x"})
    assert r.status_code == 401


def test_stub_disabled_without_provider_raises_envelope(client, owner, monkeypatch):
    monkeypatch.setenv("FY_SSE_STUB", "off")
    r = client.post("/api/streaming/chat", json={"prompt": "x"}, headers=owner)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "model_not_configured"


def test_gateway_mode_streams_provider_text():
    """Gateway mode: provider text is re-emitted as delta frames."""
    import asyncio

    from find_yourself.config import Settings
    from find_yourself.services.actor import Actor

    provider = MockModelProvider(default_response="chunk one chunk two chunk three done")
    gw = ModelGateway(provider=provider)
    svc = StreamingService(
        Settings(environment="test", session_secret="test-session-secret-that-is-long-enough-123456"),
        gateway=gw,
    )
    assert svc.resolve_mode() == "gateway"

    actor = Actor.service("svc-test", "tester")

    async def collect():
        return [f async for f in svc.stream_chat(
            actor, prompt="p", model="mock-deterministic", task_id="t1",
        )]

    frames = asyncio.run(collect())
    events = [e for f in frames for e in parse_sse(f) if e["event"]]
    names = [e["event"] for e in events]
    assert names[0] == "message_start" and names[-1] == "message_end"
    text = "".join(e["data"]["text"] for e in events if e["event"] == "delta")
    assert text == "chunk one chunk two chunk three done"
    assert events[-1]["data"]["finish_reason"] == "stop"
