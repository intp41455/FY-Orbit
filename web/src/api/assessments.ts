import { request } from './client';
import type {
  AssessmentCatalogResponse,
  AssessmentSession,
  SubmitAssessmentInput,
} from './types';

export const assessmentsApi = {
  catalog: () => request<AssessmentCatalogResponse>('/api/assessments/catalog'),
  startSession: (assessmentId: string) =>
    request<AssessmentSession>(`/api/assessments/${assessmentId}/sessions`, {
      method: 'POST',
    }),
  saveAnswers: (sessionId: string, answers: Record<string, number>) =>
    request<AssessmentSession>(`/api/assessments/sessions/${sessionId}/answers`, {
      method: 'POST',
      body: { answers },
    }),
  submit: (sessionId: string, body: SubmitAssessmentInput) =>
    request<AssessmentSession>(`/api/assessments/sessions/${sessionId}/submit`, {
      method: 'POST',
      body,
    }),
  getSession: (sessionId: string) =>
    request<AssessmentSession>(`/api/assessments/sessions/${sessionId}`),
};
