/**
 * G4-k 开箱模板→工坊通道：模板页「去工坊编辑」把模板经代码换回 DSL、
 * 写一次性交接并跳到 /dsl-canvas。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { TemplatesPage } from './TemplatesPage';

const navSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navSpy,
}));

vi.mock('../api/templates', () => ({
  controllerCheck: vi.fn(),
  exampleRun: vi.fn(),
  expandToCode: vi.fn(),
  fetchLayers: vi.fn(),
  fetchQualityTiers: vi.fn(),
  instantiate: vi.fn(),
  checkSource: vi.fn(),
  listTemplates: vi.fn(),
  restoreFactory: vi.fn(),
  templateDetail: vi.fn(),
}));

vi.mock('../api/dslCanvas', () => ({
  dslCanvasApi: { importCode: vi.fn() },
}));

vi.mock('../api/dslHandoff', () => ({
  writeDslHandoff: vi.fn(),
  takeDslHandoff: vi.fn(() => null),
}));

import {
  checkSource,
  expandToCode,
  fetchLayers,
  fetchQualityTiers,
  instantiate,
  listTemplates,
  templateDetail,
} from '../api/templates';
import { dslCanvasApi } from '../api/dslCanvas';
import { writeDslHandoff } from '../api/dslHandoff';

const CARD = {
  template_id: 't1',
  name: '写作流水线',
  scenario: 'writing',
  scenario_label: '写作',
  layer: 'novice',
  quality_tier: 'novice',
  summary: '写东西',
  member_count: 2,
  overview: {},
};

const DETAIL = {
  schema_version: '1.0.0',
  template_id: 't1',
  name: '写作流水线',
  scenario: 'writing',
  layer: 'novice',
  quality_tier: 'novice',
  problems: [],
  controller: {
    id: 'c', role: '总控', duties: ['调度'], forbidden_rules: [], system_prompt: 'p',
  },
  members: [],
  example_task: { goal: '写', steps: 3 },
  overview: {
    name: '写作流水线', scenario: 'writing', layer: 'novice', member_count: 2,
    controller: { duties: ['调度'] }, artifact_rule: {},
    estimate: { steps: 3, approx_model_calls: 2 },
    members: [],
  },
  essentials_view: [],
};

const DSL = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: 'x' } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listTemplates).mockResolvedValue({ items: [CARD] } as never);
  vi.mocked(fetchLayers).mockResolvedValue({ layers: [] } as never);
  vi.mocked(fetchQualityTiers).mockResolvedValue({ tiers: [{ id: 'novice', label: '小白档' }] } as never);
  vi.mocked(templateDetail).mockResolvedValue(DETAIL as never);
  vi.mocked(expandToCode).mockResolvedValue({ code: 'CODE', filename: 't.py' } as never);
  vi.mocked(dslCanvasApi.importCode).mockResolvedValue({ dsl: DSL, topological_order: [] } as never);
});

describe('TemplatesPage 去工坊编辑（G4-k）', () => {
  it('模板代码经 import-code 换回 DSL、写交接并跳工坊', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole('button', { name: '去工坊编辑' }));
    await waitFor(() => expect(expandToCode).toHaveBeenCalledWith('t1', 'novice'));
    await waitFor(() =>
      expect(dslCanvasApi.importCode).toHaveBeenCalledWith('CODE'),
    );
    expect(writeDslHandoff).toHaveBeenCalledWith(DSL, '开箱模板:t1');
    expect(navSpy).toHaveBeenCalledWith('/dsl-canvas');
  });

  it('换回失败时诚实报错、不跳转', async () => {
    const user = userEvent.setup();
    vi.mocked(dslCanvasApi.importCode).mockRejectedValue(new Error('parse 炸了'));
    render(
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole('button', { name: '去工坊编辑' }));
    await waitFor(() => expect(navSpy).not.toHaveBeenCalled());
    expect(writeDslHandoff).not.toHaveBeenCalled();
  });
});

describe('TemplatesPage 实体化引用（G4-a）', () => {
  const REF = {
    template_id: 't1',
    tier: 'novice',
    schema_version: '1.0.0',
    instantiated_at: '2026-10-15T00:00:00+00:00',
  };

  it('创建结果显示来源行，查新鲜度正常显示一致', async () => {
    const user = userEvent.setup();
    vi.mocked(instantiate).mockResolvedValue({
      template_id: 't1',
      created_from: 'factory',
      quality_tier: 'novice',
      source_ref: REF,
      system: {},
      unresolved: [],
      warnings: [],
      runnable: true,
      message: '可直接跑通一次完整任务',
    });
    vi.mocked(checkSource).mockResolvedValue({
      template_id: 't1',
      tier: 'novice',
      ref_schema_version: '1.0.0',
      current_schema_version: '1.0.0',
      stale: false,
    });
    render(
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole('button', { name: '创建我的系统' }));
    expect(await screen.findByTestId('source-ref-line')).toHaveTextContent('t1');
    await user.click(screen.getByTestId('source-check'));
    await waitFor(() =>
      expect(checkSource).toHaveBeenCalledWith(REF),
    );
    expect(await screen.findByTestId('source-status')).toHaveTextContent('一致');
  });

  it('源模板演进后查新报 stale 警告', async () => {
    const user = userEvent.setup();
    vi.mocked(instantiate).mockResolvedValue({
      template_id: 't1',
      created_from: 'factory',
      quality_tier: 'novice',
      source_ref: REF,
      system: {},
      unresolved: [],
      warnings: [],
      runnable: true,
      message: 'ok',
    });
    vi.mocked(checkSource).mockResolvedValue({
      template_id: 't1',
      tier: 'novice',
      ref_schema_version: '0.0.1',
      current_schema_version: '1.0.0',
      stale: true,
    });
    render(
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole('button', { name: '创建我的系统' }));
    await user.click(await screen.findByTestId('source-check'));
    expect(await screen.findByTestId('source-status')).toHaveTextContent('已演进');
  });
});
