import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ApprovalsPage } from './ApprovalsPage';
import type { Proposal } from '../api/types';

vi.mock('../api/proposals', () => ({
  proposalsApi: {
    list: vi.fn(),
    decide: vi.fn(),
  },
}));

import { proposalsApi } from '../api/proposals';

function makeProposal(over: Partial<Proposal>): Proposal {
  return {
    id: 'p1',
    operation: 'task.merge',
    target_id: 'branch-x',
    expected_version: 1,
    payload: { branch: 'x' },
    reason: 'r',
    rollback: 'r',
    digest: 'abc123',
    status: 'pending',
    expires_at: new Date(Date.now() + 3600_000).toISOString(),
    decided_at: null,
    execution_id: null,
    version: 1,
    created_at: new Date().toISOString(),
    evidence_ids: ['ev1'],
    artifact_ids: ['art1'],
    environment: 'preview',
    cost_estimate: { amount: '0.00', currency: 'USD' },
    out_bound: { domains: ['work'] },
    ...over,
  };
}

describe('ApprovalsPage semantics (BUG-04)', () => {
  it('shows "not yet merged/released" for a pending merge proposal', async () => {
    vi.mocked(proposalsApi.list).mockResolvedValue({
      proposals: [makeProposal({ status: 'pending' })],
    });
    render(<ApprovalsPage />);
    await waitFor(() => expect(screen.getByText(/尚未合并\/发布/)).toBeInTheDocument());
    expect(screen.getByText(/待审批/)).toBeInTheDocument();
  });

  it('does NOT show "merged/released" wording once executed', async () => {
    vi.mocked(proposalsApi.list).mockResolvedValue({
      proposals: [makeProposal({ status: 'executed' })],
    });
    render(<ApprovalsPage />);
    await waitFor(() => expect(screen.getByText(/外部操作已执行/)).toBeInTheDocument());
    expect(screen.queryByText(/尚未合并\/发布/)).not.toBeInTheDocument();
  });

  it('renders digest and expected version', async () => {
    vi.mocked(proposalsApi.list).mockResolvedValue({
      proposals: [makeProposal({ digest: 'deadbeef' })],
    });
    render(<ApprovalsPage />);
    await waitFor(() => expect(screen.getByText('deadbeef')).toBeInTheDocument());
    expect(screen.getByText('branch-x')).toBeInTheDocument();
  });
});
