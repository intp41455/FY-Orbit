import { request } from './client';
import type { Proposal, ProposalDecisionInput, ProposalDigestInput } from './types';

export const proposalsApi = {
  list: () => request<{ proposals: Proposal[] }>('/api/proposals'),
  get: (id: string) => request<Proposal>(`/api/proposals/${id}`),
  create: (body: ProposalDigestInput) =>
    request<Proposal>('/api/proposals', { method: 'POST', body }),
  // Only the owner session may call this (§5.3). The UI must re-display the
  // digest the user reviewed before sending it back.
  decide: (id: string, body: ProposalDecisionInput) =>
    request<Proposal>(`/api/proposals/${id}/decision`, { method: 'POST', body }),
};
