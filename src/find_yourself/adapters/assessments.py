"""Server-side assessment scoring (FROZEN_CONTRACT §11, U05/U06).

Scoring is server-authoritative. Rules enforced here:

* Empty / missing required answers never produce a default personality result —
  the session is reported ``incomplete`` with the list of missing items.
* Reverse-scored items are flipped before aggregation.
* Results are bound to the exact questionnaire version and item-set hash.
* Sources, licence and scoring basis are returned alongside the result.

The Big-Five instrument shipped here is a **synthetic** template used to prove
the scoring mechanism; it is explicitly NOT the official IPIP item set and ships
with no population norms or clinical cut-offs. Wiring real IPIP items requires
verifying the official source, licence and Chinese translation notes and is
tracked separately — official/norms/clinical conclusions are never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import uuid

from ..services.errors import ValidationFailed


@dataclass(frozen=True)
class Item:
    id: str
    dimension: str
    direction: int  # +1 normal, -1 reverse-scored
    text: str
    synthetic: bool = True


@dataclass(frozen=True)
class Questionnaire:
    id: str
    version: str
    title: str
    license: str
    source_note: str
    dimensions: list[str]
    items: list[Item]

    @property
    def item_set_hash(self) -> str:
        blob = json.dumps(
            [[i.id, i.dimension, i.direction] for i in self.items],
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --- Synthetic catalog (mechanism proof only; NOT official IPIP/MBTI) -------

def _synthetic_bigfive() -> Questionnaire:
    dims = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
    items: list[Item] = []
    for d in dims:
        for k, direction in enumerate((1, -1)):
            items.append(Item(id=f"{d[:3]}{k+1}", dimension=d, direction=direction,
                              text=f"[synthetic] placeholder item for {d} #{k+1}"))
    return Questionnaire(
        id="bigfive-synthetic",
        version="0.1.0-synthetic",
        title="Big Five (synthetic mechanism template)",
        license="synthetic-template",
        source_note=("Synthetic items used only to prove server scoring. NOT the "
                      "official IPIP-NEO item set; no norms or clinical cut-offs. "
                      "Official IPIP: https://ipip.ori.org/"),
        dimensions=dims,
        items=items,
    )


def _exploratory_fourdim() -> Questionnaire:
    dims = ["EI", "SN", "TF", "JP"]
    items: list[Item] = []
    for d in dims:
        items.append(Item(id=f"{d}1", dimension=d, direction=1,
                          text=f"[exploratory] placeholder item for {d}"))
    return Questionnaire(
        id="fourdim-exploratory",
        version="0.1.0-exploratory",
        title="Four-dimension exploratory type (non-official MBTI)",
        license="exploratory",
        source_note="Exploratory reflection tool only; NOT official MBTI and not "
                    "endorsed by The Myers-Briggs Company.",
        dimensions=dims,
        items=items,
    )


CATALOG: dict[str, Questionnaire] = {q.id: q for q in (_synthetic_bigfive(), _exploratory_fourdim())}


@dataclass
class AssessmentSession:
    session_id: str
    questionnaire_id: str
    questionnaire_version: str
    item_set_hash: str
    answers: dict[str, int] = field(default_factory=dict)
    status: str = "in_progress"
    result: dict | None = None

    def to_public(self) -> dict:
        return {
            "session_id": self.session_id,
            "questionnaire_id": self.questionnaire_id,
            "questionnaire_version": self.questionnaire_version,
            "item_set_hash": self.item_set_hash,
            "status": self.status,
            "missing": self.missing_item_ids(),
            "result": self.result,
        }

    def missing_item_ids(self) -> list[str]:
        q = CATALOG[self.questionnaire_id]
        return [i.id for i in q.items if i.id not in self.answers]


class AssessmentScorer:
    """Creates sessions, records answers and scores server-side."""

    def __init__(self):
        self._sessions: dict[str, AssessmentSession] = {}

    def catalog(self) -> list[dict]:
        return [
            {"id": q.id, "version": q.version, "title": q.title,
             "license": q.license, "source_note": q.source_note,
             "dimensions": q.dimensions, "item_count": len(q.items),
             "synthetic": True}
            for q in CATALOG.values()
        ]

    def start(self, questionnaire_id: str) -> AssessmentSession:
        if questionnaire_id not in CATALOG:
            raise ValidationFailed("unknown_questionnaire", "Unknown questionnaire")
        q = CATALOG[questionnaire_id]
        s = AssessmentSession(
            session_id=uuid.uuid4().hex,
            questionnaire_id=q.id,
            questionnaire_version=q.version,
            item_set_hash=q.item_set_hash,
        )
        self._sessions[s.session_id] = s
        return s

    def record_answers(self, session_id: str, answers: dict[str, int]) -> AssessmentSession:
        s = self._require(session_id)
        q = CATALOG[s.questionnaire_id]
        valid = {i.id for i in q.items}
        for item_id, val in answers.items():
            if item_id not in valid:
                raise ValidationFailed("unknown_item", f"Item {item_id} not in this questionnaire")
            if not isinstance(val, int) or not (1 <= val <= 5):
                raise ValidationFailed("bad_score", "Scores must be integers 1..5")
            s.answers[item_id] = val
        s.status = "in_progress"
        s.result = None
        return s

    def submit(self, session_id: str) -> AssessmentSession:
        s = self._require(session_id)
        q = CATALOG[s.questionnaire_id]
        missing = s.missing_item_ids()
        if missing:
            # No default personality result on incomplete data (U05).
            s.status = "incomplete"
            s.result = None
            return s
        # Aggregate with reverse scoring.
        totals: dict[str, list[int]] = {d: [] for d in q.dimensions}
        for item in q.items:
            raw = s.answers[item.id]
            score = (6 - raw) if item.direction < 0 else raw
            totals[item.dimension].append(score)
        profile = {
            d: round(sum(v) / len(v), 3) for d, v in totals.items()
        }
        s.status = "scored"
        s.result = {
            "profile": profile,
            "questionnaire_id": q.id,
            "questionnaire_version": q.version,
            "item_set_hash": q.item_set_hash,
            "source_note": q.source_note,
            "synthetic": True,
            "norms": None,  # never fabricate population norms
            "clinical": False,
        }
        return s

    def get(self, session_id: str) -> AssessmentSession:
        return self._require(session_id)

    def _require(self, session_id: str) -> AssessmentSession:
        s = self._sessions.get(session_id)
        if s is None:
            from ..services.errors import NotFound
            raise NotFound("assessment_session_not_found", "Assessment session not found")
        return s


# Process-local registry. Persistence across restarts requires a Core assessment
# table (not yet in the frozen schema); scored results are returned to the caller.
scorer = AssessmentScorer()
