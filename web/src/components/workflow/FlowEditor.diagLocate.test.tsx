/**
 * G4-g2 · 载入非法 DSL 时必须给出**可定位**的诊断（不是只有一句 message）。
 *
 * 缺陷复现（修复前）：`FlowEditor.loadFromText` 走 `POST /api/dsl-canvas/validate`，
 * 后端 `routes/dsl_canvas.py::validate` 用 `ValidationFailed("validation_failed", str(exc))`
 * 把 `DslValidationError` 翻成统一错误信封，而该信封**不带任何定位信息**
 * （`details` 恒为 `{}`，见 `api/errors.py::_envelope`）。于是前端读
 * `err.body.details.line` / `.column` 永远拿到 `undefined` → 顶部只显示一句
 * 「transform 节点 b verb 必须是 ...」，用户不知道**哪个节点的哪个字段**坏了。
 *
 * 修法：/validate 保持权威闸门（fail-fast、含环检测），失败时补调一次收集式
 * `/validate-ir`，把 `diagnostics[]` 的 `node_id + field_path` 显示到错误条上。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: { validate: vi.fn(), schema: vi.fn(), validateIr: vi.fn() },
}));
vi.mock('../../api/workflowGen', () => ({
  workflowGenApi: { exportScript: vi.fn() },
}));

import { dslCanvasApi } from '../../api/dslCanvas';
import { ApiError } from '../../api/client';
import { FlowEditor, formatDiagnosticLocations } from './FlowEditor';
import type { DslDiagnostic } from '../../api/dslCanvas';

/** 与后端 `services/dsl_ir.py::Diagnostic.to_dict()` 逐字对齐。 */
const DIAGS: DslDiagnostic[] = [
  {
    node_id: 'b', field_path: 'verb', code: 'unknown_verb',
    message: "transform 节点 verb 必须是 ('map', 'filter', 'template')，实际是 'translate'",
  },
  {
    node_id: 'out1', field_path: 'params.bogus', code: 'extra_field',
    message: 'Extra inputs are not permitted',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dslCanvasApi.schema).mockResolvedValue({
    schema: {}, node_types: ['input', 'transform', 'output'],
    transform_verbs: ['map', 'filter', 'template', 'branch', 'aggregate', 'merge', 'agent', 'confirm', 'artifact'],
    verb_catalog: [],
    aggregate_ops: ['count', 'sum'], merge_ops: ['concat'], output_formats: ['json', 'text'],
  });
  // 防抖 IR 校验 effect 默认通过（空图）
  vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: true, diagnostics: [] });
});

/** 提交一段非法 DSL 并等错误条出现。 */
async function submitBadDsl(): Promise<void> {
  const user = userEvent.setup();
  render(<FlowEditor />);
  await screen.findByTestId('flow-palette-input');
  await user.click(screen.getByTestId('flow-dsl-input'));
  await user.paste('{"version":"1","nodes":[{"id":"b","type":"transform","verb":"translate"}],"edges":[]}');
  await user.click(screen.getByTestId('flow-load'));
  await screen.findByTestId('flow-error');
}

// =========================================================================== //
// 1. 纯函数：诊断 → 可定位摘要
// =========================================================================== //

describe('formatDiagnosticLocations · IR 诊断压成可定位摘要', () => {
  it('每条诊断都带出节点 id 与字段路径', () => {
    const out = formatDiagnosticLocations(DIAGS);
    expect(out).toContain('b');
    expect(out).toContain('verb');
    expect(out).toContain('out1');
    expect(out).toContain('params.bogus');
  });

  it('一次全量给出，绝不只报第一条（与 DslDiagnostics 的铁律一致）', () => {
    expect(formatDiagnosticLocations(DIAGS).split('；')).toHaveLength(2);
  });

  it('文档级诊断（node_id 为空）标成「文档」而不是留空让人以为漏了', () => {
    const out = formatDiagnosticLocations([
      { node_id: '', field_path: 'version', code: 'invalid_version', message: 'version 必须为 "1"' },
    ]);
    expect(out).toContain('文档');
    expect(out).toContain('version');
  });

  it('无诊断时返回空串——绝不编造位置', () => {
    expect(formatDiagnosticLocations([])).toBe('');
  });
});

// =========================================================================== //
// 2. 组件：loadFromText 失败分支必须显示定位
// =========================================================================== //

describe('FlowEditor · 载入非法 DSL 时错误条带定位信息（G4-g2）', () => {
  it('/validate 拒绝后，错误条里出现 validate-ir 给的 node_id + field_path', async () => {
    // 真实后端形状：details 恒为 {}（api/errors.py::_envelope），没有 line/column。
    vi.mocked(dslCanvasApi.validate).mockRejectedValue(
      new ApiError(422, {
        code: 'validation_failed',
        message: "transform 节点 b verb 必须是 ('map', 'filter', 'template')，实际是 'translate'",
        details: {},
      }, 'Request failed with status 422'),
    );
    vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: false, diagnostics: DIAGS });

    await submitBadDsl();

    const banner = screen.getByTestId('flow-error');
    expect(banner.textContent).toContain("必须是 ('map', 'filter', 'template')");
    const pos = screen.getByTestId('flow-error-pos').textContent ?? '';
    expect(pos).toContain('b');
    expect(pos).toContain('verb');
    expect(pos).toContain('out1');
    expect(pos).toContain('params.bogus');
    // 失败后必须真的去问了一次收集式 IR 校验
    await waitFor(() => expect(dslCanvasApi.validateIr).toHaveBeenCalled());
  });

  it('validate-ir 也拿不到诊断时：只显示 message，不显示任何编造的位置', async () => {
    vi.mocked(dslCanvasApi.validate).mockRejectedValue(
      new ApiError(422, {
        code: 'validation_failed', message: 'DSL 校验未通过：图中有环', details: {},
      }, 'Request failed with status 422'),
    );
    vi.mocked(dslCanvasApi.validateIr).mockRejectedValue(new Error('500'));

    await submitBadDsl();

    expect(screen.getByTestId('flow-error').textContent).toContain('图中有环');
    expect(screen.queryByTestId('flow-error-pos')).toBeNull();
  });

  it('/validate 通过时不额外调 validate-ir 定位（不制造无谓请求）', async () => {
    vi.mocked(dslCanvasApi.validate).mockResolvedValue({ valid: true, topological_order: [] });
    const user = userEvent.setup();
    render(<FlowEditor />);
    await screen.findByTestId('flow-palette-input');
    vi.mocked(dslCanvasApi.validateIr).mockClear();

    await user.click(screen.getByTestId('flow-dsl-input'));
    await user.paste('{"version":"1","nodes":[],"edges":[]}');
    await user.click(screen.getByTestId('flow-load'));

    await waitFor(() => expect(dslCanvasApi.validate).toHaveBeenCalled());
    expect(screen.queryByTestId('flow-error')).toBeNull();
    // 防抖 effect 的那一次之外没有新增调用
    expect(vi.mocked(dslCanvasApi.validateIr).mock.calls.length).toBeLessThanOrEqual(1);
  });
});