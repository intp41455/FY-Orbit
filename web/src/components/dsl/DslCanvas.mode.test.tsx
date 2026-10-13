/**
 * FY-N4 · G4-i：文本画布（DslCanvas）也分三重模式（R10）。
 *
 * 背景（为什么这条不是「顺手加的」）：`DslCanvasPage` 把 `mode` 传给了
 * `FlowEditor`，却**没有**传给 `DslCanvas`。于是小白模式下 `FlowEditor`
 * 会收起代码视图/手改/导出，而同一个页面的「DSL 文本模式」页签里，
 * 那份同源的 `dsl-json` 仍然明晃晃摊在画布下方——同一个「小白用户」
 * 在两个页签看到两套复杂度标准，R10 从页面上被绕过。
 *
 * 修法钉死为**折叠而非卸载**：`<details>` 默认收起。
 *  - 收起态内容**仍在 DOM 里**（`getByTestId` 照常拿得到）⇒
 *    `DslCanvasPage.test.tsx` 里 beginner 模式读 `dsl-json` 的同源断言
 *    （`dslDoc()`）不受影响；
 *  - 收起 ≠ 隐藏：`<details>` 的折叠内容仍可被读屏/搜索命中，
 *    这与项目内既有折叠区（如「已导出脚本」）的语义一致。
 *
 * 断言原语用 jest-dom 的 `toBeVisible()`：它沿 `parentElement` 递归向上查，
 * 收起态 `<details>` 的非 summary 后代判定为不可见（与 jsdom 是否实现
 * `checkVisibility()` 无关）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  // 单一真源：展开真模块保留常量与类型，只替换网络面（照抄 DslCanvas.test.tsx）。
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: {
    run: vi.fn(), validate: vi.fn(), schema: vi.fn(), getRun: vi.fn(),
    validateIr: vi.fn(),
  },
}));

// G4-f：画布上挂了 GeneratePanel，不 mock 它就会打真实 fetch。
vi.mock('../../api/workflowGen', () => ({
  workflowGenApi: { status: vi.fn(), generate: vi.fn() },
}));

import { DslCanvas } from './DslCanvas';
import { dslCanvasApi } from '../../api/dslCanvas';
import { workflowGenApi } from '../../api/workflowGen';

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workflowGenApi.status).mockResolvedValue({ model_configured: true });
  vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: true, diagnostics: [] });
});

describe('G4-i · R10 文本画布也分三重模式', () => {
  it('默认（technical）保持现状：dsl-json 直接可见，不多一层折叠壳', () => {
    render(<DslCanvas />);

    // R4 · 默认值不得改：既有 4 个 DslCanvas 测试文件全靠它
    expect(screen.getByTestId('dsl-json')).toBeVisible();
    expect(screen.queryByTestId('dsl-json-summary')).toBeNull();
  });

  it('beginner：dsl-json 收进折叠区——收起态不可见，但仍在 DOM 里', () => {
    render(<DslCanvas mode="beginner" />);

    const json = screen.getByTestId('dsl-json');
    expect(json).not.toBeVisible();
    // 关键诚实断言：折叠 ≠ 卸载。DSLCanvasPage 的 beginner 同源断言
    // （dslDoc() = getByTestId('dsl-json').textContent）必须照常工作。
    expect(json).toBeInTheDocument();
    expect(JSON.parse(json.textContent ?? '{}')).toEqual({ version: '1', nodes: [], edges: [] });
    // 折叠入口存在
    expect(screen.getByTestId('dsl-json-summary')).toBeInTheDocument();
  });

  it('beginner：点开折叠区 → dsl-json 变可见，且 JSON 仍是同一份', async () => {
    const user = userEvent.setup();
    render(<DslCanvas mode="beginner" />);

    const before = screen.getByTestId('dsl-json').textContent;
    await user.click(screen.getByTestId('dsl-json-summary'));

    expect(screen.getByTestId('dsl-json')).toBeVisible();
    expect(screen.getByTestId('dsl-json').textContent).toBe(before);
  });

  it('beginner：折叠只影响 JSON 区，节点面板（能搭什么）照旧可见', () => {
    render(<DslCanvas mode="beginner" />);

    expect(screen.getByTestId('dsl-palette')).toBeVisible();
    expect(screen.getByTestId('palette-input')).toBeVisible();
    expect(screen.getByTestId('dsl-canvas-area')).toBeVisible();
    expect(screen.getByTestId('dsl-generate')).toBeVisible();
    expect(screen.getByTestId('dsl-run')).toBeVisible();
    // 被折起来的只有「怎么读图」这一层技术面
    expect(screen.getByTestId('dsl-json')).not.toBeVisible();
  });

  it('enterprise：与技术模式同档，dsl-json 可见且无折叠壳', () => {
    render(<DslCanvas mode="enterprise" />);

    expect(screen.getByTestId('dsl-json')).toBeVisible();
    expect(screen.queryByTestId('dsl-json-summary')).toBeNull();
  });
});