"""ProfileService: 04 个人与对象多维画像 (Personal & Object Multi-dimensional Profile).

Implements:
- Subject management (本人 vs 非本人对象, e.g. person, project, topic)
- Multi-format document import (text, markdown, conversation JSON) with speaker & segment coordinates
- Deterministic claims & evidence extraction
- Profile JSON Schema 1.0 synthesis (clusters, metrics, edges, limitations)
- User feedback (accept, edit, reject, uncertain)
- Cascading deletion with tombstone auditing
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from find_yourself.db.models import (
    AuditEvent, ProfileEvidence, ProfileFeedback, ProfileImport, ProfileRevision,
    ProfileRun, ProfileSubject, SourceSegment, Tombstone,
)
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed


class ProfileService:
    def __init__(self, session: Session, audit: AuditService):
        self.session = session
        self.audit = audit

    # -----------------------------------------------------------------------
    # Subject Management
    # -----------------------------------------------------------------------
    def create_subject(
        self,
        actor: Actor,
        label: str,
        kind: str = "self",
        description: str = "",
        confirmed: bool = False,
    ) -> ProfileSubject:
        actor.require_owner()
        kind = kind.lower().strip()
        if kind not in ("self", "person", "project", "org", "work", "topic", "other"):
            raise ValidationFailed(f"Invalid subject kind: {kind}")

        # If kind is self, check if owner already has a self subject
        if kind == "self":
            existing = self.session.execute(
                select(ProfileSubject).where(
                    ProfileSubject.owner_id == actor.owner_id,
                    ProfileSubject.kind == "self",
                )
            ).scalars().first()
            if existing:
                return existing

        subject_id = f"subj-{uuid4().hex[:12]}"
        subj = ProfileSubject(
            id=subject_id,
            owner_id=actor.owner_id,
            kind=kind,
            label=label.strip(),
            description=description.strip(),
            confirmed=confirmed or (kind == "self"),
        )
        self.session.add(subj)
        self.audit.append(
            actor,
            "profile.subject.create",
            target=subject_id,
            details={"kind": kind, "label": label},
        )
        self.session.flush()
        return subj

    def list_subjects(self, actor: Actor) -> list[ProfileSubject]:
        actor.require_owner()
        stmt = (
            select(ProfileSubject)
            .where(ProfileSubject.owner_id == actor.owner_id)
            .order_by(ProfileSubject.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_subject(self, actor: Actor, subject_id: str) -> ProfileSubject:
        actor.require_owner()
        subj = self.session.get(ProfileSubject, subject_id)
        if not subj or subj.owner_id != actor.owner_id:
            raise NotFound(f"Profile subject not found: {subject_id}")
        return subj

    # -----------------------------------------------------------------------
    # Document Import & Segmentation
    # -----------------------------------------------------------------------
    def import_document(
        self,
        actor: Actor,
        content: str,
        filename: str = "input.txt",
        subject_id: str | None = None,
        privacy_domain: str = "personal",
    ) -> ProfileImport:
        actor.require_owner()
        if privacy_domain not in ("personal", "work"):
            raise ValidationFailed(f"Invalid privacy domain: {privacy_domain}")

        target_subject = None
        if subject_id:
            target_subject = self.get_subject(actor, subject_id)

        content_bytes = content.encode("utf-8")
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        import_id = f"imp-{uuid4().hex[:12]}"

        # Segment parser: supports structured conversation JSON or text/markdown lines
        segments: list[SourceSegment] = []
        speakers: set[str] = set()

        content_stripped = content.strip()
        parsed_json_msgs = None
        if (content_stripped.startswith("[") and content_stripped.endswith("]")) or (
            content_stripped.startswith("{") and content_stripped.endswith("}")
        ):
            try:
                data = json.loads(content_stripped)
                if isinstance(data, list):
                    parsed_json_msgs = data
                elif isinstance(data, dict) and "messages" in data and isinstance(data["messages"], list):
                    parsed_json_msgs = data["messages"]
            except Exception:
                parsed_json_msgs = None

        if parsed_json_msgs is not None:
            for idx, msg in enumerate(parsed_json_msgs, start=1):
                if isinstance(msg, dict):
                    spk = str(msg.get("speaker") or msg.get("role") or "observed").strip()
                    txt = str(msg.get("text") or msg.get("content") or "").strip()
                    loc = str(msg.get("locator") or f"L{idx}")
                else:
                    spk = "observed"
                    txt = str(msg).strip()
                    loc = f"L{idx}"
                if not txt:
                    continue
                speakers.add(spk)
                seg_id = f"seg-{uuid4().hex[:12]}"
                seg_hash = hashlib.sha256(txt.encode("utf-8")).hexdigest()
                segments.append(
                    SourceSegment(
                        id=seg_id,
                        import_id=import_id,
                        speaker=spk,
                        raw_speaker=spk,
                        text_content=txt,
                        content_hash=seg_hash,
                        locator=loc,
                    )
                )
        else:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            speaker_pattern = re.compile(r"^(?:\[.*?\]\s*)?([A-Za-z0-9_\u4e00-\u9fa5]{1,20})[:：]\s*(.+)$")

            for idx, line in enumerate(lines, start=1):
                m = speaker_pattern.match(line)
                if m:
                    spk = m.group(1).strip()
                    txt = m.group(2).strip()
                else:
                    spk = "self" if (target_subject and target_subject.kind == "self") else "observed"
                    txt = line

                speakers.add(spk)
                seg_id = f"seg-{uuid4().hex[:12]}"
                seg_hash = hashlib.sha256(txt.encode("utf-8")).hexdigest()
                segments.append(
                    SourceSegment(
                        id=seg_id,
                        import_id=import_id,
                        speaker=spk,
                        raw_speaker=spk,
                        text_content=txt,
                        content_hash=seg_hash,
                        locator=f"L{idx}",
                    )
                )

        candidates = [
            {"speaker": s, "raw_speaker": s, "candidate_subject": "self" if s == "self" else s}
            for s in sorted(list(speakers))
        ]
        imp = ProfileImport(
            id=import_id,
            owner_id=actor.owner_id,
            subject_id=subject_id,
            subject_candidates=candidates,
            original_asset_ref=filename,
            source_type="conversation_json" if parsed_json_msgs is not None else ("conversation_text" if len(speakers) > 1 else "text"),
            mime="application/json" if parsed_json_msgs is not None else "text/plain",
            size=len(content_bytes),
            sha256=sha256,
            privacy_domain=privacy_domain,
            status="parsed",
        )
        self.session.add(imp)
        for seg in segments:
            self.session.add(seg)

        self.audit.append(
            actor,
            "profile.import.create",
            target=import_id,
            details={"filename": filename, "sha256": sha256, "segments": len(segments), "speakers": sorted(list(speakers))},
        )
        self.session.flush()
        return imp

    def confirm_speakers(
        self,
        actor: Actor,
        import_id: str,
        mappings: dict[str, str],
    ) -> dict[str, Any]:
        """Explicitly confirm speaker-to-subject or self/ignore attribution mapping."""
        actor.require_owner()
        imp = self.session.get(ProfileImport, import_id)
        if not imp or imp.owner_id != actor.owner_id:
            raise NotFound(f"Profile import not found: {import_id}")

        stmt = select(SourceSegment).where(SourceSegment.import_id == import_id)
        segments = list(self.session.execute(stmt).scalars().all())
        updated_count = 0
        for seg in segments:
            current_tag = seg.raw_speaker or seg.speaker
            if current_tag in mappings:
                if not getattr(seg, "raw_speaker", None):
                    seg.raw_speaker = seg.speaker
                seg.speaker = mappings[current_tag]
                updated_count += 1
            elif seg.speaker in mappings:
                if not getattr(seg, "raw_speaker", None):
                    seg.raw_speaker = seg.speaker
                seg.speaker = mappings[seg.speaker]
                updated_count += 1

        # Preserve confirmed mapping history on import record
        old_cands = imp.subject_candidates or []
        new_cands = []
        for c in old_cands:
            spk_name = c.get("speaker") if isinstance(c, dict) else c
            new_cands.append({
                "speaker": spk_name,
                "raw_speaker": spk_name,
                "confirmed_mapping": mappings.get(spk_name),
            })
        imp.subject_candidates = new_cands
        flag_modified(imp, "subject_candidates")

        self.audit.append(
            actor,
            "profile.speakers.confirm",
            target=import_id,
            details={"mappings": mappings, "updated_segments": updated_count},
        )
        self.session.flush()
        return {"import_id": import_id, "updated_segments": updated_count, "mappings": mappings}

    # -----------------------------------------------------------------------
    # Profiling Run & Synthesis (JSON Schema 1.0)
    # -----------------------------------------------------------------------
    def run_profiling(
        self,
        actor: Actor,
        subject_id: str,
        rule_version: str = "v1.0",
    ) -> ProfileRevision:
        actor.require_owner()
        subj = self.get_subject(actor, subject_id)

        # Find all segments linked to this subject via imports (strictly isolated, no fallback bleed)
        stmt_segs = (
            select(SourceSegment)
            .join(ProfileImport, SourceSegment.import_id == ProfileImport.id)
            .where(
                ProfileImport.owner_id == actor.owner_id,
                ProfileImport.subject_id == subject_id,
            )
        )
        segments = list(self.session.execute(stmt_segs).scalars().all())
        if not segments:
            raise ValidationFailed(
                f"No imported corpus segments associated with subject: {subj.label} ({subject_id}). "
                "Cross-subject data fallback is forbidden to prevent profile bleed."
            )

        # Extract claims & evidence deterministically
        evidence_list: list[ProfileEvidence] = []
        snapshot_hasher = hashlib.sha256()

        self_keywords = ["我喜欢", "我擅长", "我倾向", "目标", "认为", "希望", "关注", "prefer", "like", "goal"]
        style_keywords = ["逻辑", "分析", "架构", "设计", "重构", "安全", "测试", "logic", "architecture"]

        is_self_subject = (subj.kind == "self")
        unconfirmed_speakers: set[str] = set()

        is_self_subject = (subj.kind == "self")
        unconfirmed_speakers: set[str] = set()

        self_segments: list[SourceSegment] = []
        third_party_segments: list[SourceSegment] = []

        for seg in segments:
            snapshot_hasher.update(seg.content_hash.encode("utf-8"))
            spk = seg.speaker

            if is_self_subject:
                is_self_speaker = spk in ("self", "user", "me", subj.label)
                if is_self_speaker:
                    self_segments.append(seg)
                else:
                    third_party_segments.append(seg)
                    unconfirmed_speakers.add(spk)
            else:
                self_segments.append(seg)

        # 1. Process Self Segments -> Self Evidence
        self_evidence_list: list[ProfileEvidence] = []
        for seg in self_segments:
            txt = seg.text_content
            if is_self_subject:
                if any(k in txt for k in self_keywords):
                    kind = "self_report"
                    dim = "本人偏好与自述目标"
                    conf = 0.95
                    rev_status = "accepted"
                elif any(k in txt for k in style_keywords):
                    kind = "observed_stat"
                    dim = "工程与思维风格"
                    conf = 0.85
                    rev_status = "candidate"
                else:
                    kind = "observed_stat"
                    dim = "日常习惯"
                    conf = 0.70
                    rev_status = "candidate"
            else:
                if any(k in txt for k in style_keywords):
                    kind = "observed_stat"
                    dim = "工程与思维风格"
                    conf = 0.85
                    rev_status = "candidate"
                else:
                    kind = "observed_stat"
                    dim = "对象实践与行为观察"
                    conf = 0.75
                    rev_status = "candidate"

            ev = ProfileEvidence(
                id=f"evi-{uuid4().hex[:12]}",
                subject_id=subject_id,
                source_segment_id=seg.id,
                claim=txt[:200],
                evidence_kind=kind,
                polarity="positive",
                proposed_dimension=dim,
                confidence=conf,
                review_status=rev_status,
            )
            self_evidence_list.append(ev)
            self.session.add(ev)

        # 2. Process Third Party Segments -> Context Evidence (Strictly separated)
        third_party_evidence_list: list[ProfileEvidence] = []
        for seg in third_party_segments:
            ev = ProfileEvidence(
                id=f"evi-{uuid4().hex[:12]}",
                subject_id=subject_id,
                source_segment_id=seg.id,
                claim=seg.text_content[:200],
                evidence_kind="third_party_statement",
                polarity="neutral",
                proposed_dimension="他者发言与对话情境",
                confidence=0.50,
                review_status="candidate",
            )
            third_party_evidence_list.append(ev)
            self.session.add(ev)

        evidence_list = self_evidence_list + third_party_evidence_list

        snapshot_hash = snapshot_hasher.hexdigest() or hashlib.sha256(b"empty").hexdigest()

        # Create ProfileRun
        run_id = f"run-{uuid4().hex[:12]}"
        run = ProfileRun(
            id=run_id,
            subject_id=subject_id,
            input_snapshot_hash=snapshot_hash,
            schema_version="1.0",
            rule_version=rule_version,
            state="completed",
            completed_at=utcnow(),
        )
        self.session.add(run)

        # Compute next revision index
        stmt_rev = select(ProfileRevision.revision).where(
            ProfileRevision.subject_id == subject_id
        ).order_by(ProfileRevision.revision.desc())
        latest_rev = self.session.execute(stmt_rev).scalars().first() or 0
        new_rev_num = latest_rev + 1

        # Reproducible deterministic text metrics:
        # STRICTLY SEPARATE: Confirmed Self Stats vs Overall Dialogue Context Stats
        all_text = " ".join(seg.text_content for seg in segments)
        tokens = re.findall(r"[\w\u4e00-\u9fa5]+", all_text)
        total_tokens = len(tokens)
        unique_tokens = len(set(tokens))
        lexical_diversity = round(unique_tokens / max(total_tokens, 1), 4)

        dialogue_segment_count = len(segments)
        dialogue_word_count = sum(len(seg.text_content) for seg in segments)
        third_party_segment_count = len(third_party_segments)
        third_party_word_count = sum(len(seg.text_content) for seg in third_party_segments)

        self_segment_count = len(self_segments)
        self_word_count = sum(len(seg.text_content) for seg in self_segments)
        # Self-preference density is calculated STRICTLY on self_segments (denominator: self_word_count)
        self_pref_matches = sum(1 for seg in self_segments if any(k in seg.text_content for k in self_keywords))
        self_preference_density = round((self_pref_matches / max(self_word_count, 1)) * 1000, 2)

        self_tech_matches = sum(1 for seg in self_segments if any(k in seg.text_content for k in style_keywords))
        self_domain_focus_ratio = round((self_tech_matches / max(self_word_count, 1)) * 1000, 2)

        if is_self_subject:
            metrics = [
                {
                    "dimension": "本人有效自述切片数",
                    "raw_value": self_segment_count,
                    "display_value": f"{self_segment_count} 切片",
                    "metric_type": "self_report",
                    "evidence_count": len(self_evidence_list),
                    "calculation_formula": "count(confirmed_self_segments)",
                },
                {
                    "dimension": "本人自述有效总字数",
                    "raw_value": self_word_count,
                    "display_value": f"{self_word_count} 字",
                    "metric_type": "self_report",
                    "evidence_count": len(self_evidence_list),
                    "calculation_formula": "sum(length(confirmed_self_segments))",
                },
                {
                    "dimension": "自述偏好表达密度",
                    "raw_value": self_preference_density,
                    "display_value": f"{self_preference_density} 处/千字",
                    "metric_type": "self_report",
                    "evidence_count": self_pref_matches,
                    "numerator": self_pref_matches,
                    "denominator": self_word_count,
                    "denominator_source": "本人确认自述切片字数 (self_word_count)",
                    "calculation_formula": "count(self_pref_cues) * 1000 / self_chars",
                },
                {
                    "dimension": "本人工程与逻辑聚焦度",
                    "raw_value": self_domain_focus_ratio,
                    "display_value": f"{self_domain_focus_ratio} 处/千字",
                    "metric_type": "self_report",
                    "evidence_count": self_tech_matches,
                    "calculation_formula": "count(self_domain_cues) * 1000 / self_chars",
                },
                {
                    "dimension": "对话语料总字数",
                    "raw_value": dialogue_word_count,
                    "display_value": f"{dialogue_word_count} 字 (第三方 {third_party_word_count} 字)",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(evidence_list),
                    "calculation_formula": "sum(length(all_dialogue_segments))",
                },
                {
                    "dimension": "对话总切片数",
                    "raw_value": dialogue_segment_count,
                    "display_value": f"{dialogue_segment_count} 切片",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(evidence_list),
                    "calculation_formula": "count(all_dialogue_segments)",
                },
                {
                    "dimension": "第三方切片数",
                    "raw_value": third_party_segment_count,
                    "display_value": f"{third_party_segment_count} 切片",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(third_party_evidence_list),
                    "calculation_formula": "count(third_party_segments)",
                },
                {
                    "dimension": "词汇丰富度比率",
                    "raw_value": lexical_diversity,
                    "display_value": f"{round(lexical_diversity * 100, 1)}%",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(evidence_list),
                    "calculation_formula": "unique_tokens / total_tokens",
                },
            ]
        else:
            metrics = [
                {
                    "dimension": "语料切片样本量",
                    "raw_value": len(segments),
                    "display_value": f"{len(segments)} 切片",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(evidence_list),
                    "calculation_formula": "count(source_segments)",
                },
                {
                    "dimension": "有效语料总字数",
                    "raw_value": dialogue_word_count,
                    "display_value": f"{dialogue_word_count} 字",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(evidence_list),
                    "calculation_formula": "sum(length(text_content))",
                },
                {
                    "dimension": "词汇丰富度比率",
                    "raw_value": lexical_diversity,
                    "display_value": f"{round(lexical_diversity * 100, 1)}%",
                    "metric_type": "corpus_stat",
                    "evidence_count": len(evidence_list),
                    "calculation_formula": "unique_tokens / total_tokens",
                },
                {
                    "dimension": "工程与逻辑聚焦度",
                    "raw_value": self_domain_focus_ratio,
                    "display_value": f"{self_domain_focus_ratio} 处/千字",
                    "metric_type": "corpus_stat",
                    "evidence_count": self_tech_matches,
                    "calculation_formula": "count(domain_cues) * 1000 / total_chars",
                },
            ]

        # Map source segment id to segment info for backlinks
        seg_map = {seg.id: seg for seg in segments}

        # Synthesize Clusters: Core clusters contain only self_evidence!
        nodes_core = []
        for i, ev in enumerate(self_evidence_list[:5]):
            seg = seg_map.get(ev.source_segment_id)
            nodes_core.append({
                "id": f"n_{ev.id[:8]}",
                "label": ev.proposed_dimension,
                "description": ev.claim,
                "claim_kind": ev.evidence_kind,
                "confidence": float(ev.confidence),
                "review_status": ev.review_status,
                "evidence_refs": [ev.id],
                "source_segment_id": ev.source_segment_id,
                "locator": seg.locator if seg else "L1",
                "speaker": seg.speaker if seg else "unknown",
                "counter_evidence_refs": [],
                "x": 150 + (i % 3) * 140,
                "y": 100 + (i // 3) * 120,
            })

        nodes_style = []
        for i, ev in enumerate(self_evidence_list[5:10]):
            seg = seg_map.get(ev.source_segment_id)
            nodes_style.append({
                "id": f"n_{ev.id[:8]}",
                "label": ev.proposed_dimension,
                "description": ev.claim,
                "claim_kind": ev.evidence_kind,
                "confidence": float(ev.confidence),
                "review_status": ev.review_status,
                "evidence_refs": [ev.id],
                "source_segment_id": ev.source_segment_id,
                "locator": seg.locator if seg else "L1",
                "speaker": seg.speaker if seg else "unknown",
                "counter_evidence_refs": [],
                "x": 480 + (i % 3) * 140,
                "y": 100 + (i // 3) * 120,
            })

        clusters = [
            {
                "id": "c_core",
                "name": "核心特质",
                "summary": f"{subj.label}的核心行为与表达摘要",
                "nodes": nodes_core,
            },
            {
                "id": "c_style",
                "name": "认知与实践风格",
                "summary": "基于确认语料的条理性与思维模式观察",
                "nodes": nodes_style,
            },
        ]

        # Isolated cluster for third-party statements (if any)
        if third_party_evidence_list:
            nodes_tp = []
            for i, ev in enumerate(third_party_evidence_list[:5]):
                seg = seg_map.get(ev.source_segment_id)
                nodes_tp.append({
                    "id": f"n_{ev.id[:8]}",
                    "label": ev.proposed_dimension,
                    "description": ev.claim,
                    "claim_kind": ev.evidence_kind,
                    "confidence": float(ev.confidence),
                    "review_status": ev.review_status,
                    "evidence_refs": [ev.id],
                    "source_segment_id": ev.source_segment_id,
                    "locator": seg.locator if seg else "L1",
                    "speaker": seg.speaker if seg else "unknown",
                    "counter_evidence_refs": [],
                    "x": 300 + (i % 3) * 140,
                    "y": 240 + (i // 3) * 120,
                })
            clusters.append({
                "id": "c_third_party",
                "name": "他者发言与对话情境参考",
                "summary": "未确认为本人自述的第三方发言记录，仅作为对话背景，不计入本人画像特质",
                "nodes": nodes_tp,
            })

        # 2D edges: honest layout adjacency, no fake semantic correlation claims
        edges = []
        all_nodes = nodes_core + nodes_style
        for i in range(len(all_nodes) - 1):
            edges.append({
                "id": f"edge_{all_nodes[i]['id']}_{all_nodes[i+1]['id']}",
                "source": all_nodes[i]["id"],
                "target": all_nodes[i + 1]["id"],
                "relation": "layout_adjacency",
                "strength": 0.0,
            })

        limitations = [
            "分析基于用户导入的有限语料样本，可能存在情境呈现偏差与样本不均衡",
            "本画像未接入标准化心理量表授权输入，不呈现推测性能力分或人格测评常模分；仅呈现可重算的客观文本统计与用户自述",
            "候选假设必须由所有者逐条复核确认，未经确认的推测不作为事实定性",
        ]
        if unconfirmed_speakers:
            limitations.append(
                f"检测到未确认说话人 ({', '.join(sorted(list(unconfirmed_speakers)))})，未确认说话人切片已自动降级为第三方记录，不计入正式本人自述。"
            )

        core_summary = {
            "title": f"{subj.label} 多维特征透视 (Rev {new_rev_num})",
            "summary": f"基于 {len(segments)} 条语料切片提炼之特征画像，包含 {len(evidence_list)} 项证据关联。",
            "evidence_count": len(evidence_list),
            "formal_norm": False,
            "scale_name": None,
            "norm_note": "未接入标准化心理量表授权输入，不呈现推测性能力分或人格测评常模分；以上呈现指标为可重算语料客观统计",
        }

        rev_id = f"rev-{uuid4().hex[:12]}"
        rev = ProfileRevision(
            id=rev_id,
            subject_id=subject_id,
            revision=new_rev_num,
            profile_run_id=run_id,
            core_summary=core_summary,
            clusters=clusters,
            edges=edges,
            metrics=metrics,
            limitations=limitations,
            user_review_state="draft" if new_rev_num > 1 else "confirmed",
        )
        self.session.add(rev)

        self.audit.append(
            actor,
            "profile.revision.create",
            target=rev_id,
            details={"subject_id": subject_id, "revision": new_rev_num, "evidence_count": len(evidence_list)},
        )
        self.session.flush()
        return rev

    # -----------------------------------------------------------------------
    # Evidence Feedback
    # -----------------------------------------------------------------------
    def submit_feedback(
        self,
        actor: Actor,
        evidence_id: str,
        action: str,
        feedback_text: str | None = None,
    ) -> ProfileFeedback:
        actor.require_owner()
        action = action.lower().strip()
        if action not in ("accept", "edit", "reject", "uncertain"):
            raise ValidationFailed(f"Invalid feedback action: {action}")

        ev = self.session.get(ProfileEvidence, evidence_id)
        if not ev:
            raise NotFound(f"Profile evidence not found: {evidence_id}")

        subj = self.get_subject(actor, ev.subject_id)

        # Update evidence review_status
        if action == "accept":
            ev.review_status = "accepted"
        elif action == "reject":
            ev.review_status = "rejected"
        elif action == "edit":
            ev.review_status = "edited"
            if feedback_text:
                ev.claim = feedback_text
        elif action == "uncertain":
            ev.review_status = "uncertain"

        # Cascade feedback into existing ProfileRevision snapshots
        stmt_revs = select(ProfileRevision).where(ProfileRevision.subject_id == subj.id)
        revs = list(self.session.execute(stmt_revs).scalars().all())
        for r in revs:
            rev_updated = False
            new_clusters = []
            for cl in (r.clusters or []):
                new_nodes = []
                for nd in cl.get("nodes", []):
                    if evidence_id in nd.get("evidence_refs", []):
                        rev_updated = True
                        if action == "reject":
                            nd["review_status"] = "rejected"
                            nd["confidence"] = 0.0
                        elif action == "accept":
                            nd["review_status"] = "accepted"
                        elif action == "edit" and feedback_text:
                            nd["description"] = feedback_text
                            nd["review_status"] = "edited"
                    new_nodes.append(nd)
                new_cl = dict(cl)
                new_cl["nodes"] = new_nodes
                new_clusters.append(new_cl)
            if rev_updated:
                r.clusters = new_clusters
                r.user_review_state = "feedback_applied"
                flag_modified(r, "clusters")

        fb_id = f"fb-{uuid4().hex[:12]}"
        fb = ProfileFeedback(
            id=fb_id,
            evidence_id=evidence_id,
            subject_id=subj.id,
            action=action,
            feedback_text=feedback_text,
        )
        self.session.add(fb)

        self.audit.append(
            actor,
            "profile.evidence.feedback",
            target=evidence_id,
            details={"action": action, "subject_id": subj.id},
        )
        self.session.flush()
        return fb

    # -----------------------------------------------------------------------
    # Deletion with Tombstone Cascade
    # -----------------------------------------------------------------------
    def delete_import(self, actor: Actor, import_id: str) -> None:
        actor.require_owner()
        imp = self.session.get(ProfileImport, import_id)
        if not imp or imp.owner_id != actor.owner_id:
            raise NotFound(f"Profile import not found: {import_id}")

        subject_id = imp.subject_id

        # Find segments
        stmt_seg_ids = select(SourceSegment.id).where(SourceSegment.import_id == import_id)
        seg_ids = list(self.session.execute(stmt_seg_ids).scalars().all())

        # Find evidence
        if seg_ids:
            stmt_ev_ids = select(ProfileEvidence.id).where(
                ProfileEvidence.source_segment_id.in_(seg_ids)
            )
            ev_ids = set(self.session.execute(stmt_ev_ids).scalars().all())
        else:
            ev_ids = set()

        # Invalidate / prune derived ProfileRevision snapshots
        affected_revs = []
        if subject_id:
            stmt_revs = select(ProfileRevision).where(ProfileRevision.subject_id == subject_id)
            revs = list(self.session.execute(stmt_revs).scalars().all())
            for r in revs:
                has_deleted_ref = False
                new_clusters = []
                for cl in (r.clusters or []):
                    new_nodes = []
                    for nd in cl.get("nodes", []):
                        refs = set(nd.get("evidence_refs", []))
                        if refs & ev_ids:
                            has_deleted_ref = True
                            nd["review_status"] = "invalidated"
                            nd["confidence"] = 0.0
                            nd["description"] = "[REDACTED: 来源语料已删除，原句已物理销毁]"
                            nd["locator"] = "[DELETED]"
                            nd["speaker"] = "[REDACTED]"
                            nd.pop("source_segment_id", None)
                            nd["invalidation_note"] = f"Derived source import {import_id} deleted"
                        new_nodes.append(nd)
                    new_cl = dict(cl)
                    new_cl["nodes"] = new_nodes
                    new_clusters.append(new_cl)
                if has_deleted_ref:
                    r.clusters = new_clusters
                    r.user_review_state = "invalidated"
                    core = dict(r.core_summary or {})
                    core["summary"] = "[REDACTED: 来源语料已删除，派生特征已销毁封存]"
                    core["invalidation_note"] = f"Derived source import {import_id} was deleted by owner"
                    r.core_summary = core
                    r.metrics = [
                        {
                            **m,
                            "raw_value": 0,
                            "display_value": "[INVALIDATED]",
                        }
                        for m in (r.metrics or [])
                    ]
                    flag_modified(r, "clusters")
                    flag_modified(r, "core_summary")
                    flag_modified(r, "metrics")
                    affected_revs.append(r.id)

        # Delete import (cascade takes care of segments and evidence in DB)
        self.session.delete(imp)

        # Log tombstone for cascading audit
        tomb = Tombstone(
            id=f"tomb-{uuid4().hex[:12]}",
            target_id=import_id,
            target_kind="profile_import",
            reason=f"Owner deleted import; cascaded {len(seg_ids)} segments, {len(ev_ids)} evidences; invalidated {len(affected_revs)} revisions",
            deleted_by=actor.owner_id,
            dep_graph_hash=imp.sha256,
        )
        self.session.add(tomb)

        self.audit.append(
            actor,
            "profile.import.delete",
            target=import_id,
            details={
                "cascaded_segments": len(seg_ids),
                "cascaded_evidences": len(ev_ids),
                "invalidated_revisions": affected_revs,
            },
        )
        self.session.flush()

    def list_revisions(
        self,
        actor: Actor,
        subject_id: str,
        include_invalidated: bool = False,
    ) -> list[ProfileRevision]:
        actor.require_owner()
        self.get_subject(actor, subject_id)
        stmt = select(ProfileRevision).where(ProfileRevision.subject_id == subject_id)
        if not include_invalidated:
            stmt = stmt.where(ProfileRevision.user_review_state != "invalidated")
        stmt = stmt.order_by(ProfileRevision.revision.desc())
        return list(self.session.execute(stmt).scalars().all())

    def get_revision(
        self,
        actor: Actor,
        revision_id: str,
        allow_invalidated: bool = False,
    ) -> ProfileRevision:
        actor.require_owner()
        rev = self.session.get(ProfileRevision, revision_id)
        if not rev:
            raise NotFound(f"Profile revision not found: {revision_id}")
        self.get_subject(actor, rev.subject_id)
        if rev.user_review_state == "invalidated" and not allow_invalidated:
            raise NotFound(f"Profile revision {revision_id} has been invalidated due to source data deletion")
        return rev
