"""桌面壳安全配置护栏（收官报告 §613 CSP 收紧 + devtools 强制声明）。

为什么需要这个文件
------------------
收官报告实测确认两条，且都是真实敞口：

1. **`connect-src` 里的 `http://127.0.0.1:*` 是注定失效的假防线**。
   `main.rs:133` 用 `--port 0` 启动 sidecar（**端口每次随机**），因此任何具名
   端口的白名单明天就失效；而 `'self'` 本身就等于 sidecar origin（`main.rs:74`
   主窗与 pet 窗都导航到同一 base）。留着通配等于给「局域网 IP 侧车」或
   「沙箱内引导导航」留活靶子。
2. **`devtools` 全仓零命中**。即「生产不开 devtools」只是「恰好没开」，
   不是「被拦住」——在无 CI 门禁的前提下这两者无法区分。

按 ADR-011「凡声明安全属性，必须能回答哪个测试会因该属性失效而变红」，
本文件就是这个测试：把 `http://127.0.0.1:*` 或删掉 `devtools: false`，
下面用例立刻变红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CONF = (
    Path(__file__).resolve().parents[2]
    / "desktop"
    / "tauri"
    / "src-tauri"
    / "tauri.conf.json"
)


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    assert CONF.exists(), f"Tauri 配置缺失：{CONF}"
    return json.loads(CONF.read_text(encoding="utf-8"))


def test_csp_has_no_loopback_wildcard(tauri_conf: dict):
    """🔴 connect-src 不得再含 `http://127.0.0.1:*`（--port 0 架构下是假防线）。"""
    csp = tauri_conf["app"]["security"]["csp"]
    assert "127.0.0.1:*" not in csp, (
        "connect-src 又写回了 loopback 通配；在 --port 0 随机端口架构下它防不住任何东西，"
        "只会为「侧车换到局域网 IP」或「沙箱内引导导航」留活靶子。"
    )
    assert "127.0.0.1" not in csp, "connect-src 不应出现任何具名 loopback 来源"


def test_csp_keeps_self_and_ipc(tauri_conf: dict):
    """回归防线：收敛通配时不能把同源与 ipc 通道一起删掉（会直接跑不起来）。"""
    csp = tauri_conf["app"]["security"]["csp"]
    assert "connect-src" in csp
    connect = [d for d in csp.split(";") if "connect-src" in d][0]
    assert "'self'" in connect, "connect-src 必须保留 'self'（同源即 sidecar origin）"
    assert "ipc:" in connect, "connect-src 必须保留 ipc: 通道"
    assert "http://ipc.localhost" in connect, "connect-src 必须保留 ipc.localhost"


def test_default_src_is_self_only(tauri_conf: dict):
    csp = tauri_conf["app"]["security"]["csp"]
    default = [d for d in csp.split(";") if "default-src" in d][0]
    assert default.strip() == "default-src 'self'", "default-src 必须收紧为仅 'self'"


def test_devtools_is_explicitly_disabled(tauri_conf: dict):
    """🔴 devtools 必须是显式 false——「恰好没开」不等于「被拦住」。"""
    security = tauri_conf["app"]["security"]
    assert "devtools" in security, (
        "缺少显式 devtools 声明；生产调试口是否关闭会退化为「恰好没开」，"
        "在无 CI 门禁时无法与「被拦住」区分。"
    )
    assert security["devtools"] is False, "生产构建必须显式关闭 devtools"


def test_shell_open_is_disabled(tauri_conf: dict):
    """回归防线：shell.open 关着（远程内容不得调用系统打开）。"""
    assert tauri_conf.get("plugins", {}).get("shell", {}).get("open") is False
