import { request } from './client';
import type { AgentInfo, ArtifactInfo, SkillInfo } from './types';

export const catalogApi = {
  agents: () => request<{ agents: AgentInfo[] }>('/api/agents'),
  skills: () => request<{ skills: SkillInfo[] }>('/api/skills'),
  artifact: (id: string) => request<ArtifactInfo>(`/api/artifacts/${id}`),
};
