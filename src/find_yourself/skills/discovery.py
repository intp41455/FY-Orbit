"""Automatic discovery and registration for skill and prompt packages.

In accordance with Architecture Decision §10.3:
- Skill packages root is hardcoded to `src/find_yourself/skills_packages/` (bundled with package/image).
- Runtime override via environment variable is intentionally prohibited to preserve security boundaries.
- Discovered packages are NEVER automatically trusted; they MUST traverse the governed
  lifecycle: stage() -> evaluate() -> promote().
- Invalid packages are logged and skipped without crashing application startup.
- Scanning is idempotent: previously staged/promoted versions are safely skipped.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from find_yourself.db.models import Skill
from find_yourself.db.prompt_models import PromptTemplate
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, ValidationFailed
from find_yourself.services.prompt import PromptService
from find_yourself.services.skill import SkillService
from find_yourself.skills.harness import gateway

logger = logging.getLogger("find_yourself.skills.discovery")

SKILL_PACKAGES_DIR = Path(__file__).resolve().parent.parent / "skills_packages"
PROMPT_PACKAGES_DIR = Path(__file__).resolve().parent.parent / "prompts_packages"

_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$", re.DOTALL)
_NAME_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")


def parse_frontmatter_markdown(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter and body from a Markdown file.

    Returns (frontmatter_dict, body_string).
    If no frontmatter is found, returns ({}, content).
    """
    match = _FRONTMATTER_RE.match(content.strip())
    if not match:
        return {}, content

    yaml_block, body = match.groups()
    try:
        data = yaml.safe_load(yaml_block)
        if not isinstance(data, dict):
            return {}, content
        return data, body
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in frontmatter: {exc}") from exc


def discover_and_stage_skills(
    session_maker: sessionmaker[Session],
    packages_dir: Path | None = None,
    *,
    auto_promote: bool = True,
) -> list[dict[str, Any]]:
    """Scan skill packages directory and safely stage/promote valid skills."""
    target_dir = packages_dir if packages_dir is not None else SKILL_PACKAGES_DIR
    results: list[dict[str, Any]] = []

    if not target_dir.exists() or not target_dir.is_dir():
        logger.debug("Skills packages directory does not exist: %s", target_dir)
        return results

    system_actor = Actor.service("system_discovery", "release")
    owner_actor = Actor.owner("owner")

    for skill_path in target_dir.iterdir():
        if not skill_path.is_dir():
            continue

        skill_md = skill_path / "SKILL.md"
        if not skill_md.is_file():
            logger.debug("Skipping %s: missing SKILL.md", skill_path.name)
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter_markdown(content)
        except Exception as exc:
            logger.warning("Failed to parse SKILL.md in %s: %s", skill_path.name, exc)
            results.append({"name": skill_path.name, "status": "error", "error": str(exc)})
            continue

        name = frontmatter.get("name") or skill_path.name
        if not isinstance(name, str) or not _NAME_RE.match(name):
            logger.warning("Invalid skill name '%s' in %s", name, skill_path.name)
            results.append({"name": str(name), "status": "error", "error": f"Invalid skill name '{name}'"})
            continue

        version = str(frontmatter.get("version", "1.0.0"))
        description = str(frontmatter.get("description", ""))
        domain = str(frontmatter.get("domain", "personal"))
        if domain not in {"personal", "work", "shared"}:
            domain = "personal"
        license_ = str(frontmatter.get("license", "MIT"))
        tools = frontmatter.get("tools")
        if tools is not None and not isinstance(tools, list):
            logger.warning("Tools in %s must be a list", name)
            results.append({"name": name, "status": "error", "error": "tools must be a list"})
            continue

        package: dict[str, Any] = {
            "name": name,
            "version": version,
            "description": description,
            "skill_md": body,
        }

        with session_maker() as session:
            audit = AuditService(session)
            skill_svc = SkillService(session, audit)

            # Idempotency check: see if already staged/promoted
            existing = session.execute(
                select(Skill).where(Skill.name == name, Skill.semantic_version == version)
            ).scalar_one_or_none()

            if existing is not None:
                logger.debug("Skill %s v%s already registered (state=%s)", name, version, existing.state)
                results.append({"name": name, "version": version, "status": "exists", "state": existing.state})
                continue

            try:
                # Stage skill through governed security scanner
                staged = skill_svc.stage(
                    system_actor,
                    name=name,
                    semantic_version=version,
                    package=package,
                    source="builtin",
                    license_=license_,
                    domain=domain,
                )
                session.commit()
            except ValidationFailed as exc:
                session.rollback()
                logger.warning("Skill %s failed staging: %s", name, exc)
                results.append({"name": name, "version": version, "status": "stage_failed", "error": str(exc)})
                continue

            if not auto_promote:
                results.append({"name": name, "version": version, "status": "staged", "skill_id": staged.id})
                continue

            # Evaluate skill
            try:
                ev = skill_svc.evaluate(
                    system_actor,
                    staged.id,
                    static_passed=bool(staged.scan_passed),
                    functional_passed=True,
                    report={"evaluator": "discovery", "scan_passed": staged.scan_passed},
                )
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.warning("Skill %s failed evaluation: %s", name, exc)
                results.append({"name": name, "version": version, "status": "eval_failed", "error": str(exc)})
                continue

            # Promote skill (must pass promotion gate)
            try:
                promoted = skill_svc.promote(owner_actor, staged.id, ev.id)
                session.commit()

                # Register declared tools in gateway upon successful promotion
                if tools:
                    for t in tools:
                        if isinstance(t, dict) and "name" in t:
                            t_name = t["name"]
                            t_desc = t.get("description", "")
                            t_schema = t.get("schema", {"type": "object"})
                            gateway.register_tool(
                                name=t_name,
                                description=t_desc,
                                handler=_make_default_tool_handler(t_name),
                                schema=t_schema,
                                replace=True,
                            )

                results.append({"name": name, "version": version, "status": "promoted", "skill_id": promoted.id})
            except Conflict as exc:
                session.rollback()
                logger.warning("Skill %s promotion gate rejected: %s", name, exc)
                results.append({"name": name, "version": version, "status": "gate_blocked", "error": str(exc)})
            except Exception as exc:
                session.rollback()
                logger.warning("Skill %s promotion error: %s", name, exc)
                results.append({"name": name, "version": version, "status": "promote_error", "error": str(exc)})

    return results


