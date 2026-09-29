import { request } from './client';
import type { AgentInfo, ArtifactInfo, SkillInfo } from './types';

export const catalogApi = {
  // Real backend returns bare JSON arrays.
  agents: () => request<AgentInfo[]>('/api/agents'),
  skills: () => request<SkillInfo[]>('/api/skills'),
  artifact: (id: string) => request<ArtifactInfo>(`/api/artifacts/${id}`),
};
