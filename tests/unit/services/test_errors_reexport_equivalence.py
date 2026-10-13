"""服务层 errors 兼容垫片的 re-export 等价性单测（异常类下沉 L1 回归护栏）。

异常类实体已下沉到 L1 契约单源 ``find_yourself.contracts``，
``find_yourself.services.errors`` 退化为纯 re-export 垫片。本测试锁死两件事：

1. 垫片导出的 6 个类与 L1 契约里的类**是同一对象**（``is``），不是另建一份副本；
2. 下沉过程中类定义（``http_status`` / ``default_code`` / ``__init__`` 三个分支）
   一字未改，语义与 ``__module__`` 均指向 L1。
"""

from __future__ import annotations

import pytest

from find_yourself import contracts
from find_yourself.services import errors as shim

_ERROR_NAMES = (
    "Conflict",
    "DomainError",
    "NotFound",
    "PermissionDenied",
    "Unauthenticated",
    "ValidationFailed",
)


def test_contracts_owns_the_error_class_entities():
    """L1 契约必须是异常类的唯一实体所在地（下沉前此断言为红）。"""
    missing = [name for name in _ERROR_NAMES if not hasattr(contracts, name)]
    assert not missing, f"L1 contracts 缺少异常类实体：{missing}"


@pytest.mark.parametrize("name", _ERROR_NAMES)
def test_shim_reexports_the_very_same_objects(name):
    """垫片导出的是 L1 契约里的同一个类对象。"""
    assert hasattr(contracts, name), f"L1 contracts 未定义 {name}"
    assert getattr(shim, name) is getattr(contracts, name)
    assert getattr(shim, name).__module__ == "find_yourself.contracts"


@pytest.mark.parametrize("name", _ERROR_NAMES)
def test_shim_all_covers_every_name(name):
    """6 个名字必须全部出现在 ``__all__`` 里，否则调用方 ImportError。"""
    exported = getattr(shim, "__all__", [])
    assert name in exported, f"垫片 __all__ 缺少 {name}：{exported}"


def test_hierarchy_and_defaults_survive_the_move():
    assert issubclass(shim.DomainError, Exception)
    for name in _ERROR_NAMES:
        if name != "DomainError":
            assert issubclass(getattr(shim, name), shim.DomainError)
    assert (shim.DomainError.http_status, shim.DomainError.default_code) == (409, "domain_error")
    assert (shim.NotFound.http_status, shim.NotFound.default_code) == (404, "not_found")
    assert (shim.PermissionDenied.http_status, shim.PermissionDenied.default_code) == (403, "permission_denied")
    assert (shim.Unauthenticated.http_status, shim.Unauthenticated.default_code) == (401, "unauthenticated")
    assert (shim.Conflict.http_status, shim.Conflict.default_code) == (409, "conflict")
    assert (shim.ValidationFailed.http_status, shim.ValidationFailed.default_code) == (422, "validation_failed")


def test_init_three_branches():
    # 分支 1：message 省略 → message 取 code，code 回落 default_code
    only_code = shim.NotFound("task_missing")
    assert only_code.code == "not_found"
    assert only_code.message == "task_missing"
    assert only_code.http_status == 404

    # 分支 2：显式 message → code 保留传入值
    both = shim.Conflict("already_running", "already_running: task already running")
    assert both.code == "already_running"
    assert both.message == "already_running: task already running"

    # 分支 3：显式 http_status 覆盖类默认值，但不污染类本身
    overridden = shim.Conflict("x", "y", http_status=503)
    assert overridden.http_status == 503
    assert shim.Conflict.http_status == 409

    assert str(both) == "already_running: task already running"