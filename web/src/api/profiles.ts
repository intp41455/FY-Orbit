import { request } from './client';

export interface ProfileSubject {
  id: string;
  owner_id: string;
  kind: 'self' | 'person' | 'project' | 'org' | 'work' | 'topic' | 'other';
  label: string;
  description: string;
  confirmed: boolean;
  created_at: string;
}

export interface ProfileImport {
  id: string;
  owner_id: string;
  subject_id?: string;
  subject_candidates: Array<{ speaker: string; candidate_subject: string; segment_count: number }>;
  original_asset_ref: string;
  source_type: string;
  size: number;
  sha256: string;
  status: string;
  created_at: string;
}

export interface ProfileClusterNode {
  id: string;
  label: string;
  description: string;
  claim_kind: string;
  confidence: number;
  review_status: 'candidate' | 'pending' | 'accepted' | 'edited' | 'rejected' | 'uncertain' | 'invalidated';
  evidence_refs: string[];
  counter_evidence_refs: string[];
  source_segment_id?: string;
  locator?: string;
  speaker?: string;
  x?: number;
  y?: number;
}

export interface ProfileCluster {
  id: string;
  name: string;
  summary: string;
  nodes: ProfileClusterNode[];
}

export interface ProfileEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  strength: number;
}

export interface ProfileMetric {
  dimension: string;
  score?: number;
  raw_value?: number;
  display_value?: string;
  calculation_formula?: string;
  metric_type: 'corpus_stat' | 'self_report' | 'derived';
  evidence_count: number;
}

export interface ProfileRevision {
  id: string;
  subject_id: string;
  revision: number;
  core_summary: {
    title: string;
    summary: string;
    evidence_count: number;
    formal_norm?: boolean;
    scale_name?: string | null;
    norm_note?: string;
    invalidation_note?: string;
  };
  clusters: ProfileCluster[];
  edges?: ProfileEdge[];
  metrics: ProfileMetric[];
  limitations: string[];
  user_review_state: string;
  created_at: string;
}

export const profilesApi = {
  createSubject: (body: { label: string; kind?: string; description?: string; confirmed?: boolean }) =>
    request<ProfileSubject>('/api/profiles/subjects', { method: 'POST', body }),
  listSubjects: () =>
    request<{ items: ProfileSubject[]; count: number }>('/api/profiles/subjects'),
  getSubject: (id: string) =>
    request<ProfileSubject>(`/api/profiles/subjects/${id}`),
  importDocument: (body: { content: string; filename?: string; subject_id?: string; privacy_domain?: string }) =>
    request<ProfileImport>('/api/profiles/imports', { method: 'POST', body }),
  confirmSpeakers: (importId: string, mappings: Record<string, string>) =>
    request<{ import_id: string; updated_segments: number; mappings: Record<string, string> }>(
      `/api/profiles/imports/${importId}/confirm-speakers`,
      { method: 'POST', body: { mappings } }
    ),
  runProfiling: (subjectId: string, body?: { rule_version?: string }) =>
    request<ProfileRevision>(`/api/profiles/${subjectId}/runs`, { method: 'POST', body: body ?? {} }),
  listRevisions: (subjectId: string) =>
    request<{ items: ProfileRevision[]; count: number }>(`/api/profiles/${subjectId}/revisions`),
  getRevision: (id: string) =>
    request<ProfileRevision>(`/api/profiles/revisions/${id}`),
  submitFeedback: (evidenceId: string, body: { action: 'accept' | 'edit' | 'reject' | 'uncertain'; feedback_text?: string }) =>
    request<{ id: string; action: string; feedback_text?: string }>(`/api/profiles/evidence/${evidenceId}/feedback`, { method: 'POST', body }),
  deleteImport: (id: string) =>
    request<void>(`/api/profiles/imports/${id}`, { method: 'DELETE' }),
};
