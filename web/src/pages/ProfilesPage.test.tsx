import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProfilesPage } from './ProfilesPage';
import type { ProfileRevision, ProfileSubject, ProfileImport } from '../api/profiles';

vi.mock('../api/profiles', () => ({
  profilesApi: {
    listSubjects: vi.fn(),
    createSubject: vi.fn(),
    getSubject: vi.fn(),
    importDocument: vi.fn(),
    confirmSpeakers: vi.fn(),
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
    formal_norm: false,
    norm_note: '未接入标准化心理量表授权输入，不呈现推测性能力分或人格测评常模分；以上呈现指标为可重算语料客观统计',
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
          locator: 'dialogue.txt:L1',
          speaker: 'Alice',
          source_segment_id: 'seg-1',
          x: 120,
          y: 100,
        },
      ],
    },
  ],
  edges: [
    {
      id: 'edge-1',
      source: 'n_1',
      target: 'n_1',
      relation: 'reinforces',
      strength: 1.0,
    },
  ],
  metrics: [
    {
      dimension: '切片样本量 (segment_count)',
      score: 100,
      raw_value: 12,
      display_value: '12 段',
      calculation_formula: 'count(corpus_segments)',
      metric_type: 'corpus_stat',
      evidence_count: 12,
    },
    {
      dimension: '工程严谨度',
      score: 92,
      display_value: '92%',
      calculation_formula: 'count(engineering_domain_keywords) / total_words',
      metric_type: 'corpus_stat',
      evidence_count: 5,
    },
    {
      dimension: '目标导向',
      score: 80,
      display_value: '80%',
      calculation_formula: 'first_person_declarations',
      metric_type: 'self_report',
      evidence_count: 2,
    },
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

  it('renders subject list, clusters, norm disclaimer, and deterministic text metrics', async () => {
    vi.mocked(profilesApi.listSubjects).mockResolvedValue({ items: [mockSubject], count: 1 });
    vi.mocked(profilesApi.listRevisions).mockResolvedValue({ items: [mockRevision], count: 1 });

    render(<ProfilesPage />);

    expect(await screen.findByText('本人档案')).toBeInTheDocument();
    expect(await screen.findByText('本人档案 多维特征透视 (Rev 1)')).toBeInTheDocument();
    expect(screen.getAllByText('防御性架构偏好').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/未接入标准化心理量表授权输入/)).toBeInTheDocument();
    expect(screen.getByText(/非标准化常模 \(Formal Norm: False\)/)).toBeInTheDocument();
    expect(screen.getByText('切片样本量 (segment_count)')).toBeInTheDocument();
    expect(screen.getByText('12 段')).toBeInTheDocument();
    expect(screen.getByText(/count\(corpus_segments\)/)).toBeInTheDocument();
    expect(screen.getAllByText('语料统计').length).toBeGreaterThanOrEqual(1);
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

  it('switches to list view and shows source segment locators', async () => {
    vi.mocked(profilesApi.listSubjects).mockResolvedValue({ items: [mockSubject], count: 1 });
    vi.mocked(profilesApi.listRevisions).mockResolvedValue({ items: [mockRevision], count: 1 });

    const user = userEvent.setup();
    render(<ProfilesPage />);

    expect(await screen.findByText('本人档案 多维特征透视 (Rev 1)')).toBeInTheDocument();

    const listViewBtn = screen.getByRole('button', { name: '详细列表' });
    await user.click(listViewBtn);

    expect(screen.getByText(/dialogue\.txt:L1/)).toBeInTheDocument();
  });

  it('handles speaker confirmation after document import', async () => {
    vi.mocked(profilesApi.listSubjects).mockResolvedValue({ items: [mockSubject], count: 1 });
    vi.mocked(profilesApi.listRevisions).mockResolvedValue({ items: [mockRevision], count: 1 });
    const mockImportRes: ProfileImport = {
      id: 'imp-10',
      owner_id: 'user-1',
      subject_id: 'subj-1',
      subject_candidates: [
        { speaker: 'Alice', candidate_subject: 'self', segment_count: 5 },
        { speaker: 'Bob', candidate_subject: 'third_party', segment_count: 3 },
      ],
      original_asset_ref: 's3://bucket/test.txt',
      source_type: 'dialogue',
      size: 100,
      sha256: 'abc',
      status: 'analyzed',
      created_at: new Date().toISOString(),
    };
    vi.mocked(profilesApi.importDocument).mockResolvedValue(mockImportRes);
    vi.mocked(profilesApi.confirmSpeakers).mockResolvedValue({
      import_id: 'imp-10',
      updated_segments: 8,
      mappings: { Alice: 'self', Bob: 'third_party' },
    });

    const user = userEvent.setup();
    render(<ProfilesPage />);

    const textarea = await screen.findByLabelText('文本内容');
    await user.type(textarea, 'Alice: testing dialogue');

    const submitImportBtn = screen.getByRole('button', { name: '提交语料切片' });
    await user.click(submitImportBtn);

    expect(await screen.findByText(/检测到的发言人切片归属映射/)).toBeInTheDocument();

    const confirmBtn = screen.getByRole('button', { name: '确认发言人归属并生效' });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(profilesApi.confirmSpeakers).toHaveBeenCalledWith('imp-10', expect.any(Object));
    });
    expect(await screen.findByText(/已确认 8 个发言人切片归属/)).toBeInTheDocument();
  });
});
