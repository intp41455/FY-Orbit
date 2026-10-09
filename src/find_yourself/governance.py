import re
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AgentManifest, ChangeProposal, MemoryRecord, ProposalCreate, now, uid
from .store import digest


class Denied(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=120)
    input_usd_per_million: float = Field(gt=0)
    output_usd_per_million: float = Field(gt=0)
    allowed_domains: list[str] = Field(min_length=1)
    provider_label: str = Field(min_length=1, max_length=80)


class Governance:
    TARGETS = {"memory.upsert": "memory", "memory.delete": "memory", "grant.add": "grant",
               "grant.revoke": "grant", "agent.register": "agent", "agent.drain": "agent",
               "skill.stage": "skill", "skill.promote": "skill", "skill.disable": "skill",
               "config.model": "config", "conversation.delete": "conversation",
               "task.merge": "task", "task.release": "task"}

    def __init__(self, store):
        self.store = store

    def validate(self, s, p):
        data, op = p.payload, p.operation
        target = self.store.get(s, p.target_id) if p.target_id else None
        expected_kind = self.TARGETS[op]
        if target and target.kind != expected_kind:
            raise Denied("target_kind", "Target has a different resource type")
        if p.expected_version != (target.version if target else 0):
            raise Denied("stale_version", "Resource changed; create a new proposal")
        if op not in {"memory.upsert", "grant.add", "agent.register", "skill.stage", "config.model"} and not target:
            raise Denied("missing_target", "Resource no longer exists", 404)
        if op == "memory.upsert":
            memory = MemoryRecord.model_validate(data)
            for source in memory.sources:
                row = self.store.get(s, source)
                if not row or row.kind not in {"message", "assessment", "artifact", "memory", "import"}:
                    raise Denied("source_missing", "Every memory source must be an existing content record")
                if row.data.get("deleted"):
                    raise Denied("source_deleted", "Deleted sources cannot support active memories")
            if target and target.data.get("domain") != memory.domain:
                raise Denied("domain_immutable", "Create a separately reviewed record to change domain")
        elif op == "grant.add":
            if set(data) != {"consumer_domain", "source_domain", "record_ids", "expires_at"}:
                raise Denied("grant_schema", "Grant needs exact domains, record IDs, expiry")
            if data["consumer_domain"] not in {"personal", "work"} or data["source_domain"] not in {"personal", "work"}:
                raise Denied("grant_domain", "Invalid domain")
            expiry = datetime.fromisoformat(data["expires_at"])
            if expiry.tzinfo is None or expiry <= now() or expiry > now() + timedelta(days=30):
                raise Denied("grant_expiry", "Grant expiry must be within 30 days")
            if not data["record_ids"] or not isinstance(data["record_ids"], list):
                raise Denied("grant_scope", "Explicit record IDs required; wildcard access is prohibited")
            for key in data["record_ids"]:
                r = self.store.get(s, key)
                if not r or r.data.get("domain") != data["source_domain"]:
                    raise Denied("grant_scope", "Grant source does not match")
        elif op == "agent.register":
            AgentManifest.model_validate(data)
        elif op == "skill.stage":
            from .skills import inspect_skill
            report = inspect_skill(data)
            if report["blocked"]:
                raise Denied("unsafe_skill", "; ".join(report["findings"]))
        elif op == "skill.promote":
            if target.data.get("state") != "staged":
                raise Denied("skill_state", "Only staged skills can be promoted")
            report = self.store.get(s, data.get("evaluation_id"), "evaluation")
            if not report or report.data.get("subject_digest") != digest(target.data["package"]):
                raise Denied("missing_evaluation", "Evaluation must bind this exact skill version")
            if not report.data.get("passed"):
                raise Denied("failed_evaluation", "Skill evaluation did not pass")
        elif op == "config.model":
            ModelConfig.model_validate(data)
            if p.target_id != "model-config":
                raise Denied("config_target", "Model config must target model-config")
        elif op in {"task.merge", "task.release"}:
            required = {"commit_sha", "artifact_digest", "environment", "evidence_ids"}
            if set(data) != required or not re.fullmatch(r"[a-f0-9]{40}", data.get("commit_sha", "")):
                raise Denied("release_binding", "Exact commit, artifact, environment and evidence are required")
            if not data["evidence_ids"]:
                raise Denied("release_evidence", "Release requires verified evidence")
            for key in data["evidence_ids"]:
                row = self.store.get(s, key, "artifact")
                if not row or not row.data.get("verified") or row.data.get("task_id") != target.id:
                    raise Denied("release_evidence", "Evidence is not verified for this task")
        return target

    def propose(self, p: ProposalCreate):
        with self.store.tx() as s:
            self.validate(s, p)
            value = p.model_dump(mode="json")
            value["target_id"] = p.target_id or uid()
            proposal = ChangeProposal(**value, digest=digest(value),
                                      expires_at=now() + timedelta(minutes=p.expires_in_minutes))
            row = self.store.add(s, "proposal", proposal.model_dump(mode="json"), proposal.proposal_id)
            self.store.audit(s, "proposal.created", row.id, {"operation": p.operation, "digest": proposal.digest})
            return self.store.public(row)

    def decide(self, approval):
        with self.store.tx() as s:
            row = self.store.get(s, approval.proposal_id, "proposal")
            if not row:
                raise Denied("not_found", "Proposal not found", 404)
            p = ChangeProposal.model_validate(row.data)
            payload = ProposalCreate.model_validate({k: row.data[k] for k in ProposalCreate.model_fields})
            if p.digest != approval.digest or digest(payload.model_dump(mode="json")) != p.digest:
                raise Denied("digest_mismatch", "Approval does not match exact proposal content")
            if p.status != "pending":
                raise Denied("already_decided", "Proposal already decided")
            if p.expires_at <= now():
                raise Denied("expired", "Approval expired")
            if approval.decision == "approve":
                target = self.validate(s, payload)
                self.apply(s, payload, target)
            self.store.update(row, {**row.data, "status": "applied" if approval.decision == "approve" else "rejected",
                                    "decided_at": now().isoformat()})
            self.store.add(s, "approval", {**approval.model_dump(), "owner": self.store.owner, "at": now().isoformat()})
            self.store.audit(s, "proposal." + approval.decision, row.id, {"digest": p.digest})
            return self.store.public(row)

    def apply(self, s, p, target):
        op, value = p.operation, dict(p.payload)
        if op == "memory.delete":
            value = {**target.data, "active": False, "deleted": True, "content": "", "sources": []}
            self._forget_dependents(s, target.id)
            self.store.add(s, "tombstone", {"target_id": target.id, "at": now().isoformat()})
        elif op == "conversation.delete":
            value = {"domain": target.data["domain"], "deleted": True, "title": "已删除"}
            for msg in self.store.rows(s, "message"):
                if msg.data["conversation_id"] == target.id:
                    self.store.update(msg, {**msg.data, "content": "", "deleted": True})
                    self._forget_dependents(s, msg.id)
                    self.store.add(s, "tombstone", {"target_id": msg.id, "at": now().isoformat()})
            for task in self.store.rows(s, "task"):
                if task.data.get("conversation_id") == target.id:
                    self.store.update(task, {**task.data, "goal": "[deleted]", "result": None,
                                            "status": "cancelled", "deleted": True})
                    self.store.add(s, "tombstone", {"target_id": task.id, "at": now().isoformat()})
            self.store.add(s, "tombstone", {"target_id": target.id, "at": now().isoformat()})
        elif op == "grant.revoke":
            value = {**target.data, "active": False}
        elif op == "grant.add":
            value["active"] = True
        elif op == "agent.register":
            value.update(state="registered", healthy=False)
        elif op == "agent.drain":
            value = {**target.data, "state": "draining", "healthy": False}
        elif op == "skill.stage":
            value = {"package": value, "state": "staged"}
        elif op == "skill.promote":
            value = {**target.data, "state": "active", "evaluation_id": p.payload["evaluation_id"]}
        elif op == "skill.disable":
            value = {**target.data, "state": "disabled"}
        elif op in {"task.merge", "task.release"}:
            # A permit is not evidence that GitHub or production was modified.
            self.store.add(s, "permit", {"operation": op, "task_id": target.id, **value,
                                        "state": "authorized", "expires_at": (now() + timedelta(minutes=30)).isoformat()})
            return
        if target:
            if op != "memory.delete" and not target.data.get("deleted"):
                self.store.add(s, "revision", {"target_id": target.id, "version": target.version, "data": target.data})
            self.store.update(target, value)
        else:
            self.store.add(s, self.TARGETS[op], value, p.target_id)

    def _forget_dependents(self, s, key):
        for memory in self.store.rows(s, "memory"):
            if key in memory.data.get("sources", []):
                self.store.update(memory, {**memory.data, "active": False, "content": "", "deleted": True})
                self.store.add(s, "tombstone", {"target_id": memory.id, "at": now().isoformat()})
        for rev in self.store.rows(s, "revision"):
            if rev.data.get("target_id") == key or key in rev.data.get("data", {}).get("sources", []):
                self.store.update(rev, {"target_id": rev.data["target_id"], "redacted": True})

    def readable(self, s, row, consumer_domain):
        if row.data.get("deleted") or row.data.get("active") is False:
            return False
        source_domain = row.data.get("domain")
        if source_domain == consumer_domain or (source_domain == "shared" and row.kind == "memory"):
            return True
        for grant in self.store.rows(s, "grant"):
            g = grant.data
            if (g.get("active") and g["consumer_domain"] == consumer_domain and g["source_domain"] == source_domain
                    and row.id in g["record_ids"] and datetime.fromisoformat(g["expires_at"]) > now()):
                return True
        return False

    def retrieve(self, query, domain, limit=8, max_chars=10000):
        with self.store.tx() as s:
            hits = []
            for kind in ("memory", "message", "import"):
                for row in self.store.rows(s, kind):
                    if not self.readable(s, row, domain):
                        continue
                    content = row.data.get("content", "")
                    # Deterministic lexical fallback. Vector search is separately configurable.
                    terms = [query[i:i+2].lower() for i in range(max(1, len(query)-1))]
                    score = sum(term in content.lower() for term in set(terms))
                    if score:
                        hits.append({"id": row.id, "version": row.version, "kind": kind,
                                     "content": content, "category": row.data.get("category", row.data.get("role")),
                                     "score": score, "sources": row.data.get("sources", [row.id])})
            hits.sort(key=lambda x: (-x["score"], x["id"]))
            result, used = [], 0
            for hit in hits[:limit]:
                remaining = max_chars-used
                if remaining <= 0:
                    break
                hit["content"] = hit["content"][:remaining]
                result.append(hit)
                used += len(hit["content"])
            return result
