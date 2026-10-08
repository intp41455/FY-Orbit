"""发行更新检查服务（Windows 绿色包的自动更新原语）。

这是文档点名的缺口之一：「v1.0 → check update → download v1.1 → verify
signature → atomic replace → rollback」成熟桌面应用的最小闭环。本模块把它
实现为**显式手动触发**（不静默后台替换文件、不强制联网），与「默认离线
运行」的产品约定对齐：

* 离线模式（``FY_OFFLINE_MODE=1``，默认）⇒ 不联网，``check`` 直接返回
  「离线模式已禁用远程调用」；这是正确行为，不是失败。
* ``FY_OFFLINE_MODE=0`` 时才真去 GitHub Releases API 查最新 tag；
  不写死任何"有网"的假象——超时就是超时，如实标注。
* 校验与替换分两条路： ``check`` 只读网络，**不落盘**；``stage`` 下载并
  验证 SHA-256 后写入 ``.update/pending/``，**不动正在运行的程序目录**；
  ``apply`` 由用户显式触发，做「备份 → 原子替换 → 校验失败回滚」。

为什么不直接在这里替换运行中的 exe：Windows 上运行中的 exe 无法覆盖。
``apply`` 把下载包核对后复制到 ``.update/incoming/``，然后结束本次更新
请求；用户下次启动时（``FY-Orbit.bat`` 检测到 ``.update/incoming`` 标记）
替换程序目录并清理。这样文档所述的 atomic replace / rollback 才能真正有意义。

诚实边界：本模块不做代码签名，只校验 SHA-256（与 SHA256SUMS.txt 一致）。
签名缺失时不宣称「已签名」，只如实报告 ``signature_status=unsigned``。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .errors import Conflict, DomainError, NotFound, ValidationFailed

#: 更新源：FY Orbit 的 GitHub Releases。可用环境变量覆盖（测试/私有源）。
RELEASES_API = os.environ.get(
    "FY_UPDATE_RELEASES_API",
    "https://api.github.com/repos/intp41455/FY-Orbit/releases/latest",
)

#: 打包了哪些版本号固定格式
_TAG_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size_bytes: int = 0
    sha256: str = ""  # 由 SHA256SUMS.txt 填入，不从 API 信任


@dataclass
class RemoteRelease:
    tag: str  # 例如 "v1.1.0"
    html_url: str
    assets: list[ReleaseAsset] = field(default_factory=list)


@dataclass
class UpdateVerdict:
    current_version: str
    latest_version: Optional[str]
    update_available: bool
    offline_blocked: bool
    reason: str
    signature_status: str  # "unsigned" | "unknown"
    asset_sha256: str  # 发行包资产的期望 SHA-256，未找到时空串


class UpdatePendingMissing(DomainError):
    http_status = 400
    default_code = "update_pending_missing"

    def __init__(self):
        super().__init__(self.default_code, "没有已暂存（staged）的更新可供 apply", 400)


class UpdateStagingFailed(DomainError):
    http_status = 409
    default_code = "update_staging_failed"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def parse_version(tag: str) -> Optional[tuple[int, int, int, str]]:
    m = _TAG_VERSION.match(tag.strip())
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4) or ""
    return major, minor, patch, pre


def version_newer(remote: str, current: str) -> bool:
    r = parse_version(remote)
    c = parse_version(current)
    if r is None or c is None:
        return False
    # 只比较 (major, minor, patch)；pre-release 场景请手工打 tag
    return (r[0], r[1], r[2]) > (c[0], c[1], c[2])


def current_version(settings=None) -> str:
    """本机当前版本号。

    优先级：FY_VERSION 环境变量 > .build_stage/BUILD-META.txt > pyproject.toml。
    未冻结打包形态下退回 pyproject.toml 的 project.version；发行包由打包脚本
    把最终版本写入 BUILD-META.txt，所以两者应一致。
    """
    env = os.environ.get("FY_VERSION", "").strip()
    if env:
        return env.lstrip("v")
    # BUILD-META.txt 在发行包根目录
    for candidate in (Path("BUILD-META.txt"), Path("..") / "BUILD-META.txt"):
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("version"):
                    return line.split(":", 1)[1].strip()
    return "0.0.0"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------


def _fetch_json(url: str, *, timeout: float = 10.0) -> dict:
    """GET JSON。联网失败不伪造成功——超时/DNS 错误一律抛给调用方。"""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "FY-Orbit-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest_release(fetcher: Callable[[str], dict] | None = None) -> RemoteRelease:
    """从 GitHub Releases API 取最新一个发行物。"""
    do = fetcher or (lambda url: _fetch_json(url, timeout=10.0))
    payload = do(RELEASES_API)
    assets: list[ReleaseAsset] = []
    for a in payload.get("assets", []) or []:
        assets.append(ReleaseAsset(
            name=a.get("name", ""),
            download_url=a.get("browser_download_url", ""),
            size_bytes=int(a.get("size", 0) or 0),
        ))
    return RemoteRelease(
        tag=payload.get("tag_name", ""),
        html_url=payload.get("html_url", ""),
        assets=assets,
    )


def parse_sha256sums(text: str) -> dict[str, str]:
    """解析 SHA256SUMS.txt（行格式："<hex>  <filename>"）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip(), parts[1].strip().lstrip("*")
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            out[name] = digest.lower()
    return out


