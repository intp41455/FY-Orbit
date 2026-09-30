"""API routes for 04 Profile (个人与对象多维画像)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from ..deps import csrf_protected, get_actor, get_services, Services
from ...services.actor import Actor

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class CreateSubjectRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="self", pattern="^(self|person|project|org|work|topic|other)$")
    description: str = Field(default="", max_length=1000)
    confirmed: bool = False


class ImportDocumentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)
    filename: str = Field(default="input.txt", max_length=200)
    subject_id: str | None = None
    privacy_domain: str = Field(default="personal", pattern="^(personal|work)$")


class RunProfilingRequest(BaseModel):
    rule_version: str = Field(default="v1.0", max_length=32)


class FeedbackRequest(BaseModel):
    action: str = Field(pattern="^(accept|edit|reject|uncertain)$")
    feedback_text: str | None = Field(default=None, max_length=2000)


class ConfirmSpeakersRequest(BaseModel):
    mappings: dict[str, str]


@router.post("/subjects", status_code=status.HTTP_201_CREATED)
async def create_subject(
    body: CreateSubjectRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    subj = svc.profiles.create_subject(
        actor,
        label=body.label,
        kind=body.kind,
        description=body.description,
        confirmed=body.confirmed,
    )
    svc.session.commit()
    return {
        "id": subj.id,
        "owner_id": subj.owner_id,
        "kind": subj.kind,
        "label": subj.label,
        "description": subj.description,
        "confirmed": subj.confirmed,
        "created_at": subj.created_at.isoformat(),
    }


@router.get("/subjects")
async def list_subjects(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    subjs = svc.profiles.list_subjects(actor)
    return {
        "items": [
            {
                "id": s.id,
                "owner_id": s.owner_id,
                "kind": s.kind,
                "label": s.label,
                "description": s.description,
                "confirmed": s.confirmed,
                "created_at": s.created_at.isoformat(),
            }
            for s in subjs
        ],
        "count": len(subjs),
    }


@router.get("/subjects/{id}")
async def get_subject(
    id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    s = svc.profiles.get_subject(actor, id)
    return {
        "id": s.id,
        "owner_id": s.owner_id,
        "kind": s.kind,
        "label": s.label,
        "description": s.description,
        "confirmed": s.confirmed,
        "created_at": s.created_at.isoformat(),
    }


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def import_document(
    body: ImportDocumentRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    imp = svc.profiles.import_document(
        actor,
        content=body.content,
        filename=body.filename,
        subject_id=body.subject_id,
        privacy_domain=body.privacy_domain,
    )
    svc.session.commit()
    return {
        "id": imp.id,
        "owner_id": imp.owner_id,
        "subject_id": imp.subject_id,
        "subject_candidates": imp.subject_candidates,
        "original_asset_ref": imp.original_asset_ref,
        "source_type": imp.source_type,
        "size": imp.size,
        "sha256": imp.sha256,
        "status": imp.status,
        "created_at": imp.created_at.isoformat(),
    }


@router.post("/imports/{id}/confirm-speakers", status_code=status.HTTP_200_OK)
@router.post("/imports/{id}/confirm_speakers", status_code=status.HTTP_200_OK)
async def confirm_speakers(
    id: str,
    body: ConfirmSpeakersRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.profiles.confirm_speakers(actor, import_id=id, mappings=body.mappings)
    svc.session.commit()
    return res


@router.post("/{subject_id}/runs", status_code=status.HTTP_201_CREATED)
async def run_profiling(
    subject_id: str,
    body: RunProfilingRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rev = svc.profiles.run_profiling(actor, subject_id, rule_version=body.rule_version)
    svc.session.commit()
    return {
        "id": rev.id,
        "subject_id": rev.subject_id,
        "revision": rev.revision,
        "core_summary": rev.core_summary,
        "clusters": rev.clusters,
        "metrics": rev.metrics,
        "limitations": rev.limitations,
        "user_review_state": rev.user_review_state,
        "created_at": rev.created_at.isoformat(),
    }


@router.get("/{subject_id}/revisions")
async def list_revisions(
    subject_id: str,
    include_invalidated: bool = False,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    revs = svc.profiles.list_revisions(actor, subject_id, include_invalidated=include_invalidated)
    return {
        "items": [
            {
                "id": r.id,
                "subject_id": r.subject_id,
                "revision": r.revision,
                "core_summary": r.core_summary,
                "clusters": r.clusters,
                "metrics": r.metrics,
                "limitations": r.limitations,
                "user_review_state": r.user_review_state,
                "created_at": r.created_at.isoformat(),
            }
            for r in revs
        ],
        "count": len(revs),
    }


@router.get("/revisions/{id}")
async def get_revision(
    id: str,
    allow_invalidated: bool = False,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    rev = svc.profiles.get_revision(actor, id, allow_invalidated=allow_invalidated)
    return {
        "id": rev.id,
        "subject_id": rev.subject_id,
        "revision": rev.revision,
        "core_summary": rev.core_summary,
        "clusters": rev.clusters,
        "metrics": rev.metrics,
        "limitations": rev.limitations,
        "user_review_state": rev.user_review_state,
        "created_at": rev.created_at.isoformat(),
    }


@router.post("/evidence/{id}/feedback")
async def submit_evidence_feedback(
    id: str,
    body: FeedbackRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    fb = svc.profiles.submit_feedback(
        actor,
        evidence_id=id,
        action=body.action,
        feedback_text=body.feedback_text,
    )
    svc.session.commit()
    return {
        "id": fb.id,
        "evidence_id": fb.evidence_id,
        "subject_id": fb.subject_id,
        "action": fb.action,
        "feedback_text": fb.feedback_text,
        "created_at": fb.created_at.isoformat(),
    }


@router.delete("/imports/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import(
    id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> Response:
    svc.profiles.delete_import(actor, id)
    svc.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
