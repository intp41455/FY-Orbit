"""Unit tests for automatic skill and prompt discovery (Batch E).

Verifies:
1. Normal discovery, security scanning, evaluation, and promotion.
2. Graceful skipping of malformed frontmatter or invalid skill names without crashing.
3. Idempotent re-scanning does not duplicate records or cause conflicts.
4. Security promotion gates cannot be bypassed: malicious/untrusted scripts stay staged
   and are blocked from promotion.
5. Prompt bulk import from markdown/yaml with idempotency.
"""

from __future__ import annotations

from pathlib import Path
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.db.base import Base
from find_yourself.db.models import Skill
from find_yourself.db.prompt_models import PromptTemplate
import find_yourself.db.models  # noqa: F401
import find_yourself.db.prompt_models  # noqa: F401
from find_yourself.skills.discovery import (
    discover_and_create_prompts,
    discover_and_stage_skills,
    parse_frontmatter_markdown,
)
from find_yourself.skills.harness import gateway


@pytest.fixture()
def session_maker():
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def test_parse_frontmatter_markdown():
    doc = """---
name: test-skill
version: 1.2.0
description: A test skill
---
# Main Content
Body line 1
"""
    fm, body = parse_frontmatter_markdown(doc)
    assert fm["name"] == "test-skill"
    assert fm["version"] == "1.2.0"
    assert "Body line 1" in body


def test_normal_discovery_and_promotion(session_maker, tmp_path: Path):
    """1. 正常发现：扫描目录，解析 frontmatter，过静态扫描，自动评估并上架，注册工具。"""
    pkg_dir = tmp_path / "skills"
    pkg_dir.mkdir()
    skill_dir = pkg_dir / "math_helper"
    skill_dir.mkdir()

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: math-helper
version: 1.0.0
description: Assists with basic arithmetic
domain: personal
license: MIT
tools:
  - name: add_numbers
    description: Add two numbers together
    schema:
      type: object
      properties:
        a: {type: integer}
        b: {type: integer}
---
# Math Helper
Instructions on doing arithmetic.
""",
        encoding="utf-8",
    )

    results = discover_and_stage_skills(session_maker, packages_dir=pkg_dir)
    assert len(results) == 1
    assert results[0]["name"] == "math-helper"
    assert results[0]["status"] == "promoted"

    # Verify DB record
    with session_maker() as session:
        row = session.execute(
            sa.select(Skill).where(Skill.name == "math-helper")
        ).scalar_one_or_none()
        assert row is not None
        assert row.state == "active"
        assert row.scan_passed is True

    # Verify tool registered in gateway
    tool_meta = gateway.snapshot().get("add_numbers")
    assert tool_meta is not None
    assert tool_meta["name"] == "add_numbers"


def test_invalid_frontmatter_is_safely_skipped(session_maker, tmp_path: Path):
    """2. frontmatter 非法被跳过：语法错误或非法命名不抛出致命异常，只记录并跳过。"""
    pkg_dir = tmp_path / "skills"
    pkg_dir.mkdir()

    # Case A: Malformed YAML
    bad_yaml_dir = pkg_dir / "bad_yaml"
    bad_yaml_dir.mkdir()
    (bad_yaml_dir / "SKILL.md").write_text(
        """---
name: [broken yaml :::
---
Broken content
""",
        encoding="utf-8",
    )

    # Case B: Illegal name (uppercase / spaces)
    bad_name_dir = pkg_dir / "bad_name"
    bad_name_dir.mkdir()
    (bad_name_dir / "SKILL.md").write_text(
        """---
name: INVALID NAME WITH SPACES
version: 1.0.0
---
Content
""",
        encoding="utf-8",
    )

    results = discover_and_stage_skills(session_maker, packages_dir=pkg_dir)
    assert len(results) == 2
    assert all(r["status"] == "error" for r in results)

    # Database remains completely clean
    with session_maker() as session:
        rows = list(session.execute(sa.select(Skill)).scalars())
        assert len(rows) == 0


def test_idempotent_discovery(session_maker, tmp_path: Path):
    """3. 幂等：重复执行不产生重复注册或主键冲突。"""
    pkg_dir = tmp_path / "skills"
    pkg_dir.mkdir()
    skill_dir = pkg_dir / "idempotent_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: idem-skill
version: 1.0.0
description: Idempotency verification skill
---
Simple content
""",
        encoding="utf-8",
    )

    # First run: should promote
    r1 = discover_and_stage_skills(session_maker, packages_dir=pkg_dir)
    assert len(r1) == 1
    assert r1[0]["status"] == "promoted"

    # Second run: should report exists
    r2 = discover_and_stage_skills(session_maker, packages_dir=pkg_dir)
    assert len(r2) == 1
    assert r2[0]["status"] == "exists"

    # Exactly 1 row in DB
    with session_maker() as session:
        rows = list(session.execute(sa.select(Skill).where(Skill.name == "idem-skill")).scalars())
        assert len(rows) == 1


def test_promotion_gate_cannot_be_bypassed(session_maker, tmp_path: Path):
    """4. 门禁不可绕过：包含高危代码（如危险 import/破坏指令）的技能被安全扫描阻断，严禁 promote。"""
    pkg_dir = tmp_path / "skills"
    pkg_dir.mkdir()
    toxic_dir = pkg_dir / "toxic_skill"
    toxic_dir.mkdir()

    # Skill contains blocking pattern: `import ctypes` and `os.system(...)`
    (toxic_dir / "SKILL.md").write_text(
        """---
name: toxic-skill
version: 1.0.0
description: Attempts to execute hazardous system operations
---
# Toxic Skill
import os
os.system("rm -rf /")
""",
        encoding="utf-8",
    )

    results = discover_and_stage_skills(session_maker, packages_dir=pkg_dir)
    assert len(results) == 1
    assert results[0]["name"] == "toxic-skill"
    # Blocked at promotion gate!
    assert results[0]["status"] == "gate_blocked"

    # In DB, state remains staged with scan_passed = False
    with session_maker() as session:
        row = session.execute(
            sa.select(Skill).where(Skill.name == "toxic-skill")
        ).scalar_one_or_none()
        assert row is not None
        assert row.state == "staged"
        assert row.scan_passed is False
        findings = row.scan_report.get("findings", [])
        assert any(f["severity"] in ("critical", "high") for f in findings)


def test_prompt_bulk_discovery(session_maker, tmp_path: Path):
    """5. 提示词包批量导入：从 Markdown 解析 frontmatter，创建 PromptTemplate，并具备幂等性。"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Markdown prompt
    p1 = prompts_dir / "welcome.md"
    p1.write_text(
        """---
name: welcome-template
description: Welcome greeting template
scope: platform
variables_schema:
  user:
    type: str
    required: true
---
Welcome {{user}}!
""",
        encoding="utf-8",
    )

    # First run
    r1 = discover_and_create_prompts(session_maker, packages_dir=prompts_dir)
    assert len(r1) == 1
    assert r1[0]["status"] == "created"

    with session_maker() as session:
        tmpl = session.execute(
            sa.select(PromptTemplate).where(PromptTemplate.name == "welcome-template")
        ).scalar_one_or_none()
        assert tmpl is not None
        assert tmpl.description == "Welcome greeting template"

    # Second run: idempotent
    r2 = discover_and_create_prompts(session_maker, packages_dir=prompts_dir)
    assert len(r2) == 1
    assert r2[0]["status"] == "exists"
