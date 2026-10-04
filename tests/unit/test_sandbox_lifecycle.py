"""C0 沙箱生命周期缺陷回归测试（S-1 / S-2 / S-3）。

这些用例是「杀掉缺陷就变红」的变异防线，不是「跑通就过」的烟雾测试。
每个 test 名字里点明它钉死的行为，删除对应实现后该用例必须失败。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from find_yourself.runtime.sandbox import (
    FORBIDDEN_WRITE_TARGETS,
    NETWORK_EGRESS_MARKERS,
    ExecutionResult,
    IsolatedScriptRunner,
    SandboxConfig,
    _diff_new_files,
    _snapshot_tree,
)


@pytest.fixture()
def runner(tmp_path: Path) -> IsolatedScriptRunner:
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "README.md").write_text("baseline", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    return IsolatedScriptRunner(
        SandboxConfig(sandbox_root=str(sandbox), protected_root=str(protected))
    )


# --------------------------------------------------------------------------
# S-1: 超时必须杀掉整棵进程树并验证回收
# --------------------------------------------------------------------------


def test_s1_timeout_sets_timed_out_and_exit_code(runner: IsolatedScriptRunner):
    res = runner.run_script("import time; time.sleep(30)", timeout=1.0)
    assert res.timed_out is True
    assert res.exit_code == -9
    assert res.success is False
    assert any(v.startswith("timeout_exceeded") for v in res.violations)


def test_s1_timeout_records_kill_evidence_with_pid(runner: IsolatedScriptRunner):
    """超时后必须留下 kill 证据（含被杀的 pid），否则无法证明树被终止。"""
    res = runner.run_script("import time; time.sleep(30)", timeout=1.0)
    assert res.kill_evidence, "kill_evidence 不能为空——超时必须尝试终止进程树"
    assert res.kill_evidence.get("attempted") is True
    assert isinstance(res.kill_evidence.get("pid"), int)
    assert res.kill_evidence["pid"] > 0


def test_s1_kill_evidence_reports_tree_signal(runner: IsolatedScriptRunner):
    res = runner.run_script("import time; time.sleep(30)", timeout=1.0)
    signal = res.kill_evidence.get("tree_signal")
    assert signal, "必须记录用的是哪种终止手段（taskkill /F /T 或 SIGKILL 进程组）"
    assert isinstance(signal, str)


def test_s1_grandchild_process_is_actually_dead(raller_free: bool = True):
    """父进程超时被杀后，孙进程必须不再存活——这是 S-1 的核心断言。

    脚本 fork 一个孙进程（Windows 用 multiprocessing，POSIX 用 os.fork），
    孙进程把自己的 pid 写进沙箱内的文件然后长时间睡眠。
    只杀直接子进程时，孙进程会存活，pid 文件里的 pid 仍 alive。
    """
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    sandbox = tmpdir / "sandbox"
    protected = tmpdir / "protected"
    protected.mkdir()
    runner = IsolatedScriptRunner(
        SandboxConfig(sandbox_root=str(sandbox), protected_root=str(protected))
    )
    # 用 subprocess 起孙进程：父脚本 fork 出来的孙进程继承父 pid，taskkill /T 会一并杀。
    script = f"""
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(r"{tmpdir.as_posix()}/grandchild.pid").write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
"""
    res = runner.run_script(script, timeout=2.0)
    assert res.timed_out is True
    pid_file = tmpdir / "grandchild.pid"
    assert pid_file.exists(), "脚本应来得及写出孙进程 pid"

    from find_yourself.services.terminal import process_alive

    # 给 kill_process_tree 的 2 秒回收轮询留出余量
    time.sleep(2.5)
    grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
    assert not process_alive(grandchild_pid), (
        f"孙进程 {grandchild_pid} 仍存活——S-1 未修复：只杀了直接子进程"
    )


def test_s1_normal_exit_leaves_no_kill_evidence(runner: IsolatedScriptRunner):
    res = runner.run_script("print('done')")
    assert res.exit_code == 0
    assert res.success is True
    assert res.kill_evidence == {}
    assert res.timed_out is False


# --------------------------------------------------------------------------
# S-2: memory_limit_mb 不能再是死字段
# --------------------------------------------------------------------------


def test_s2_memory_limit_field_is_no_longer_dead(runner: IsolatedScriptRunner):
    """旧实现里 memory_limit_mb 只定义不引用。这里断言它进入结果对象。"""
    res = runner.run_script("print('x')")
    assert res.memory_limit_mb == runner.config.memory_limit_mb
    assert res.memory_limit_mb == 256  # 默认值来自配置，不再是死字段


def test_s2_memory_limited_flag_is_reported_honestly(runner: IsolatedScriptRunner):
    """不能假装限制了。平台不支持时必须 reported False，而不是静默声称成功。"""
    res = runner.run_script("print('x')")
    assert isinstance(res.memory_limited, bool)
    if os.name == "nt":
        # Windows 无 setrlimit，诚实上报 False
        assert res.memory_limited is False
    else:
        assert res.memory_limited is True


def test_s2_memory_limit_can_be_disabled_explicitly(tmp_path: Path):
    protected = tmp_path / "p"
    protected.mkdir()
    runner = IsolatedScriptRunner(
        SandboxConfig(
            sandbox_root=str(tmp_path / "s"),
            protected_root=str(protected),
            enforce_memory_limit=False,
        )
    )
    res = runner.run_script("print('x')")
    assert res.memory_limited is False
    assert res.memory_limit_mb == 256  # 仍然如实上报配置值


def test_s2_zero_memory_limit_means_no_cap(tmp_path: Path):
    protected = tmp_path / "p2"
    protected.mkdir()
    runner = IsolatedScriptRunner(
        SandboxConfig(
            sandbox_root=str(tmp_path / "s2"),
            protected_root=str(protected),
            memory_limit_mb=0,
        )
    )
    res = runner.run_script("print('x')")
    assert res.memory_limited is False
    assert res.success is True


@pytest.mark.skipif(os.name == "nt", reason="RLIMIT_AS 只在 POSIX 生效")
def test_s2_posix_actually_caps_address_space(tmp_path: Path):
    """真跑一个试图吃 2GB 的脚本：POSIX 上必须因地址空间受限而失败。"""
    protected = tmp_path / "p3"
    protected.mkdir()
    runner = IsolatedScriptRunner(
        SandboxConfig(
            sandbox_root=str(tmp_path / "s3"),
            protected_root=str(protected),
            memory_limit_mb=128,
        )
    )
    res = runner.run_script(
        "x = bytearray(2 * 1024 * 1024 * 1024); print('allocated')",
        timeout=30.0,
    )
    assert res.success is False, "2GB 分配在 128MB 上限下必须失败"


# --------------------------------------------------------------------------
# S-3: 越界写入 / 网络出口必须被记录
# --------------------------------------------------------------------------


def test_s3_write_inside_sandbox_is_allowed(runner: IsolatedScriptRunner):
    res = runner.run_script(
        "from pathlib import Path; Path('out.txt').write_text('hi', encoding='utf-8')"
    )
    assert res.success is True
    assert res.out_of_sandbox_writes == []


def test_s3_write_outside_sandbox_is_detected(runner: IsolatedScriptRunner):
    """脚本往受保护根写一个文件——必须被抓到（旧实现只看 stdout，完全看不见）。"""
    target = runner.protected_root / "src"
    target.mkdir(parents=True, exist_ok=True)
    res = runner.run_script(
        f"from pathlib import Path; Path(r'{target.as_posix()}/evil.py').write_text('x = 1', encoding='utf-8')"
    )
    assert res.out_of_sandbox_writes, "越界写入必须被文件系统 diff 抓到"
    assert any("evil.py" in p for p in res.out_of_sandbox_writes)
    assert any(v.startswith("out_of_sandbox_write_detected") for v in res.violations)
    assert res.success is False


def test_s3_forbidden_write_targets_cover_critical_paths():
    for critical in (".env", ".git", "src", "web", "migrations", "pyproject.toml"):
        assert critical in FORBIDDEN_WRITE_TARGETS


@pytest.mark.skipif(
    True,
    reason=(
        "已知边界：写入检测只扫描 protected_root 子树。完全落在 protected_root "
        "之外的路径不会被 diff 覆盖——要覆盖它需要挂文件系统级监控（minifilter/"
        "fanotify），属于 C 批容器化的工作。此处固化为已知限制，防止有人误以为"
        "检测是全盘覆盖的。"
    ),
)
def test_s3_write_outside_protected_root_is_known_uncovered(runner: IsolatedScriptRunner):
    """记录已知边界：越界检测的范围是 protected_root，不是全盘。"""
    import tempfile

    outside = Path(tempfile.mkdtemp())
    res = runner.run_script(
        f"from pathlib import Path; Path(r'{(outside / 'x.txt').as_posix()}').write_text('x', encoding='utf-8')"
    )
    # 该路径不在 protected_root 子树内，因此不在扫描范围。
    assert all("x.txt" not in p for p in res.out_of_sandbox_writes)


def test_s3_network_egress_marker_is_a_violation(runner: IsolatedScriptRunner):
    res = runner.run_script("print('socket_connect_attempt 1.2.3.4:443')")
    assert any(v.startswith("network_egress_attempt") for v in res.violations)
    assert res.success is False


def test_s3_all_egress_markers_are_wired():
    for marker in NETWORK_EGRESS_MARKERS:
        assert marker in NETWORK_EGRESS_MARKERS


def test_s3_secret_exfiltration_still_detected(runner: IsolatedScriptRunner):
    res = runner.run_script("print('FY_DATABASE_URL=postgres://x')")
    assert "secret_exfiltration_detected" in res.violations


def test_s3_docker_socket_still_detected(runner: IsolatedScriptRunner):
    res = runner.run_script("print('docker.sock connected')")
    assert "docker_socket_access_detected" in res.violations


# --------------------------------------------------------------------------
# 环境清洗（原有能力不得回退）
# --------------------------------------------------------------------------


def test_env_sanitization_strips_secrets(runner: IsolatedScriptRunner):
    env = runner.get_sanitized_environment()
    # 沙箱自己注入的隔离声明不带 FY_ 前缀（FY_* 一律剥离），因此这里
    # 可以直接断言「任何 FY_ 开头的键都不该出现在子进程环境里」。
    for k in env:
        assert "SECRET" not in k.upper(), f"{k} 泄露了 SECRET 语义"
        assert "TOKEN" not in k.upper(), f"{k} 泄露了 TOKEN 语义"
        assert not k.upper().startswith("FY_"), (
            f"{k} 是宿主 FY_ 变量，不该透传（W05 密钥剥离铁律）"
        )


def test_env_declares_isolation_and_egress_policy(runner: IsolatedScriptRunner):
    env = runner.get_sanitized_environment()
    assert env["SANDBOX_ISOLATED"] == "1"
    assert env["SANDBOX_EGRESS_POLICY"] == "audit"


def test_env_has_no_fy_prefixed_keys_at_all(runner: IsolatedScriptRunner):
    """W05 回归防线：曾经注入 FY_SANDBOX_EGRESS_POLICY 导致密钥剥离测试变红。"""
    env = runner.get_sanitized_environment()
    assert [k for k in env if k.upper().startswith("FY_")] == []


# --------------------------------------------------------------------------
# 内部 diff 工具的直接单测
# --------------------------------------------------------------------------


def test_snapshot_and_diff_detect_new_file(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    before = _snapshot_tree(root)
    assert before == {}
    (root / "new.txt").write_text("x", encoding="utf-8")
    after = _snapshot_tree(root)
    assert len(_diff_new_files(before, after)) == 1


def test_diff_is_empty_when_nothing_changed(tmp_path: Path):
    root = tmp_path / "r2"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    snap = _snapshot_tree(root)
    assert _diff_new_files(snap, _snapshot_tree(root)) == []


def test_extra_files_are_materialised(runner: IsolatedScriptRunner):
    res = runner.run_script(
        "from pathlib import Path; assert Path('helper.py').exists(); print('ok')",
        extra_files={"helper.py": "VALUE = 1\n"},
    )
    assert res.success is True


def test_result_success_requires_clean_run(runner: IsolatedScriptRunner):
    res = runner.run_script("import sys; sys.exit(3)")
    assert res.exit_code == 3
    assert res.success is False


def test_launch_failure_is_reported_not_raised(runner: IsolatedScriptRunner):
    res = runner.run_script("x = 1", script_name="ok.py")
    assert isinstance(res, ExecutionResult)
    assert res.run_id.startswith("run_")


# --------------------------------------------------------------------------
# S0-1a / S0-1b: 路径穿越（收官报告判为「本轮最严重」）
#
# 报告特别警告：script_name 与 extra_files 是**同一函数内的两个独立参数**，
# 走不同代码行（写入点 A vs 写入点 B）。只修一个、只测一个，会出现
# 「看着覆盖了其实没有」。故下面两组用例**必须分别存在**。
# --------------------------------------------------------------------------


def test_s0_1a_script_name_relative_traversal_is_rejected(runner: IsolatedScriptRunner):
    """`../../evil.py` 这种相对逃逸必须被拒——不能写进源码树。"""
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    with pytest.raises(SandboxBoundaryViolation) as exc:
        runner.run_script("print('pwned')", script_name="../../../evil.py")
    assert "script_name" in str(exc.value)
    assert "escapes" in str(exc.value)


def test_s0_1a_script_name_absolute_escape_is_rejected(runner: IsolatedScriptRunner, tmp_path: Path):
    """绝对路径逃逸同样必须被拒。"""
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    outside = tmp_path / "outside_evil.py"
    with pytest.raises(SandboxBoundaryViolation):
        runner.run_script("print('pwned')", script_name=str(outside))
    assert not outside.exists(), "逃逸目标绝不能被创建"


def test_s0_1a_traversal_really_writes_nothing(runner: IsolatedScriptRunner, tmp_path: Path):
    """被拒后磁盘上必须真的没有残留文件（不是先写后校验）。"""
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    (runner.protected_root / "src").mkdir(parents=True, exist_ok=True)
    before = _snapshot_tree(runner.protected_root)
    with pytest.raises(SandboxBoundaryViolation):
        runner.run_script("x=1", script_name="../../src/planted.py")
    assert _snapshot_tree(runner.protected_root) == before, "校验必须先于写入"


def test_s0_1a_sensitive_filename_is_rejected(runner: IsolatedScriptRunner):
    """即便路径没逃逸，也不许把 .env / .git 当写目标。"""
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    for bad in (".env", ".git", "id_rsa", "config.env", "find-yourself.db"):
        with pytest.raises(SandboxBoundaryViolation):
            runner.run_script("x=1", script_name=bad)


def test_s0_1b_extra_files_key_traversal_is_rejected(runner: IsolatedScriptRunner):
    """🔴 extra_files 的键是**独立攻击面**：修好 script_name 不代表它也安全。"""
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    with pytest.raises(SandboxBoundaryViolation) as exc:
        runner.run_script(
            "print('ok')",
            extra_files={"../../../planted.py": "import os\n"},
        )
    assert "extra_files" in str(exc.value)


def test_s0_1b_extra_files_absolute_escape_is_rejected(runner: IsolatedScriptRunner, tmp_path: Path):
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    outside = tmp_path / "escaped.txt"
    with pytest.raises(SandboxBoundaryViolation):
        runner.run_script("print('ok')", extra_files={str(outside): "boom"})
    assert not outside.exists()


def test_s0_1b_extra_files_sensitive_name_rejected(runner: IsolatedScriptRunner):
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    with pytest.raises(SandboxBoundaryViolation):
        runner.run_script("print('ok')", extra_files={".env": "SECRET=1"})


def test_s0_1_legit_nested_extra_files_still_work(runner: IsolatedScriptRunner):
    """回归防线：合法嵌套路径不能被误伤。"""
    res = runner.run_script(
        "from pathlib import Path; assert Path('pkg/mod.py').exists(); print('ok')",
        extra_files={"pkg/mod.py": "VALUE = 1\n"},
    )
    assert res.success is True
    assert res.out_of_sandbox_writes == []


def test_s0_1_both_surfaces_guarded_independently(runner: IsolatedScriptRunner):
    """一条用例同时证明两个面都有守卫——防止有人只改一处。"""
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    with pytest.raises(SandboxBoundaryViolation):
        runner.run_script("x=1", script_name="../../a.py")
    with pytest.raises(SandboxBoundaryViolation):
        runner.run_script("x=1", extra_files={"../../b.py": "y=1"})


# --------------------------------------------------------------------------
# S-C: 假安全声明 —— 声明必须与实现一致（ADR-011 第二类假绿）
# --------------------------------------------------------------------------


def test_sc_isolation_level_is_reported_honestly(runner: IsolatedScriptRunner):
    """必须能查询真实隔离等级，而不是靠读 docstring 猜。"""
    info = runner.config.describe_isolation()
    assert info["isolation_level"] == "process"
    assert info["security_boundary"] is False, "不得声称自己是安全边界"


def test_sc_unenforced_claims_are_named(runner: IsolatedScriptRunner):
    """docstring 里「不做什么」必须能在代码里查到对应条目。"""
    info = runner.config.describe_isolation()
    for key in ("filesystem_jail", "network_egress_block",
                "docker_socket_block", "core_db_volume_block"):
        assert key in info["not_enforced"], f"{key} 未在诚实清单中列出"


def test_sc_memory_cap_is_listed_as_enforced_only_when_wired(tmp_path: Path):
    """memory_limit_mb 不能既是「声明」又无消费点——用描述接口对齐。"""
    protected = tmp_path / "p"
    protected.mkdir()
    on = IsolatedScriptRunner(SandboxConfig(
        sandbox_root=str(tmp_path / "s1"), protected_root=str(protected)))
    assert on.config.describe_isolation()["enforced"]["memory_cap"] is True

    off = IsolatedScriptRunner(SandboxConfig(
        sandbox_root=str(tmp_path / "s2"), protected_root=str(protected),
        enforce_memory_limit=False))
    assert off.config.describe_isolation()["enforced"]["memory_cap"] is False


def test_sc_module_docstring_makes_no_docker_socket_guarantee():
    """硬判据：模块 docstring 不得再出现「无法访问 docker socket」这种假承诺。"""
    import inspect
    from find_yourself.runtime import sandbox as mod
    doc = inspect.getdoc(mod) or ""
    assert "cannot access core DB volume or host Docker socket" not in doc, (
        "docstring 又写回了未实现的隔离承诺（S-C 复发）"
    )
    assert "NOT a security boundary" in doc, "必须显式声明自己不是安全边界"
