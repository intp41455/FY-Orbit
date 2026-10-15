import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../api/charts', () => ({
  chartsApi: {
    dailyFortune: vi.fn(),
    drawTarot: vi.fn(),
    computeChart: vi.fn(),
    computeSynastry: vi.fn(),
  },
}));

import { chartsApi } from '../api/charts';
import { FortunePage } from './FortunePage';

const api = chartsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

describe('FortunePage 合盘仅供娱乐标注（I-11）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.computeSynastry!.mockResolvedValue({
      compatibility_score: 84,
      element_balance: {},
      synergy_points: ['日主对照（A: 木 / B: 火），五行分布见下表，供对照参考。'],
      friction_points: ['时间紧张时建议建立显式的同步节奏。'],
      actionable_advice: '合作中可先明确各自的分工边界。',
      disclaimer: '【仅供娱乐，勿当真】合盘分数是古典五行计数对照的启发式数字。',
    });
  });

  it('结果区顶部显著标注仅供娱乐并展示 disclaimer', async () => {
    render(<FortunePage />);
    fireEvent.click(screen.getByText('💫 能量合盘'));
    fireEvent.click(screen.getByText('计算五行契合度'));
    const note = await screen.findByTestId('synastry-entertainment-note');
    expect(note.textContent).toContain('仅供娱乐，勿当真');
    expect(screen.getByTestId('synastry-disclaimer').textContent).toContain('仅供娱乐');
  });
});
