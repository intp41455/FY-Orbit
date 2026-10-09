"""发行构建链静态护栏（deploy/build_release_package.py 与发行文案）。

为什么用静态检查而不是真跑 PyInstaller
-------------------------------------
``--build`` 会真构建前端与后端二进制（分钟级、需要 npm/PyInstaller 环境），
不适合单测。这里守的是**结构性契约**——也就是历史上真实出过问题的那几条：

* 脚本曾经引用仓库根的 ``landing-page-2026-10-06.html``，而该文件已在 02e1008
  被删除 ⇒ ``--build`` 必然 ``FileNotFoundError``；
* ZIP 用 ``zf.write()`` 直接写工作区文件，条目时间戳来自构建机器当前时间 ⇒
  同一 tag 两次构建字节不同，「可复现构建」名不副实；
* 文档曾宣称「以独立原生应用窗口启动工作台」，而实际是浏览器 / Edge App 窗口
  承载 UI，同时又写「任何 64 位 Windows」——两处都是超出事实的宣传口径。

判据：删掉/改回被保护的行为，本文件必须变红。
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "deploy" / "build_release_package.py"
README = ROOT / "README.md"


@pytest.fixture(scope="module")
def builder() -> object:
    assert BUILDER.exists(), f"发行构建脚本缺失：{BUILDER}"
    spec = importlib.util.spec_from_file_location("fy_build_release_package", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. 落地页来源必须指向入库的权威副本，不能指向已删除的临时文件
# ---------------------------------------------------------------------------

def test_landing_source_exists_on_disk(builder):
    """脚本声明的落地页来源必须真实存在于仓库中。

    历史事故：``LANDING = ROOT / "landing-page-2026-10-06.html"``，而该文件在
    02e1008（清理临时落地页）中被删除，``--build`` 到第 3 步必然崩。
    """
    assert builder.LANDING.is_file(), (
        f"发行构建脚本引用的落地页 {builder.LANDING} 不存在，打包必然失败"
    )
    assert builder.LANDING.is_relative_to(ROOT)


def test_builder_does_not_reference_deleted_landing_file(builder):
    """不许再出现仓库根 ``landing-page-*.html`` 的引用。"""
    src = BUILDER.read_text(encoding="utf-8")
    stale = re.findall(r'["\']landing-page-[^"\']+\.html["\']', src)
    assert not stale, f"仍引用已删除的临时落地页：{stale}"


# ---------------------------------------------------------------------------
# 2. 可复现构建：ZIP 条目时间戳必须固定，且不得直接 zf.write(工作区文件)
# ---------------------------------------------------------------------------

def test_zip_entries_use_fixed_timestamp(builder):
    """ZIP 条目时间戳必须来自常量，而非构建机器当前时间。"""
    src = BUILDER.read_text(encoding="utf-8")
    assert "ZIP_EPOCH" in src, "缺少固定 ZIP 时间戳常量"
    assert "date_time=ZIP_EPOCH" in src, "ZIP 条目未使用固定时间戳"
    # 直接用文件系统 mtime 写条目是可复现性的天敌
    assert "zf.write(p" not in src, "仍在用 zf.write() 写入工作区文件（时间戳随机器变化）"
    assert "zf.writestr(info" in src


def test_zip_normalises_permissions_and_creator(builder):
    """条目权限与 create_system 必须规整，否则跨机器构建结果不同。"""
    src = BUILDER.read_text(encoding="utf-8")
    assert "FILE_ATTR" in src and "DIR_ATTR" in src
    assert "create_system" in src


def test_builder_embeds_provenance_without_timestamp(builder):
    """BUILD-META 必须记录 commit/tag，但不得含构建时间戳（含了就不算可复现）。"""
    meta = builder.build_meta("1.2.3")
    assert "git commit" in meta and "1.2.3" in meta
    assert not re.search(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}", meta), (
        "构建元信息里出现 ISO 时间戳，会让同 commit 的两次构建产出不同字节"
    )


def test_builder_does_not_call_datetime_now(builder):
    """整个脚本不得为了记录时间而调用 datetime.now()/time.time()。"""
    src = BUILDER.read_text(encoding="utf-8")
    assert "datetime.now" not in src
    assert "time.time()" not in src


# ---------------------------------------------------------------------------
# 3. 版本号必须由 tag 决定（可复现发行链的前提）
# ---------------------------------------------------------------------------

def test_version_detection_prefers_explicit_then_tag(builder):
    assert builder.detect_version("v2.5.1") == "2.5.1"
    assert builder.detect_version("2.5.1") == "2.5.1"
    # 拿不到 tag 时必须退化成显眼的 dev 版本，而不是悄悄沿用旧版本号
    assert builder.detect_version() == "0.0.0-dev"


def test_output_zip_name_embeds_version(builder):
    """产物名必须带版本号，避免不同版本互相覆盖。"""
    assert re.match(r"^FY-Orbit-Windows-v\{version\}\.zip$", "FY-Orbit-Windows-v{version}.zip")
    src = BUILDER.read_text(encoding="utf-8")
    assert 'f"FY-Orbit-Windows-v{version}.zip"' in src


def test_builder_writes_sha256_sidecar(builder):
    """必须产出顶层 ZIP 的 SHA-256 旁挂文件（Release 上要公布同一摘要）。"""
    src = BUILDER.read_text(encoding="utf-8")
    assert "sha256_file" in src
    assert '.zip.sha256' in src


# ---------------------------------------------------------------------------
# 4. 打包内容完整性：verify.bat + SHA256SUMS.txt 必须进包
# ---------------------------------------------------------------------------

def test_package_ships_verifier_and_sums(builder, tmp_path):
    app = tmp_path / "FY-Orbit"
    app.mkdir()
    for name in ("FY-Orbit.bat", "Install.bat", "使用说明.txt"):
        (app / name).write_text("x", encoding="utf-8")
    sums = builder._package_filesums(app)
    assert sums.startswith("#")
    for name in ("FY-Orbit.bat", "Install.bat", "使用说明.txt"):
        assert name in sums
    # 每行必须是 "<64位hex>  <相对路径>"，verify.bat 按这个格式解析
    for line in sums.splitlines():
        if line.startswith("#"):
            continue
        digest, name = line.split("  ", 1)
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"校验和格式错，verify.bat 解析不了：{line}"
        assert not name.startswith("/") and ".." not in name


def test_verify_bat_checks_all_listed_files_and_fails_loudly(builder):
    """verify.bat 必须逐文件比对并在失败时返回非零（启动器据此中止）。"""
    src = builder.VERIFY_BAT
    assert "SHA256SUMS.txt" in src
    assert "Get-FileHash" in src
    assert "exit /b 1" in src
    assert "exit /b 0" in src


def test_launcher_runs_verifier_before_backend(builder):
    """启动器必须先校验再拉起后端，且校验失败要中止而不是继续跑。"""
    src = builder.LAUNCHER_BAT
    assert "verify.bat" in src
    verify_at = src.index("verify.bat")
    backend_at = src.index("%BACKEND_EXE% --host")
    assert verify_at < backend_at, "校验发生在启动后端之后，顺序反了"
    assert "errorlevel 1" in src and "exit /b 1" in src


# ---------------------------------------------------------------------------
# 5. 发行文案不得超出事实
# ---------------------------------------------------------------------------

def test_user_doc_does_not_claim_native_tauri_window(builder):
    """使用说明不得宣称「原生窗口 / Tauri」——实际是浏览器/Edge App 窗口承载 UI。"""
    for name, text in (("使用说明", builder.README_TXT), ("启动器", builder.LAUNCHER_BAT)):
        # 「以原生窗口启动」是 README 的旧错话，改了就变红
        assert "以独立原生应用窗口启动" not in text, (
            f"{name} 仍宣称以「原生窗口」启动，与实际发行形态不符"
        )
        # 违规的是「把本发行包当 Tauri 应用卖」，而不是诚实披露「不是 Tauri 应用」
        assert "完整 Tauri" not in text, f"{name} 把本发行包宣称成完整 Tauri 应用"
        assert "Tauri Native Desktop App" not in text, (
            f"{name} 把本发行包宣称成 Tauri 原生桌面应用"
        )
    # 启动窗口/UI 形态的如实讲法必须写在用户文档里
    assert "浏览器" in builder.README_TXT
    assert "本地 Web 应用" in builder.README_TXT


def test_user_doc_states_actual_app_shape(builder):
    """必须如实说明形态：本地 Web 应用打包成桌面发行物。"""
    assert "本地 Web 应用" in builder.README_TXT
    assert "浏览器" in builder.README_TXT


def test_user_doc_narrows_windows_support(builder):
    """「任何 64 位 Windows」过宽，必须收窄到 Windows 10/11 x64。"""
    assert "任何 64 位 Windows" not in builder.README_TXT, (
        "『任何 64 位 Windows』是不可兑现的兼容承诺，必须收窄"
    )
    assert "Windows 10" in builder.README_TXT and "Windows 11" in builder.README_TXT
    assert "x64" in builder.README_TXT


def test_user_doc_discloses_missing_code_signature(builder):
    """未签名是用户会遇到 SmartScreen 的真实原因，必须写进用户文档而不是藏起来。"""
    assert "SmartScreen" in builder.README_TXT
    assert "未做 Authenticode 代码签名" in builder.README_TXT


def test_user_doc_tells_user_how_to_verify_integrity(builder):
    """给了 SHA-256 就必须告诉用户怎么用，否则等于没给。"""
    assert "Get-FileHash" in builder.README_TXT
    assert "SHA-256" in builder.README_TXT


# ---------------------------------------------------------------------------
# 6. README 不得出现超出事实的发行口径
# ---------------------------------------------------------------------------

def test_readme_does_not_claim_native_window_launch():
    text = README.read_text(encoding="utf-8")
    assert "以独立原生应用窗口启动工作台" not in text, (
        "README 宣称原生窗口启动，与实际（浏览器 / Edge App 窗口）不符"
    )


def test_readme_does_not_claim_broad_windows_support():
    text = README.read_text(encoding="utf-8")
    assert "任何 64 位 Windows" not in text


def test_readme_license_section_matches_bsl():
    """BSL 1.1 项目，README 末尾不得再自称 Apache-2.0。"""
    text = README.read_text(encoding="utf-8")
    assert "Apache License 2.0](LICENSE)" not in text, (
        "README 末尾仍写 Apache-2.0，与 LICENSE（BSL 1.1）矛盾"
    )
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Business Source License" in license_text
    assert "Business Source License" in text


# ---------------------------------------------------------------------------
# 7. 脚本本体可解析（语法护栏）
# ---------------------------------------------------------------------------

def test_builder_is_valid_python():
    ast.parse(BUILDER.read_text(encoding="utf-8"))
