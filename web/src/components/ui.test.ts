import { describe, expect, it } from 'vitest';
import { errorMessage } from './ui';

describe('errorMessage 团队错误中文化', () => {
  it('按 code 命中中文（与 message 语言无关，兜住旧版英文）', () => {
    expect(
      errorMessage({
        status: 422,
        body: {
          code: 'team_not_started',
          message: "Operation 'pause' needs a started member instance; start the team first.",
        },
      }),
    ).toContain('草稿');
  });

  it('未知 code 仍原样透出（不编造）', () => {
    expect(
      errorMessage({ status: 422, body: { code: 'wick_xyz', message: 'Something odd' } }),
    ).toBe('Something odd');
  });
});
