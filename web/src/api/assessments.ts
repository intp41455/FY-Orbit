import { request } from './client';

// Real backend shapes (observed on loopback). The questionnaire catalog exposes
// item metadata but NOT item text; the session exposes the missing item ids and
// a server-computed result. Scoring, reverse-scoring and norms live server-side.
export interface QuestionnaireInfo {
  id: string;
  version: string;
  title: string;
  license: string;
  source_note: string;
  dimensions: string[];
  item_count: number;
  synthetic: boolean;
}
export interface AssessmentCatalogResponse {
  questionnaires: QuestionnaireInfo[];
}
export interface RealAssessmentResult {
  scales: Record<string, number>;
  type_label?: string;
  interpretation?: string;
  caveat?: string;
  norm_note?: string;
  official_mbti?: boolean;
  clinical?: boolean;
  synthetic?: boolean;
}
export interface RealAssessmentSession {
  session_id: string;
  questionnaire_id: string;
  questionnaire_version: string;
  item_set_hash: string;
  status: 'in_progress' | 'submitted' | string;
  missing: string[];
  result: RealAssessmentResult | null;
}

export const assessmentsApi = {
  catalog: () => request<AssessmentCatalogResponse>('/api/assessments/catalog'),
  startSession: (questionnaireId: string) =>
    request<RealAssessmentSession>(`/api/assessments/${questionnaireId}/sessions`, { method: 'POST' }),
  submit: (sessionId: string, answers: Record<string, number>) =>
    request<RealAssessmentSession>(`/api/assessments/sessions/${sessionId}/submit`, {
      method: 'POST',
      body: { answers },
    }),
};