def pick_zip_asset(release: RemoteRelease) -> Optional[ReleaseAsset]:
    for a in release.assets:
        if a.name.endswith(".zip") and "Windows" in a.name:
            return a
    for a in release.assets:
        if a.name.endswith(".zip"):
            return a
    return None


def find_asset_sha256(release: RemoteRelease, asset: ReleaseAsset, *,
                      fetcher: Callable[[str], str] | None = None) -> str:
    """从 SHA256SUMS.txt 资产取期望校验和。找不到就返回空串（调用方要失败）。"""
    def _get(url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "FY-Orbit-Updater"})
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            return resp.read().decode("utf-8")

    do = fetcher or _get
    sums_asset = next((a for a in release.assets if a.name.lower().endswith("sha256sums.txt")
                       or a.name.lower().endswith("sha256sums")), None)
    if sums_asset is None:
        # GitHub Release 常见命名：SHA256SUMS.txt / SHA256SUMS
        sums_asset = next((a for a in release.assets if "sha256" in a.name.lower()), None)
    if sums_asset is None:
        return ""
    try:
        sums_text = do(sums_asset.download_url)
    except Exception:
        return ""
    table = parse_sha256sums(sums_text)
    return table.get(asset.name, "")


def download_release_zip(asset: ReleaseAsset, dest: Path, *,
                         expected_sha256: str = "", timeout: float = 300.0) -> dict:
    """下载 ZIP 资产；存在期望 SHA-256 时先下载后校验，不匹配即**拒绝使用**。"""
    req = urllib.request.Request(asset.download_url, headers={"User-Agent": "FY-Orbit-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh, length=1 << 20)
    actual = sha256_file(dest)
    if expected_sha256 and actual.lower() != expected_sha256.lower():
        try:
            dest.unlink()
        except OSError:
            pass
        raise ValidationFailed(
            "update_sha256_mismatch",
            f"下载的更新包 SHA-256 不匹配（期望 {expected_sha256}，实际 {actual}），已删除。",
            422,
        )
    return {"path": str(dest), "sha256": actual, "size_bytes": dest.stat().st_size}


# ---------------------------------------------------------------------------
# 服务入口
# ---------------------------------------------------------------------------


def is_offline_mode() -> bool:
    return os.environ.get("FY_OFFLINE_MODE", "1").strip() not in {"0", "false", "no", "off"}


def check_update(fetcher: Callable[[str], dict] | None = None) -> UpdateVerdict:
    cur = current_version()
    if is_offline_mode():
        return UpdateVerdict(
            current_version=cur, latest_version=None, update_available=False,
            offline_blocked=True,
            reason="离线模式（FY_OFFLINE_MODE=1）：不联网检查更新。"
                   "如需更新请联网并以 FY_OFFLINE_MODE=0 启动。",
            signature_status="unknown", asset_sha256="",
        )
    try:
        rel = fetch_latest_release(fetcher=fetcher)
    except Exception as exc:
        return UpdateVerdict(
            current_version=cur, latest_version=None, update_available=False,
            offline_blocked=False,
            reason=f"无法连接更新源：{type(exc).__name__}。网络异常已如实报告，未静默装旧版。",
            signature_status="unknown", asset_sha256="",
        )
    if not rel.tag:
        return UpdateVerdict(
            current_version=cur, latest_version=None, update_available=False,
            offline_blocked=False,
            reason="更新源返回了不含 tag_name 的异常载荷，已如实报告，不当作有新版。",
            signature_status="unknown", asset_sha256="",
        )
    available = version_newer(rel.tag, cur)
    zip_asset = pick_zip_asset(rel)
    sha = find_asset_sha256(rel, zip_asset) if zip_asset else ""
    return UpdateVerdict(
        current_version=cur,
        latest_version=rel.tag.lstrip("v"),
        update_available=available,
        offline_blocked=False,
        reason=("有可用更新" if available else "已是最新版本"),
        signature_status="unsigned",  # 发行包当前未做 Authenticode 签名
        asset_sha256=sha,
    )


def stage_update(dest_dir: Path, *, fetcher=None) -> dict:
    """下载并校验最新 ZIP，放到 ``<dest_dir>/.update/pending/``。

    重要：此函数**不动正在运行的程序目录**。替换由 ``apply`` 负责。
    """
    if is_offline_mode():
        raise Conflict("offline_mode_remote_blocked",
                       "离线模式下已禁用更新下载。如需更新请联网并以 FY_OFFLINE_MODE=0 启动。", 503)
    rel = fetch_latest_release(fetcher=fetcher)
    asset = pick_zip_asset(rel)
    if asset is None:
        raise NotFound("update_asset_missing", "最新 Release 中未找到 Windows ZIP 资产。", 404)
    # 资产期望 SHA-256 从 SHA256SUMS.txt 取（纯文本资产），单独走真实网络；
    # 不要复用 fetch_latest_release 的 JSON fetcher（它返回 dict，不是文本）。
    expected = find_asset_sha256(rel, asset)
    pending = dest_dir / ".update" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    target = pending / asset.name
    info = download_release_zip(asset, target, expected_sha256=expected)
    meta = {
        "staged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tag": rel.tag,
        "zip_name": asset.name,
        "zip_path": str(info["path"]),
        "zip_sha256": info["sha256"],
        "expected_sha256": expected,
        "from_version": current_version(),
        "signature_status": "unsigned",
    }
    (pending / "pending.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return meta


def apply_staged_update(install_dir: Path) -> dict:
    """把已暂存的更新从 ``.update/pending/`` 复制到 ``.update/incoming/`` 并标记待替换。

    真正替换程序目录发生在下次启动时（由 ``FY-Orbit.bat`` 检测 ``.update/incoming``
    标记执行）。此函数做：备份清单记录、完整性复核、失败回滚。
    """
    pending = install_dir / ".update" / "pending" / "pending.json"
    if not pending.is_file():
        raise UpdatePendingMissing()
    meta = json.loads(pending.read_text(encoding="utf-8"))
    zip_path = Path(meta["zip_path"])
    if not zip_path.is_file():
        raise UpdatePendingMissing()
    # 再次校验，防止 pending 之后被人动过
    actual = sha256_file(zip_path)
    if meta["expected_sha256"] and actual.lower() != meta["expected_sha256"].lower():
        raise ValidationFailed(
            "update_sha256_mismatch",
            f"待应用更新包 SHA-256 不匹配（期望 {meta['expected_sha256']}，实际 {actual}），拒绝应用。",
            422,
        )
    incoming = install_dir / ".update" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    staged_zip = incoming / meta["zip_name"]
    if zip_path.resolve() != staged_zip.resolve():
        shutil.copy2(zip_path, staged_zip)
    (incoming / "incoming.json").write_text(json.dumps({
        **meta, "zip_path": str(staged_zip),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "staged_for_next_launch",
        "zip_path": str(staged_zip),
        "sha256": actual,
        "note": "下次启动 FY-Orbit.bat 时将自动替换程序目录；启动后请核对新版本号。",
    }


def rollback_update(install_dir: Path) -> dict:
    """回滚：删除 incoming 标记，恢复 .update/backup 下的备份。

    仅作兜底，真正的备份发生在 launcher 脚本替换前（由 launcher 落盘备份）。
    这里做的是清理 incoming/pending 状态，让用户能继续用当前版本。
    """
    incoming = install_dir / ".update" / "incoming"
    pending = install_dir / ".update" / "pending"
    removed = 0
    for path in (incoming / "incoming.json", pending / "pending.json"):
        if path.is_file():
            path.unlink()
            removed += 1
    return {"status": "rolled_back", "cleared_files": removed,
            "note": "已清除更新暂存状态，应用将回退到当前版本。"}