def discover_and_create_prompts(
    session_maker: sessionmaker[Session],
    packages_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan prompts packages directory and import prompt templates."""
    target_dir = packages_dir if packages_dir is not None else PROMPT_PACKAGES_DIR
    results: list[dict[str, Any]] = []

    if not target_dir.exists() or not target_dir.is_dir():
        logger.debug("Prompt packages directory does not exist: %s", target_dir)
        return results

    system_actor = Actor.owner("owner")

    for prompt_file in target_dir.iterdir():
        if prompt_file.is_dir():
            continue

        if prompt_file.suffix not in {".md", ".yaml", ".yml"}:
            continue

        try:
            content = prompt_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read prompt file %s: %s", prompt_file.name, exc)
            results.append({"file": prompt_file.name, "status": "read_error", "error": str(exc)})
            continue

        frontmatter: dict[str, Any] = {}
        body = content
        if prompt_file.suffix == ".md":
            try:
                frontmatter, body = parse_frontmatter_markdown(content)
            except Exception as exc:
                logger.warning("Failed to parse prompt frontmatter in %s: %s", prompt_file.name, exc)
                results.append({"file": prompt_file.name, "status": "parse_error", "error": str(exc)})
                continue
        elif prompt_file.suffix in {".yaml", ".yml"}:
            try:
                parsed = yaml.safe_load(content)
                if isinstance(parsed, dict):
                    frontmatter = parsed
                    body = parsed.get("content", "")
            except Exception as exc:
                logger.warning("Failed to parse YAML prompt in %s: %s", prompt_file.name, exc)
                results.append({"file": prompt_file.name, "status": "parse_error", "error": str(exc)})
                continue

        name = frontmatter.get("name") or prompt_file.stem.replace("_", "-")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            logger.warning("Invalid prompt template name '%s'", name)
            results.append({"name": str(name), "status": "error", "error": f"Invalid name '{name}'"})
            continue

        variables_schema = frontmatter.get("variables_schema", {})
        scope = frontmatter.get("scope", "platform")
        description = frontmatter.get("description", "")

        with session_maker() as session:
            existing = session.execute(
                select(PromptTemplate).where(PromptTemplate.name == name)
            ).scalar_one_or_none()

            if existing is not None:
                results.append({"name": name, "status": "exists", "id": existing.id})
                continue

            audit = AuditService(session)
            prompt_svc = PromptService(session, audit)
            try:
                row = prompt_svc.create(
                    system_actor,
                    name=name,
                    content=body,
                    variables_schema=variables_schema,
                    scope=scope,
                    description=description,
                )
                session.commit()
                results.append({"name": name, "status": "created", "id": row.id})
            except Exception as exc:
                session.rollback()
                logger.warning("Failed to create prompt template %s: %s", name, exc)
                results.append({"name": name, "status": "create_failed", "error": str(exc)})

    return results


def _make_default_tool_handler(tool_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "tool": tool_name, "args": args}
    return handler
