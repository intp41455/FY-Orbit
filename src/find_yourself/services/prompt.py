"""Prompt template library service (行动项 #13 / 工单 P1-06).

Deterministic rendering contract:

* ``{{var}}`` / ``{{var|default}}`` whitelist interpolation only — the variable
  grammar is a regex, there is NO expression evaluation, NO nested includes
  and no code execution path (injection surface / approval bypass).
* Rendering is pure string splicing: no clock, no randomness, no network. Call
 ers that need a timestamp pass it as a ``now`` variable (local_agents fix).
* Same template version + same variables => byte-identical output and hashes.
* Audit: every non-preview render writes ``prompt_render_logs`` with
  ``variables_hash = sha256(canonical variable JSON)`` — never plaintext.

Governance: content/schema changes, activation and disable go through
``ProposalService`` (operations ``prompt.stage`` / ``prompt.activate`` /
``prompt.disable``). ``apply_approved`` is the deterministic executor run after
an owner decision; it re-verifies status and digest before touching data.
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Proposal
from ..db.prompt_models import (
    PROMPT_SCOPES,
    PROMPT_VAR_TYPES,
    PromptRenderLog,
    PromptTemplate,
    PromptVersion,
)
from .actor import Actor
from .audit import AuditService
from .errors import Conflict, NotFound, ValidationFailed
from .hasher import content_hash, digest

# Escape hatch for literal braces (design §2): "\{\{" renders as "{{" — each
# brace is escaped individually, so "\{" -> "{" and "\}" -> "}".
_ESCAPED_OPEN = re.compile(r"\\\{")
_ESCAPED_CLOSE = re.compile(r"\\\}")

# Whitelist grammar: {{ name }} optionally {{ name | literal default }}.
# No expressions, no nested {{ }} inside the default, no recursion.
_VAR_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|([^{}]*?))?\s*\}\}")

_NAME_RE = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,198}[A-Za-z0-9_])?$")

PROMPT_PROPOSAL_OPS = ("prompt.stage", "prompt.activate", "prompt.disable")


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    template_id: str
    name: str
    version: int
    content_hash: str
    variables_hash: str


def parse_template_variables(text: str) -> dict[str, str | None]:
    """Extract declared-by-usage variables: name -> default (None if absent)."""
    found: dict[str, str | None] = {}
    for match in _VAR_TOKEN.finditer(text):
        name, default = match.group(1), match.group(2)
        if name not in found or found[name] is None:
            found[name] = default
    return found


def validate_variables_schema(schema: dict) -> None:
    """Structural validation of variables_schema: {name: {type, required, default}}."""
    if not isinstance(schema, dict):
        raise ValidationFailed("bad_schema", "variables_schema must be an object")
    for name, spec in schema.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
            raise ValidationFailed("bad_variable_name", f"Illegal variable name: {name}")
        spec = spec or {}
        if not isinstance(spec, dict):
            raise ValidationFailed("bad_schema", f"Variable {name} spec must be an object")
        vtype = spec.get("type", "str")
        if vtype not in PROMPT_VAR_TYPES:
            raise ValidationFailed(
                "bad_variable_type",
                f"Variable {name} type must be one of {PROMPT_VAR_TYPES}, got {vtype!r}",
            )


def _check_type(name: str, value: Any, vtype: str) -> Any:
    if vtype == "str":
        if not isinstance(value, str):
            raise ValidationFailed("type_mismatch", f"Variable {name} must be str")
        return value
    if vtype == "bool":
        if not isinstance(value, bool):
            raise ValidationFailed("type_mismatch", f"Variable {name} must be bool")
        return value
    if vtype == "int":
        # bool is an int subclass in Python — exclude it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationFailed("type_mismatch", f"Variable {name} must be int")
        return value
    if vtype == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationFailed("type_mismatch", f"Variable {name} must be float")
        return float(value)
    if vtype == "list":
        if not isinstance(value, list):
            raise ValidationFailed("type_mismatch", f"Variable {name} must be list")
        return value
    raise ValidationFailed("bad_variable_type", f"Variable {name} has unknown type {vtype!r}")


def normalize_variables(schema: dict, variables: dict[str, Any]) -> dict[str, Any]:
    """Validate supplied variables against the schema and apply defaults.

    Returns the normalized variable mapping used for rendering/hashing:
    declared variables only, defaults applied, types coerced. Extra supplied
    variables are ignored (design §2). Missing required variable without a
    default raises ValidationFailed (HTTP 422; zero cost, zero log).
    """
    if not isinstance(variables, dict):
        raise ValidationFailed("bad_variables", "variables must be an object")
    normalized: dict[str, Any] = {}
    for name, spec in schema.items():
        spec = spec or {}
        vtype = spec.get("type", "str")
        required = bool(spec.get("required", False))
        if name in variables and variables[name] is not None:
            normalized[name] = _check_type(name, variables[name], vtype)
            continue
        if "default" in spec and spec["default"] is not None:
            normalized[name] = _check_type(name, spec["default"], vtype)
            continue
        if required:
            raise ValidationFailed(
                "missing_variable",
                f"Variable {name} is required and has no default",
            )
    return normalized


def render_text(template_content: str, schema: dict, variables: dict[str, Any]) -> str:
    """Deterministic substitution on an already-normalized variable mapping."""
    out = template_content
    out = _ESCAPED_OPEN.sub("\x00LB\x00", out)
    out = _ESCAPED_CLOSE.sub("\x00RB\x00", out)
    def _sub(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        if name in variables:
            value = variables[name]
        elif default is not None:
            value = default
        else:
            # Unreachable when normalize_variables ran; keep render_text total.
            raise ValidationFailed("missing_variable", f"Variable {name} is required")
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return ", ".join("" if v is None else str(v) for v in value)
        return str(value)

    out = _VAR_TOKEN.sub(_sub, out)
    return out.replace("\x00LB\x00", "{").replace("\x00RB\x00", "}")


def variables_hash(normalized: dict[str, Any]) -> str:
    return digest(normalized)


class PromptService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    # -- lookup -----------------------------------------------------------
    def get_template(self, name: str) -> PromptTemplate:
        row = self.s.execute(
            select(PromptTemplate).where(PromptTemplate.name == name)
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("prompt_not_found", f"Prompt template {name!r} not found")
        return row

    def get_versions(self, name: str) -> list[PromptVersion]:
        tpl = self.get_template(name)
        return list(self.s.execute(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == tpl.id)
            .order_by(PromptVersion.version.asc())
        ).scalars())

    # -- create (draft; activation still requires a proposal) --------------
    def create(
        self, actor: Actor, *, name: str, content: str,
        variables_schema: dict | None = None, scope: str = "platform",
        description: str = "", owner: str | None = None,
    ) -> PromptTemplate:
        actor.require_authenticated()
        if not isinstance(name, str) or not _NAME_RE.match(name or ""):
            raise ValidationFailed("bad_name", "Template name must be letters/digits/_ . -")
        if scope not in PROMPT_SCOPES:
            raise ValidationFailed("bad_scope", f"scope must be one of {PROMPT_SCOPES}")
        schema = variables_schema or {}
        validate_variables_schema(schema)
        self._check_unknown_variables(content, schema)
        existing = self.s.execute(
            select(PromptTemplate).where(PromptTemplate.name == name)
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict("prompt_exists", f"Prompt template {name!r} already exists")
        row = PromptTemplate(
            id=uuid4().hex, name=name, latest_version=1,
            variables_schema=schema, owner=owner or actor.owner_id or actor.service_id or "",
            scope=scope, description=description or "", is_active=False,
        )
        self.s.add(row)
        self.s.flush()
        self.s.add(PromptVersion(
            id=uuid4().hex, prompt_id=row.id, version=1,
            content=content, content_hash=content_hash(content),
            variables_schema=schema, created_by=actor.owner_id or actor.service_id or "system",
        ))
        self.s.flush()
        self.audit.append(actor, "prompt.created", row.id,
                          {"name": name, "scope": scope, "content_hash": content_hash(content)[:12]})
        return row

    # -- metadata update (content/schema changes must go through proposals) --
    def update_meta(
        self, actor: Actor, name: str, *, description: str | None = None,
        scope: str | None = None, expected_version: int | None = None,
    ) -> PromptTemplate:
        actor.require_authenticated()
        row = self.get_template(name)
        if expected_version is not None and expected_version != row.version:
            raise Conflict("version_conflict",
                           f"Template is at version {row.version}, expected {expected_version}")
        if scope is not None:
            if scope not in PROMPT_SCOPES:
                raise ValidationFailed("bad_scope", f"scope must be one of {PROMPT_SCOPES}")
            row.scope = scope
        if description is not None:
            row.description = description
        row.version += 1
        self.s.flush()
        self.audit.append(actor, "prompt.meta_updated", row.id,
                          {"description": description is not None, "scope": row.scope})
        return row

    @staticmethod
    def _check_unknown_variables(content: str, schema: dict) -> None:
        declared = set(schema.keys())
        used = set(parse_template_variables(content).keys())
        unknown = used - declared
        if unknown:
            raise ValidationFailed(
                "unknown_variables",
                f"Template uses variables not declared in schema: {sorted(unknown)}",
            )

    # -- render (deterministic) --------------------------------------------
    def render(
        self, name: str, variables: dict[str, Any], *, version: int | None = None,
        task_id: str | None = None, scope: str | None = None, log: bool = True,
    ) -> RenderedPrompt:
        tpl = self.get_template(name)
        if not tpl.is_active:
            raise Conflict("prompt_not_active",
                           f"Prompt template {name!r} is not active; activate it first")
        if version is None:
            version = tpl.latest_version
        ver = self.s.execute(
            select(PromptVersion).where(
                PromptVersion.prompt_id == tpl.id, PromptVersion.version == version)
        ).scalar_one_or_none()
        if ver is None:
            raise NotFound("prompt_version_not_found",
                           f"Prompt template {name!r} has no version {version}")
        normalized = normalize_variables(ver.variables_schema or {}, variables or {})
        text = render_text(ver.content, ver.variables_schema or {}, normalized)
        vh = variables_hash(normalized)
        ch = content_hash(text)
        if log:
            self.s.add(PromptRenderLog(
                id=uuid4().hex, template_name=name, version=version,
                variables_hash=vh, scope=scope, task_id=task_id,
            ))
            self.s.flush()
        return RenderedPrompt(
            text=text, template_id=tpl.id, name=name, version=version,
            content_hash=ch, variables_hash=vh,
        )

    # -- proposal governance ------------------------------------------------
    def propose_stage(self, actor: Actor, name: str, *, content: str,
                      variables_schema: dict | None, reason: str, rollback: str = "",
                      expires_in_minutes: int = 30) -> Proposal:
        """Propose a new version (create the template draft if absent)."""
        actor.require_authenticated()
        schema = variables_schema
        tpl: PromptTemplate | None = self.s.execute(
            select(PromptTemplate).where(PromptTemplate.name == name)
        ).scalar_one_or_none()
        if tpl is None:
            if not isinstance(name, str) or not _NAME_RE.match(name or ""):
                raise ValidationFailed("bad_name", "Template name must be letters/digits/_ . -")
            if not schema:
                raise ValidationFailed("bad_schema", "New templates must declare variables_schema")
        else:
            schema = schema if schema is not None else (tpl.variables_schema or {})
        validate_variables_schema(schema)
        self._check_unknown_variables(content, schema)
        return self._proposal_svc(actor).create(
            actor, operation="prompt.stage",
            payload={"name": name, "content": content, "variables_schema": schema},
            reason=reason, rollback=rollback, target_id=name,
            expected_version=tpl.version if tpl is not None else 0,
            expires_in_minutes=expires_in_minutes,
        )

    def propose_activate(self, actor: Actor, name: str, *, version: int, reason: str,
                         rollback: str = "", expires_in_minutes: int = 30) -> Proposal:
        actor.require_authenticated()
        tpl = self.get_template(name)
        ver = self.s.execute(
            select(PromptVersion).where(
                PromptVersion.prompt_id == tpl.id, PromptVersion.version == version)
        ).scalar_one_or_none()
        if ver is None:
            raise NotFound("prompt_version_not_found",
                           f"Prompt template {name!r} has no version {version}")
        return self._proposal_svc(actor).create(
            actor, operation="prompt.activate",
            payload={"name": name, "version": version},
            reason=reason, rollback=rollback, target_id=name,
            expected_version=tpl.version,
            expires_in_minutes=expires_in_minutes,
        )

    def propose_disable(self, actor: Actor, name: str, *, reason: str,
                        rollback: str = "", expires_in_minutes: int = 30) -> Proposal:
        actor.require_authenticated()
        tpl = self.get_template(name)
        return self._proposal_svc(actor).create(
            actor, operation="prompt.disable",
            payload={"name": name}, reason=reason, rollback=rollback,
            target_id=name, expected_version=tpl.version,
            expires_in_minutes=expires_in_minutes,
        )

    def _proposal_svc(self, actor: Actor):
        # Imported lazily to avoid a circular import at module load.
        from .proposal import ProposalService
        return ProposalService(self.s, self.audit)

    # -- deterministic executor (after owner decision) ----------------------
    def apply_approved(self, actor: Actor, proposal_id: str, client_digest: str) -> dict:
        """Execute an approved prompt.* proposal exactly once.

        Verifies: owner-only caller, proposal exists, operation is a prompt op,
        status is ``executed`` (decided via /api/proposals), digest matches the
        stored canonical digest AND the client-supplied digest. Idempotent for
        an already-applied stage (same content hash) and activate/disable.
        """
        actor.require_owner()
        p = self.s.get(Proposal, proposal_id)
        if p is None:
            raise NotFound("proposal_not_found", "Proposal not found")
        if p.operation not in PROMPT_PROPOSAL_OPS:
            raise ValidationFailed("not_prompt_operation",
                                   f"Operation {p.operation} is not a prompt operation")
        if p.status != "executed":
            raise Conflict("proposal_not_executed",
                           f"Proposal status is {p.status!r}; approve it via /api/proposals first")
        from .proposal import ProposalService
        recomputed = ProposalService(self.s, self.audit).compute_digest(p)
        if p.digest != recomputed or client_digest != p.digest:
            raise Conflict("digest_mismatch", "Apply digest does not match the proposal")

        payload = p.payload or {}
        name = payload.get("name") or p.target_id
        if p.operation == "prompt.stage":
            return self._apply_stage(actor, p, name, payload)
        if p.operation == "prompt.activate":
            return self._apply_activate(actor, p, name, payload)
        return self._apply_disable(actor, p, name)

    def _apply_stage(self, actor: Actor, p: Proposal, name: str, payload: dict) -> dict:
        content = payload["content"]
        schema = payload.get("variables_schema") or {}
        validate_variables_schema(schema)
        self._check_unknown_variables(content, schema)
        ch = content_hash(content)
        tpl = self.s.execute(
            select(PromptTemplate).where(PromptTemplate.name == name)
        ).scalar_one_or_none()
        if tpl is None:
            tpl = PromptTemplate(
                id=uuid4().hex, name=name, latest_version=1, variables_schema=schema,
                owner=actor.owner_id, scope="platform", description="", is_active=False,
            )
            self.s.add(tpl)
            self.s.flush()
            next_version = 1
        else:
            existing = self.s.execute(
                select(PromptVersion).where(
                    PromptVersion.prompt_id == tpl.id, PromptVersion.content_hash == ch)
            ).scalar_one_or_none()
            if existing is not None:
                # Idempotent re-apply: same content already staged.
                self.audit.append(actor, "prompt.applied_idempotent", tpl.id,
                                  {"proposal_id": p.id, "operation": p.operation})
                return {"template": name, "version": existing.version,
                        "content_hash": ch, "idempotent": True}
            next_version = 1 + max(
                self.s.execute(
                    select(PromptVersion.version).where(PromptVersion.prompt_id == tpl.id)
                ).scalars(), default=0)
        self.s.add(PromptVersion(
            id=uuid4().hex, prompt_id=tpl.id, version=next_version,
            content=content, content_hash=ch, variables_schema=schema,
            created_by=actor.owner_id,
        ))
        tpl.variables_schema = schema
        tpl.version += 1
        self.s.flush()
        self.audit.append(actor, "prompt.staged", tpl.id,
                          {"proposal_id": p.id, "version": next_version,
                           "content_hash": ch[:12]})
        return {"template": name, "version": next_version, "content_hash": ch}

    def _apply_activate(self, actor: Actor, p: Proposal, name: str, payload: dict) -> dict:
        tpl = self.get_template(name)
        version = int(payload["version"])
        ver = self.s.execute(
            select(PromptVersion).where(
                PromptVersion.prompt_id == tpl.id, PromptVersion.version == version)
        ).scalar_one_or_none()
        if ver is None:
            raise NotFound("prompt_version_not_found",
                           f"Prompt template {name!r} has no version {version}")
        tpl.latest_version = version
        tpl.is_active = True
        tpl.version += 1
        self.s.flush()
        self.audit.append(actor, "prompt.activated", tpl.id,
                          {"proposal_id": p.id, "version": version})
        return {"template": name, "version": version, "is_active": True}

    def _apply_disable(self, actor: Actor, p: Proposal, name: str) -> dict:
        tpl = self.get_template(name)
        tpl.is_active = False
        tpl.version += 1
        self.s.flush()
        self.audit.append(actor, "prompt.disabled", tpl.id, {"proposal_id": p.id})
        return {"template": name, "is_active": False}
