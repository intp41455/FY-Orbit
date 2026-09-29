import re
import yaml
from .store import digest


def inspect_skill(package):
    required = {"name", "version", "source", "license", "skill_md", "permissions", "domain"}
    findings = []
    if set(package) != required:
        return {"blocked": True, "findings": ["Package fields must exactly match the manifest schema"]}
    text = package["skill_md"]
    if not isinstance(text, str) or len(text) > 50000:
        return {"blocked": True, "findings": ["Invalid skill size"]}
    if not text.startswith("---\n"):
        findings.append("Missing SKILL.md frontmatter")
    else:
        try:
            header = yaml.safe_load(text.split("---", 2)[1])
            if not isinstance(header, dict) or header.get("name") != package["name"] or not header.get("description"):
                findings.append("Frontmatter needs matching name and description")
        except (yaml.YAMLError, IndexError):
            findings.append("Invalid YAML")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", package["name"]):
        findings.append("Invalid skill name")
    if package["domain"] not in {"personal", "work", "shared"}:
        findings.append("Invalid domain")
    if package["permissions"]:
        findings.append("Initial instruction-skill runner permits no direct tool permissions")
    if not package["source"] or not package["license"]:
        findings.append("Source and license are mandatory")
    suspicious = [r"ignore (all |previous |system )*instructions", r"disable.*(audit|approval|sandbox)",
                  r"(curl|wget).*\|.*(sh|bash)", r"(api[_ -]?key|secret).*https?://", r"rm\s+-rf", r"powershell.*-enc"]
    for pattern in suspicious:
        if re.search(pattern, text, re.I):
            findings.append("Potential executable or instruction injection content: " + pattern)
    return {"blocked": bool(findings), "findings": findings, "digest": digest(package),
            "scope": "Static screening only; no code executed, no claim of complete safety"}


def evaluate_skill(store, skill_id):
    from .governance import Denied
    with store.tx() as s:
        skill = store.get(s, skill_id, "skill")
        if not skill or skill.data.get("state") != "staged":
            raise Denied("not_staged", "Approve staging first")
        report = inspect_skill(skill.data["package"])
        # Format/security pass is intentionally distinct from real task efficacy.
        row = store.add(s, "evaluation", {"subject_digest": digest(skill.data["package"]),
                        "static_passed": not report["blocked"], "passed": False,
                        "findings": report["findings"], "functional_status": "needs_owner_review",
                        "scope": "Instruction package, no scripts or dependency installation"})
        store.audit(s, "skill.screened", skill_id, {"evaluation_id": row.id})
        return store.public(row)
