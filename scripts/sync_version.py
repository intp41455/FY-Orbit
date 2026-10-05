#!/usr/bin/env python3
"""Sync project version across pyproject.toml, package.json, tauri.conf.json, and __init__.py (Batch G / §8).

Single source of truth: pyproject.toml [project.version].
Injects version and git commit hash into:
- src/find_yourself/__init__.py
- web/package.json
- desktop/tauri/src-tauri/tauri.conf.json
- web/src/version.json
"""

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def get_git_commit_short() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def get_source_version() -> str:
    pyproject_path = ROOT_DIR / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', content)
    if not m:
        raise ValueError(f"Could not find version in {pyproject_path}")
    return m.group(1)


def update_python_init(version: str, commit: str) -> None:
    init_path = ROOT_DIR / "src" / "find_yourself" / "__init__.py"
    if not init_path.exists():
        return
    text = f'"""Find Yourself: evidence, consent, and durable work."""\n\n__version__ = "{version}"\n__commit__ = "{commit}"\n'
    init_path.write_text(text, encoding="utf-8")


def update_package_json(version: str) -> None:
    pkg_path = ROOT_DIR / "web" / "package.json"
    if not pkg_path.exists():
        return
    data = json.loads(pkg_path.read_text(encoding="utf-8"))
    data["version"] = version
    pkg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def update_tauri_conf(version: str) -> None:
    tauri_path = ROOT_DIR / "desktop" / "tauri" / "src-tauri" / "tauri.conf.json"
    if not tauri_path.exists():
        return
    data = json.loads(tauri_path.read_text(encoding="utf-8"))
    data["version"] = version
    tauri_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def update_web_version_json(version: str, commit: str) -> None:
    web_version_path = ROOT_DIR / "web" / "src" / "version.json"
    data = {
        "version": version,
        "commit": commit,
    }
    web_version_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sync(check_only: bool = False) -> bool:
    version = get_source_version()
    commit = get_git_commit_short()

    # Check mode
    if check_only:
        pkg_path = ROOT_DIR / "web" / "package.json"
        if pkg_path.exists():
            pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
            if pkg_data.get("version") != version:
                print(f"Version mismatch in web/package.json: expected {version}, got {pkg_data.get('version')}")
                return False

        tauri_path = ROOT_DIR / "desktop" / "tauri" / "src-tauri" / "tauri.conf.json"
        if tauri_path.exists():
            tauri_data = json.loads(tauri_path.read_text(encoding="utf-8"))
            if tauri_data.get("version") != version:
                print(f"Version mismatch in tauri.conf.json: expected {version}, got {tauri_data.get('version')}")
                return False

        print(f"Versions in sync: {version} ({commit})")
        return True

    update_python_init(version, commit)
    update_package_json(version)
    update_tauri_conf(version)
    update_web_version_json(version, commit)
    print(f"Synced version {version} ({commit}) across Python, Web, and Tauri.")
    return True


if __name__ == "__main__":
    check_mode = "--check" in sys.argv
    success = sync(check_only=check_mode)
    sys.exit(0 if success else 1)
