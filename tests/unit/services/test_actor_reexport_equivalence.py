"""服务层 actor 兼容垫片的 re-export 等价性单测（Actor 身份契约下沉 L1 回归护栏）。

``Actor`` 实体已下沉到 L1 ``find_yourself.identity``，``find_yourself.services.actor``
退化为纯 re-export 垫片。本测试锁死两件事：

1. 垫片导出的 ``Actor`` 与 L1 的 ``Actor`` **是同一对象**（``is``），不是另建副本；
2. 下沉过程中 ``Actor`` 的类定义（frozen 语义 / 11 个字段默认值 / 构造器 /
   全部判定方法）一字未改，``PermissionDenied`` / ``Unauthenticated`` 仍来自 L1
   ``find_yourself.contracts``。

导入方式刻意做成「缺失即断言失败」而非导入期 ImportError：这样在尚未下沉的老代码上
本文件也能正常收集，并以清晰的 AssertionError 报红。
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from datetime import timedelta

import pytest

from find_yourself import contracts
from find_yourself.db.types import utcnow
from find_yourself.services import actor as shim
from find_yourself.services import errors as errors_shim

try:  # 下沉前 find_yourself.identity 不存在 → 记为 None，由断言给出可读失败
    identity = importlib.import_module("find_yourself.identity")
except ModuleNotFoundError:  # pragma: no cover - 仅在下沉前触发
    identity = None

_MISSING = "L1 模块 find_yourself.identity 尚未建立：Actor 实体还没有下沉到 L1（ADR-0001 D3/D6）"


def _l1_actor_cls() -> type:
    """取出 L1 的 ``Actor`` 类；缺失时以断言失败而非 AttributeError 报红。"""
    assert identity is not None, _MISSING
    assert hasattr(identity, "Actor"), "L1 find_yourself.identity 未定义 Actor 实体"
    return identity.Actor


# --------------------------------------------------------------------------
# 1. 实体归属与 re-export 等价性
# --------------------------------------------------------------------------
def test_l1_identity_module_exists():
    """L1 必须真实存在身份契约单源模块（下沉前此断言为红）。"""
    assert identity is not None, _MISSING


def test_shim_reexports_the_very_same_object():
    """垫片导出的是 L1 的同一个类对象，不是副本。"""
    l1_actor = _l1_actor_cls()
    assert shim.Actor is l1_actor


def test_actor_module_points_at_l1():
    """``__module__`` 必须指向 L1，证明实体真的搬走了而不是复制了一份。"""
    _l1_actor_cls()
    assert shim.Actor.__module__ == "find_yourself.identity"


def test_shim_all_covers_actor():
    """Actor 必须出现在 ``__all__`` 里，否则既有调用方 ImportError。"""
    assert "Actor" in getattr(shim, "__all__", []), f"垫片 __all__ 缺少 Actor：{getattr(shim, '__all__', [])}"


def test_identity_module_does_not_depend_on_services():
    """L1 身份模块不得再回头 import L2.1 的 services 包。"""
    assert identity is not None, _MISSING
    imported = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(identity)))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name and "services" in name for name in imported), (
        f"L1 identity 反向依赖 services：{sorted(n for n in imported if n)}"
    )


def test_errors_used_by_actor_come_from_l1_contracts():
    """Actor 抛的异常类必须来自 L1 contracts，而不是 services.errors 垫片。"""
    _l1_actor_cls()
    assert errors_shim.PermissionDenied is contracts.PermissionDenied
    assert errors_shim.Unauthenticated is contracts.Unauthenticated
    assert contracts.PermissionDenied.__module__ == "find_yourself.contracts"
    assert contracts.Unauthenticated.__module__ == "find_yourself.contracts"


# --------------------------------------------------------------------------
# 2. 下沉过程中类定义语义未变
# --------------------------------------------------------------------------
def test_actor_is_frozen_dataclass():
    Actor = _l1_actor_cls()
    assert Actor.__dataclass_params__.frozen is True
    actor = Actor.owner("u1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        actor.owner_id = "u2"


def test_all_field_names_survive_the_move():
    Actor = _l1_actor_cls()
    assert [f.name for f in dataclasses.fields(Actor)] == [
        "subject_type",
        "owner_id",
        "service_id",
        "service_kind",
        "csrf_token",
        "bound_domains",
        "bound_task_id",
        "allowed_tools",
        "expires_at",
        "max_budget_cents",
    ]


def test_field_defaults_survive_the_move():
    """除 subject_type 外的 9 个字段默认值逐个锁死。"""
    Actor = _l1_actor_cls()
    actor = Actor(subject_type="owner")
    assert actor.owner_id == ""
    assert actor.service_id == ""
    assert actor.service_kind == ""
    assert actor.csrf_token == ""
    assert actor.bound_domains == ()
    assert actor.bound_task_id is None
    assert actor.allowed_tools == ()
    assert actor.expires_at is None
    assert actor.max_budget_cents is None


# --------------------------------------------------------------------------
# 3. 构造器与判定方法行为
# --------------------------------------------------------------------------
def test_owner_constructor():
    Actor = _l1_actor_cls()
    actor = Actor.owner("u1", "tok")
    assert actor.subject_type == "owner"
    assert actor.owner_id == "u1"
    assert actor.csrf_token == "tok"
    assert actor.service_id == ""
    # 默认 csrf_token 为空串
    assert Actor.owner("u2").csrf_token == ""


def test_service_constructor():
    Actor = _l1_actor_cls()
    actor = Actor.service("s1", "worker", ["d1", "d2"], task_id="t1")
    assert actor.subject_type == "service"
    assert actor.service_id == "s1"
    assert actor.service_kind == "worker"
    assert actor.bound_domains == ("d1", "d2")
    assert actor.bound_task_id == "t1"
    assert actor.allowed_tools == ()
    assert actor.expires_at is None
    assert actor.max_budget_cents is None

    full = Actor.service(
        "s2", "tool_gateway", ["d"], task_id="t2",
        allowed_tools=["a", "b"], max_budget_cents=500,
    )
    assert full.allowed_tools == ("a", "b")
    assert full.max_budget_cents == 500


def test_require_owner_allows_owner_only():
    Actor = _l1_actor_cls()
    assert Actor.owner("u1").require_owner() is None
    with pytest.raises(contracts.PermissionDenied) as exc:
        Actor.service("s1", "worker").require_owner()
    assert exc.value.code == "owner_only"
    assert exc.value.http_status == 403


def test_require_authenticated():
    Actor = _l1_actor_cls()
    assert Actor.owner("u1").require_authenticated() is None
    assert Actor.service("s1", "worker").require_authenticated() is None
    with pytest.raises(contracts.Unauthenticated) as exc:
        Actor(subject_type="anonymous").require_authenticated()
    assert exc.value.code == "unauthenticated"
    assert exc.value.http_status == 401

    expired = Actor.service("s1", "worker", expires_at=utcnow() - timedelta(seconds=1))
    with pytest.raises(contracts.Unauthenticated) as exc2:
        expired.require_authenticated()
    assert exc2.value.code == "service_expired"


def test_is_expired():
    Actor = _l1_actor_cls()
    assert Actor.owner("u1").is_expired() is False
    past = Actor(subject_type="owner", owner_id="u1", expires_at=utcnow() - timedelta(seconds=1))
    future = Actor(subject_type="owner", owner_id="u1", expires_at=utcnow() + timedelta(hours=1))
    assert past.is_expired() is True
    assert future.is_expired() is False
    # expires_at 恰为当前时间也算过期（<= 而非 <）
    assert Actor(subject_type="owner", expires_at=utcnow()).is_expired() is True


def test_can_access_domain_and_require_domain():
    Actor = _l1_actor_cls()
    owner = Actor.owner("u1")
    assert owner.can_access_domain("anything") is True
    assert owner.require_domain("anything") is None

    svc = Actor.service("s1", "worker", ["alpha"])
    assert svc.can_access_domain("alpha") is True
    assert svc.can_access_domain("beta") is False
    assert svc.require_domain("alpha") is None
    with pytest.raises(contracts.PermissionDenied) as exc:
        svc.require_domain("beta")
    assert exc.value.code == "domain_forbidden"
    assert exc.value.http_status == 403


def test_can_use_tool_and_require_tool():
    Actor = _l1_actor_cls()
    owner = Actor.owner("u1")
    assert owner.can_use_tool("anything") is True
    assert owner.require_tool("anything") is None

    svc = Actor.service("s1", "worker", allowed_tools=["read"])
    assert svc.can_use_tool("read") is True
    assert svc.can_use_tool("write") is False
    assert svc.require_tool("read") is None
    with pytest.raises(contracts.PermissionDenied) as exc:
        svc.require_tool("write")
    assert exc.value.code == "tool_forbidden"
    assert exc.value.http_status == 403

    wildcard = Actor.service("s2", "worker", allowed_tools=["*"])
    assert wildcard.can_use_tool("anything") is True
    assert wildcard.require_tool("anything") is None

    empty = Actor.service("s3", "worker")
    assert empty.can_use_tool("read") is False


def test_require_task():
    Actor = _l1_actor_cls()
    assert Actor.owner("u1").require_task("t1") is None

    unbound = Actor.service("s1", "worker")
    assert unbound.require_task("t1") is None

    bound = Actor.service("s2", "worker", task_id="t1")
    assert bound.require_task("t1") is None
    with pytest.raises(contracts.PermissionDenied) as exc:
        bound.require_task("t2")
    assert exc.value.code == "task_mismatch"
    assert exc.value.http_status == 403


def test_capability_subject():
    Actor = _l1_actor_cls()
    assert Actor.owner("u1").capability_subject() == "owner:u1"
    assert Actor.owner("").capability_subject() == "owner:"
    assert Actor.service("s1", "worker").capability_subject() == "service:s1"
    assert Actor.service("", "worker").capability_subject() == "service:"


def test_actor_equality_and_repr_survive():
    """frozen dataclass 的 eq/hash 语义未变。"""
    Actor = _l1_actor_cls()
    assert Actor.owner("u1") == Actor.owner("u1")
    assert Actor.owner("u1") != Actor.owner("u2")
    assert hash(Actor.owner("u1")) == hash(Actor.owner("u1"))
    assert Actor.__qualname__ == "Actor"