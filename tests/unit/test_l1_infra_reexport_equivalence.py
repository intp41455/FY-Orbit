"""5 个基础设施模块整簇下沉 L1 的 re-export 等价性单测（ADR-0001 D3/D6 回归护栏）。

``hashing / anchors / audit / budget / offline`` 的实体已下沉到 ``find_yourself``
顶层（L1 契约与基础设施），``services/`` 侧的 5 个原文件退化为纯 re-export 垫片。
本测试锁死六件事：

1. **实体真的搬走了**：L1 模块存在，且实体的 ``__module__`` 字面等于
   ``find_yourself.<name>``（硬判别——复制一份留在原地会红）。
2. **公开名集合一致**：垫片 ``__all__`` 与下沉清单**一字不差、一个不多一个不少**。
3. **同一对象而非副本**：垫片转出的每个公开名与 L1 里的对象 ``is`` 同一。
4. **模块级单例/共享状态同一性**：模块级被实例化出来的对象与模块级可变缓存
   （``offline._probe_cache``）两条路径拿到同一个。
5. **常量一致**：``MESSAGE_ID_KEY`` / ``OFFLINE_MODE_ENV`` /
   ``DEFAULT_ANCHOR_DIR`` / ``RETIREMENT_EVIDENCE_KEY`` / ``ZERO`` 两路径取值相同。
6. **禁止残留 + 点名单包含性**：``runtime/`` 下不得再出现 ``services.audit`` /
   ``services.budget`` / ``services.offline`` 字样（门禁 ``_parse_imports()`` 用
   ``ast.walk`` 全树遍历判定，把 import 挪进函数体或 ``TYPE_CHECKING`` 块无效）；
   且 5 个新模块名必须都在门禁的 L1 点名单里，否则「消解成功」是假性消失。

导入方式刻意做成「缺失即断言失败」而非导入期 ImportError：这样在尚未下沉的老代码上
本文件也能正常收集，并以清晰的 AssertionError 报红（而不是 CollectionError）。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from decimal import Decimal
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "find_yourself"

#: (L1 模块名, services 侧垫片模块名, 下沉清单里的公开名 tuple)
CLUSTERS = (
    (
        "find_yourself.hashing",
        "find_yourself.services.hasher",
        ("canonical_json", "digest", "content_hash"),
    ),
    (
        "find_yourself.anchors",
        "find_yourself.services.anchor_store",
        (
            "DEFAULT_ANCHOR_DIR",
            "RETIREMENT_EVIDENCE_KEY",
            "anchor_key",
            "AnchorRecord",
            "AnchorStore",
            "FileAnchorStore",
            "S3AnchorStore",
            "NullAnchorStore",
        ),
    ),
    (
        "find_yourself.audit",
        "find_yourself.services.audit",
        ("MESSAGE_ID_KEY", "AuditVerifyResult", "AuditService", "message_id_of"),
    ),
    (
        "find_yourself.budget",
        "find_yourself.services.budget",
        ("ZERO", "BudgetLimits", "BudgetService"),
    ),
    (
        "find_yourself.offline",
        "find_yourself.services.offline",
        (
            "OFFLINE_MODE_ENV",
            "OfflineUnavailable",
            "OfflineStatus",
            "offline_mode_enabled",
            "is_offline",
            "probe_network",
            "remote_block_reason",
            "require_remote",
            "status",
            "clear_cache",
        ),
    ),
)

_SHIM_MODULES = tuple(shim for _, shim, _ in CLUSTERS)
_L1_MODULES = tuple(l1 for l1, _, _ in CLUSTERS)

#: 每个簇的公开名里「既不是类、也不是函数/模块」的值对象（常量、模块级单例）清单。
#: 这份清单是硬钉死的：常量在搬迁过程中被复制成第二份、或悄悄变成函数，都必须报红。
#: 由本文件下方 test_module_level_instantiated_singletons_are_shared 实测校验。
EXPECTED_VALUE_OBJECTS: dict[str, tuple[str, ...]] = {
    "find_yourself.hashing": (),  # 公开名全是函数，无值对象
    "find_yourself.anchors": ("DEFAULT_ANCHOR_DIR", "RETIREMENT_EVIDENCE_KEY"),
    "find_yourself.audit": ("MESSAGE_ID_KEY",),
    "find_yourself.budget": ("ZERO",),
    "find_yourself.offline": ("OFFLINE_MODE_ENV",),
}
assert set(EXPECTED_VALUE_OBJECTS) == set(_L1_MODULES), (
    "EXPECTED_VALUE_OBJECTS 必须与 CLUSTERS 的 L1 模块一一对应"
)


def _try_import(name: str):
    """导入模块；缺失返回 ``None``，由断言给出可读失败（而非导入期异常）。"""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:  # pragma: no cover - 仅在下沉前触发
        return None


def _load(module_name: str):
    module = _try_import(module_name)
    assert module is not None, (
        f"L1 模块 {module_name} 未下沉：5 个基础设施模块应整簇下沉到 "
        f"find_yourself 顶层（L1），services 侧只留 re-export 垫片（ADR-0001 D3/D6）"
    )
    return module


def _attrs(module, name: str, public: tuple[str, ...]):
    missing = [n for n in public if not hasattr(module, n)]
    assert not missing, f"{module.__name__} 缺少公开名：{missing}"
    return {n: getattr(module, n) for n in public}


# --------------------------------------------------------------------------
# 1. 实体真的搬走了（L1 模块存在 + __module__ 指向 L1）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("l1_name", _L1_MODULES)
def test_l1_module_exists(l1_name: str):
    """5 个 L1 模块必须真实存在（下沉前此断言为红）。"""
    _load(l1_name)


@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_entities_declare_l1_as_their_module(l1_name: str, shim_name: str,
                                            public: tuple[str, ...]):
    """类的 ``__module__`` 必须指向 L1，证明实体搬走而不是原地复制一份。"""
    l1 = _load(l1_name)
    moved = [
        n for n in public
        if inspect.isclass(getattr(l1, n)) and getattr(l1, n).__module__ == shim_name
    ]
    assert not moved, (
        f"{l1_name} 里的类 {moved} 的 __module__ 仍指向旧垫片 {shim_name}，"
        f"说明实体没真正下沉"
    )


# --------------------------------------------------------------------------
# 2. 公开名集合一致（__all__ 一个不多一个不少）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_shim_all_equals_public_manifest_exactly(l1_name: str, shim_name: str,
                                                 public: tuple[str, ...]):
    """垫片 ``__all__`` 必须与下沉清单**完全相等**（防回归断言的基准）。"""
    shim = _load(shim_name)
    exported = getattr(shim, "__all__", None)
    assert exported is not None, f"{shim_name} 垫片必须显式声明 __all__"
    assert len(exported) == len(set(exported)), f"{shim_name}.__all__ 有重复项：{exported}"
    assert set(exported) == set(public), (
        f"{shim_name}.__all__ 与下沉清单不一致："
        f"多 {sorted(set(exported) - set(public))} / 少 {sorted(set(public) - set(exported))}"
    )


@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_shim_defines_no_entities(l1_name: str, shim_name: str,
                                  public: tuple[str, ...]):
    """垫片必须是纯 re-export：不再定义任何函数/类实体，也不写业务逻辑。"""
    shim = _load(shim_name)
    tree = ast.parse(inspect.getsource(shim))
    defined = [
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert not defined, f"{shim_name} 垫片里仍有实体定义：{defined}"
    assigns = [
        n.targets[0].id for n in tree.body
        if isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and not n.targets[0].id.startswith("__")
    ]
    assert not assigns, f"{shim_name} 垫片里仍有模块级赋值（应为纯 re-export）：{assigns}"


@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_l1_owns_every_public_name(l1_name: str, shim_name: str,
                                   public: tuple[str, ...]):
    """L1 必须承载下沉清单里的每个公开名。"""
    l1 = _load(l1_name)
    _attrs(l1, l1_name, public)


# --------------------------------------------------------------------------
# 3. 身份一致（is，不是副本）
# --------------------------------------------------------------------------
@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_shim_reexports_the_very_same_objects(l1_name: str, shim_name: str,
                                              public: tuple[str, ...]):
    """垫片导出的每个名字与 L1 是**同一个对象**，不是副本。"""
    l1 = _load(l1_name)
    shim = _load(shim_name)
    for name, obj in _attrs(l1, l1_name, public).items():
        assert getattr(shim, name) is obj, (
            f"{shim_name}.{name} 与 L1 的对象不是同一实体（是副本，re-export 失效）"
        )


@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_reimport_yields_the_same_objects(l1_name: str, shim_name: str,
                                          public: tuple[str, ...]):
    """重复 import 两条路径仍拿到同一对象（模块缓存未被绕过）。"""
    l1 = _load(l1_name)
    shim = _load(shim_name)
    again_l1 = importlib.import_module(l1_name)
    again_shim = importlib.import_module(shim_name)
    for name, obj in _attrs(l1, l1_name, public).items():
        assert getattr(again_l1, name) is obj
        assert getattr(again_shim, name) is obj


# --------------------------------------------------------------------------
# 4. 模块级单例 / 共享状态同一性
# --------------------------------------------------------------------------
@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_module_level_instantiated_singletons_are_shared(l1_name: str, shim_name: str,
                                                        public: tuple[str, ...]):
    """公开名里所有非类、非函数的值对象（常量/单例）在两条路径上必须是同一个对象。

    类与函数的同一性由上面的对象同一性测试覆盖；这里专门盯住「值」——
    搬迁时最容易被顺手复制一份而丢失身份的东西（如 ``ZERO = Decimal(...)``）。
    """
    l1 = _load(l1_name)
    shim = _load(shim_name)
    found: list[str] = []
    for name in public:
        value = getattr(l1, name)
        assert getattr(shim, name) is value, (
            f"{shim_name}.{name} 与 L1 的不是同一个对象（实体被复制了一份）"
        )
        if not (inspect.isclass(value) or inspect.isroutine(value)
                or inspect.ismodule(value)):
            found.append(name)
    # 必须与 EXPECTED_VALUE_OBJECTS 精确相等（而不是宽松的上下界）：
    # 断言写成恒真的上界会让本节完全失效，而值对象恰好是搬迁时最容易被复制一份
    # 而丢失身份的东西（如 ``ZERO = Decimal(...)``、``*_KEY`` 常量），值得硬钉住。
    # ``hashing`` 的公开名全是函数，值对象为空是事实而非缺陷。
    expected = EXPECTED_VALUE_OBJECTS[l1_name]
    assert sorted(found) == sorted(expected), (
        f"{l1_name} 的值对象名单与 EXPECTED_VALUE_OBJECTS 不一致："
        f"实际 {sorted(found)} / 期望 {sorted(expected)}"
        f"（常量被改成函数/类，或新增了模块级单例未登记）"
    )


def test_offline_probe_cache_is_the_same_shared_dict():
    """``offline._probe_cache`` 是模块级可变缓存：垫片路径必须共享同一份。

    这是本轮唯一真实存在的「模块级共享可变状态」，下沉最容易破的就是它——
    复制一份缓存会让探测结果在两条路径间不一致。垫片是纯 re-export，不应
    持有自己的 ``_probe_cache``；因此这里断言垫片**根本没有**该属性，
    而垫片暴露的 ``clear_cache`` / ``probe_network`` 操作的正是 L1 那一份。
    """
    l1 = _load("find_yourself.offline")
    shim = _load("find_yourself.services.offline")
    assert not hasattr(shim, "_probe_cache"), (
        "services.offline 不应持有自己的 _probe_cache（垫片必须是纯 re-export）"
    )
    probe_cache = vars(l1)["_probe_cache"]
    assert isinstance(probe_cache, dict), "L1 offline._probe_cache 应为 dict"
    probe_cache["__g7c3b_probe__"] = True
    try:
        shim.clear_cache()
        assert "__g7c3b_probe__" not in probe_cache, (
            "经垫片 clear_cache() 未清空 L1 的 _probe_cache（两者不是同一份状态）"
        )
    finally:
        probe_cache.pop("__g7c3b_probe__", None)
    # 垫片暴露的函数对象的 __globals__ 必须就是 L1 模块的命名空间：
    # 这是「共享同一份模块级状态」的充要证据（避免真实网络探测带来的抖动）。
    assert shim.probe_network.__globals__ is vars(l1), (
        "services.offline.probe_network 的 __globals__ 不是 L1 模块命名空间"
    )


# --------------------------------------------------------------------------
# 5. 常量一致
# --------------------------------------------------------------------------
def test_string_constants_are_equal_on_both_paths():
    """4 个字符串常量两条路径取值相同（值本身也锁死，防搬运手滑）。"""
    cases = (
        ("find_yourself.audit", "find_yourself.services.audit", "MESSAGE_ID_KEY", "message_id"),
        ("find_yourself.offline", "find_yourself.services.offline",
         "OFFLINE_MODE_ENV", "FY_OFFLINE_MODE"),
        ("find_yourself.anchors", "find_yourself.services.anchor_store",
         "DEFAULT_ANCHOR_DIR", "~/.find-yourself/anchors"),
        ("find_yourself.anchors", "find_yourself.services.anchor_store",
         "RETIREMENT_EVIDENCE_KEY", "r39_retirement"),
    )
    for l1_name, shim_name, const, expected in cases:
        l1 = _load(l1_name)
        shim = _load(shim_name)
        assert getattr(l1, const) == expected, f"{l1_name}.{const} 取值被改动：{getattr(l1, const)!r}"
        assert getattr(shim, const) == getattr(l1, const), (
            f"{shim_name}.{const} 与 L1 取值不一致"
        )


def test_budget_zero_constant_is_equal_on_both_paths():
    """``ZERO`` 是 Decimal 实例，两路径必须是同一对象且值不变。"""
    l1 = _load("find_yourself.budget")
    shim = _load("find_yourself.services.budget")
    assert l1.ZERO == Decimal("0.000000"), f"ZERO 取值被改动：{l1.ZERO!r}"
    assert shim.ZERO is l1.ZERO


# --------------------------------------------------------------------------
# 6. 禁止残留 + 门禁点名单包含性
# --------------------------------------------------------------------------
@pytest.mark.parametrize("forbidden", ("services.audit", "services.budget", "services.offline"))
def test_runtime_has_no_residual_reference_to_moved_services(forbidden: str):
    """``runtime/`` 下不得有任何文件源码文本出现 ``<forbidden>``。

    门禁 ``_parse_imports()`` 用 ``ast.walk(tree)`` 全树遍历、按 import 语句里写的
    **绝对模块名字符串** 判定，不区分作用域、不排除 ``if TYPE_CHECKING:`` 块——
    所以只要字样还在，那条 D3R 边就永远消解不掉。
    """
    runtime_dir = _SRC / "runtime"
    assert runtime_dir.is_dir(), f"runtime 目录不存在：{runtime_dir}"
    pattern = re.compile(re.escape(forbidden))
    hits = []
    for py in sorted(runtime_dir.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{py.relative_to(_SRC).as_posix()}:{lineno}: {line.strip()}")
    assert not hits, (
        f"runtime/ 下仍残留 `{forbidden}` 字样（{len(hits)} 处），D3R 无法消解：\n"
        + "\n".join(hits)
    )


def test_gate_l1_allowlist_contains_all_five_new_modules():
    """门禁 L1 是「点名单」机制：不在名单里的模块判为 None，依赖边会假性消失。

    所以 5 个新 L1 模块必须逐个出现在门禁的点名单单元组文本里。
    """
    gate = Path(__file__).resolve().parents[2] / "scripts" / "p0" / "run_layering_gate.py"
    text = gate.read_text(encoding="utf-8")
    match = re.search(r"if module in \((.*?)\)\s*:", text, re.DOTALL)
    assert match, "未能在 run_layering_gate.py 里定位 L1 点名单单元组"
    allowlist = match.group(1)
    missing = [name for name in _L1_MODULES if f'"{name}"' not in allowlist]
    assert not missing, (
        f"门禁 L1 点名单缺少 {missing}：不在点名单里的模块会被判为「不属于任何层」，"
        f"其依赖边将假性消失（消解成功是自欺欺人）"
    )


@pytest.mark.parametrize("l1_name,shim_name,public", CLUSTERS)
def test_l1_module_does_not_import_services(l1_name: str, shim_name: str,
                                            public: tuple[str, ...]):
    """L1 模块不得回头 import L2.1 的 services 包（下沉才成立的语义前提）。"""
    l1 = _load(l1_name)
    imported = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(l1)))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name and "services" in name for name in imported), (
        f"L1 {l1_name} 反向依赖 services：{sorted(n for n in imported if n)}"
    )
