import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AssessmentsPage } from './AssessmentsPage';
import type {
  QuestionnaireInfo,
  RealAssessmentSession,
} from '../api/assessments';

vi.mock('../api/assessments', () => ({
  assessmentsApi: {
    catalog: vi.fn(),
    startSession: vi.fn(),
    submit: vi.fn(),
  },
}));

import { assessmentsApi } from '../api/assessments';

const q: QuestionnaireInfo = {
  id: 'bigfive-synthetic',
  version: '0.1.0-synthetic',
  title: 'Big Five (synthetic)',
  license: 'synthetic-template',
  source_note: 'Synthetic items only; no IPIP norms or clinical cut-offs.',
  dimensions: ['openness', 'conscientiousness'],
  item_count: 2,
  synthetic: true,
};

const session: RealAssessmentSession = {
  session_id: 's1',
  questionnaire_id: q.id,
  questionnaire_version: q.version,
  item_set_hash: 'ab12',
  status: 'in_progress',
  missing: ['i1', 'i2'],
  result: null,
};

describe('AssessmentsPage (U05/U06)', () => {
  it('does not submit / fabricate a result when answers are missing', async () => {
    vi.mocked(assessmentsApi.catalog).mockResolvedValue({ questionnaires: [q] });
    vi.mocked(assessmentsApi.startSession).mockResolvedValue(session);
    const user = userEvent.setup();

    render(<AssessmentsPage />);
    await user.click(await screen.findByRole('button', { name: /开始测评/ }));
    await screen.findAllByLabelText(/请评分/);

    // Leave both items unanswered, then submit.
    await user.click(screen.getByRole('button', { name: /提交/ }));

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toMatch(/漏答/),
    );
    expect(assessmentsApi.submit).not.toHaveBeenCalled();
  });

  it('shows questionnaire version and synthetic source note', async () => {
    vi.mocked(assessmentsApi.catalog).mockResolvedValue({ questionnaires: [q] });
    vi.mocked(assessmentsApi.startSession).mockResolvedValue(session);
    const user = userEvent.setup();
    render(<AssessmentsPage />);
    await user.click(await screen.findByRole('button', { name: /开始测评/ }));
    expect(screen.getByText(new RegExp('问卷 v' + q.version))).toBeInTheDocument();
    expect(screen.getAllByText(/Synthetic/i).length).toBeGreaterThan(0);
  });

  it('renders custom four-dimension exploratory non-official labels and disclaimer (U07)', async () => {
    const scoredSession: RealAssessmentSession = {
      session_id: 's-fourdim',
      questionnaire_id: 'fourdim-exploratory',
      questionnaire_version: '0.1.0-exploratory',
      item_set_hash: 'fd1234',
      status: 'scored',
      missing: [],
      result: {
        type_label: '[非官方探索倾向: ENFP]',
        scales: { EI: 4.0, SN: 4.5, TF: 3.8, JP: 4.2 },
        official_mbti: false,
        clinical: false,
        caveat: '探索性自我反思工具，绝非官方 MBTI® 认证报告，亦非医学/心理诊断。',
        interpretation: '在当前探索性题目中体现出 ENFP 维度的情境倾向。',
        norm_note: '无匹配常模，不提供人群百分位。',
      },
    };
    vi.mocked(assessmentsApi.catalog).mockResolvedValue({ questionnaires: [q] });
    vi.mocked(assessmentsApi.startSession).mockResolvedValue(scoredSession);
    const user = userEvent.setup();
    render(<AssessmentsPage />);
    await user.click(await screen.findByRole('button', { name: /开始测评/ }));

    expect(screen.getByText(/\[非官方探索倾向: ENFP\]/)).toBeInTheDocument();
    expect(screen.getByText(/绝非官方 MBTI® 认证报告/)).toBeInTheDocument();
    expect(screen.getByText(/无匹配常模，不提供人群百分位。/)).toBeInTheDocument();
  });
});
