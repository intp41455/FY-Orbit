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
});
