import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProfilesPage } from './ProfilesPage';
import type { ProfileRevision, ProfileSubject } from '../api/profiles';

vi.mock('../api/profiles', () => ({
  profilesApi: {
    listSubjects: vi.fn(),
    createSubject: vi.fn(),
    getSubject: vi.fn(),
    importDocument: vi.fn(),
    runProfiling: vi.fn(),
    listRevisions: vi.fn(),
    getRevision: vi.fn(),
    submitFeedback: vi.fn(),
    deleteImport: vi.fn(),
  },
}));

import { profilesApi } from '../api/profiles';

const mockSubject: ProfileSubject = {
  id: 'subj-1',
  owner_id: 'user-1',
  kind: 'self',
  label: '本人档案',
  description: '核心自我画像',
  confirmed: true,
  created_at: new Date().toISOString(),
};

const mockRevision: ProfileRevision = {
  id: 'rev-1',
  subject_id: 'subj-1',
  revision: 1,
  core_summary: {
    title: '本人档案 多维特征透视 (Rev 1)',
    summary: '基于测试切片提炼之特征画像',
    evidence_count: 2,
  },
  clusters: [
    {
      id: 'c_core',
      name: '核心特质',
      summary: '核心行为观察',
      nodes: [
        {
          id: 'n_1',
          label: '防御性架构偏好',
          description: '主张采用严格的不可变审计日志与防御性架构',
          claim_kind: 'observation',
          confidence: 0.92,
          review_status: 'pending',
          evidence_refs: ['ev-1'],
          counter_evidence_refs: [],
        },
      ],
    },
  ],
  metrics: [
    { dimension: '工程严谨度', score: 92, metric_type: 'corpus_stat', evidence_count: 5 },
    { dimension: '目标导向', score: 80, metric_type: 'self_report', evidence_count: 2 },
  ],
  limitations: [
    '分析基于用户导入的有限语料样本',
    '本画像呈现文本可推演之特征与自述，严格不代表临床心理学或医学诊断结论',
  ],
  user_review_state: 'confirmed',
  created_at: new Date().toISOString(),
};

describe('ProfilesPage (04 功能规格)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders subject list, clusters, and clinical disclaimer', async () => {
    vi.mocked(profilesApi.listSubjects).mockResolvedValue({ items: [mockSubject], count: 1 });
    vi.mocked(profilesApi.listRevisions).mockResolvedValue({ items: [mockRevision], count: 1 });

    render(<ProfilesPage />);

    expect(await screen.findByText('本人档案')).toBeInTheDocument();
    expect(await screen.findByText('本人档案 多维特征透视 (Rev 1)')).toBeInTheDocument();
    expect(screen.getByText('防御性架构偏好')).toBeInTheDocument();
    expect(screen.getByText(/严格禁止下达任何临床诊断/)).toBeInTheDocument();
    expect(screen.getByText('语料统计')).toBeInTheDocument();
    expect(screen.getByText('用户自述')).toBeInTheDocument();
  });

  it('allows user to submit evidence feedback', async () => {
    vi.mocked(profilesApi.listSubjects).mockResolvedValue({ items: [mockSubject], count: 1 });
    vi.mocked(profilesApi.listRevisions).mockResolvedValue({ items: [mockRevision], count: 1 });
    vi.mocked(profilesApi.submitFeedback).mockResolvedValue({
      id: 'fb-1',
      action: 'accept',
      feedback_text: '',
    });

    const user = userEvent.setup();
    render(<ProfilesPage />);

    const acceptBtn = await screen.findByRole('button', { name: '确认' });
    await user.click(acceptBtn);

    await waitFor(() => {
      expect(profilesApi.submitFeedback).toHaveBeenCalledWith('ev-1', { action: 'accept' });
    });
  });
});
