"""画布文档构造器（dsl_sdk 测试包共用；唯一命名避免模块名冲突）。"""

from __future__ import annotations

from typing import Any


def doc_of(nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    return {"version": "1", "nodes": nodes, "edges": edges}


def lit(value: Any, node_id: str = "src") -> dict[str, Any]:
    return {"id": node_id, "type": "input",
            "params": {"kind": "literal", "value": value}}


def xf(node_id: str, verb: str, **params: Any) -> dict[str, Any]:
    return {"id": node_id, "type": "transform", "verb": verb, "params": params}


def out(node_id: str = "out", fmt: str = "json") -> dict[str, Any]:
    return {"id": node_id, "type": "output", "params": {"format": fmt}}
