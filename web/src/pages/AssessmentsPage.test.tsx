import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AssessmentsPage } from './AssessmentsPage';
import type { AssessmentCatalogEntry, AssessmentSession } from '../api/types';

vi.mock('../api/assessments', () => ({
  assessmentsApi: {
    catalog: vi.fn(),
    startSession: vi.fn(),
    submit: vi.fn(),
    saveAnswers: vi.fn(),
    getSession: vi.fn(),
  },
}));

import { assessmentsApi } from '../api/assessments';

const entry: AssessmentCatalogEntry = {
  assessment_id: 'big5',
  kind: 'big5',
  title: '大五人格（IPIP）',
  version: '1.0',
  description: 'desc',
  compliance_notice: '基于合法 IPIP 条目；无群体常模，不输出百分位。',
  scale_min: 1,
  scale_max: 5,
  items: [
    { id: 'i1', text: '我喜欢社交', reverse_scored: false },
    { id: 'i2', text: '我经常焦虑', reverse_scored: true },
  ],
};

const session: AssessmentSession = {
  id: 's1',
  assessment_id: 'big5',
  questionnaire_version: '1.0',
  started_at: new Date().toISOString(),
  submitted_at: null,
  answers: {},
  completed: false,
  result: null,
};

describe('AssessmentsPage (U05/U06)', () => {
  it('does not submit / fabricate a result when answers are missing', async () => {
    vi.mocked(assessmentsApi.catalog).mockResolvedValue({ assessments: [entry] });
    vi.mocked(assessmentsApi.startSession).mockResolvedValue(session);
    const user = userEvent.setup();

    render(<AssessmentsPage />);
    await user.click(await screen.findByRole('button', { name: /开始测评/ }));
    await screen.findByLabelText(/我喜欢社交/);

    // Answer only one of two questions.
    await user.selectOptions(screen.getByLabelText(/我喜欢社交/), '4');
    await user.click(screen.getByRole('button', { name: /提交/ }));

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toMatch(/漏答|未作答/),
    );
    expect(assessmentsApi.submit).not.toHaveBeenCalled();
  });

  it('shows reverse-scored badge and questionnaire version', async () => {
    vi.mocked(assessmentsApi.catalog).mockResolvedValue({ assessments: [entry] });
    vi.mocked(assessmentsApi.startSession).mockResolvedValue(session);
    const user = userEvent.setup();
    render(<AssessmentsPage />);
    await user.click(await screen.findByRole('button', { name: /开始测评/ }));
    expect((await screen.findAllByText(/反向计分/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/问卷版本 v1\.0/)).toBeInTheDocument();
    expect(screen.getAllByText(/IPIP/).length).toBeGreaterThan(0);
  });
});
