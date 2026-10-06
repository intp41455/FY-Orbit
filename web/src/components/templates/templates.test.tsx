/**
 * P13 开箱模板前端组件测试（A-开箱模板-02/03/07）。
 *
 * 只测**界面契约**，不重复测后端逻辑：
 *   - 构成摘要必须一眼可见（成员 / 职责 / 工具 / 产出 / 大致用量）；
 *   - 用量必须标注「预估」，不能被渲染成账单数字；
 *   - 八项必备条件的**说明**必须能展开看到；
 *   - 逐项覆盖回传的是「值」（对象按 JSON 解析），不是整条必备项；
 *   - 总控提示词的警告**不阻断**（按钮仍可用，并明确说明「不会阻断保存」）。
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SystemOverviewCard } from './SystemOverviewCard';
import { EssentialsPanel } from './EssentialsPanel';
import { ControllerPromptPanel } from './ControllerPromptPanel';
import type { ControllerSpec, EssentialItem, SystemOverview } from '../../api/templates';

const overview: SystemOverview = {
  template_id: 'writing-pipeline',
  name: '写作流水线',
  scenario: 'writing',
  scenario_label: '写作流水线',
  layer: 'novice_default',
  quality_tier: 'novice',
  controller: { id: 'controller', role: '总控', duties: ['任务分配', '调度跟进'] },
  member_count: 2,
  members: [
    { id: 'topic', role: '选题策划', responsibilities: ['产出候选选题'], tools: ['web.search'] },
    { id: 'drafter', role: '正文写作', responsibilities: ['按大纲写初稿'], tools: [] },
  ],
  artifact_rule: { dir: 'artifacts/{task_id}/writing/', pattern: '{seq:02d}-{member}-{slug}.md' },
  estimate: {
    estimated: true,
    basis: '成员数 × 示例任务步数 × 每步保守 token 中位',
    steps: 5,
    member_count: 2,
    approx_model_calls: 5,
    approx_tokens: 9000,
    cost_note: '实际费用以运行时账单为准（A-成本仪表盘-01）',
  },
};

const essentials: EssentialItem[] = [
  {
    key: 'budget',
    label: '用量预算',
    value: { max_model_calls: 24, warn_at_percent: 80 },
    explain: '出厂给的保守预算；80% 预警、95% 强制暂停。',
    overridable: true,
    factory_value: { max_model_calls: 24, warn_at_percent: 80 },
  },
  {
    key: 'termination',
    label: '终止条件',
    value: '排版稿通过收口校验即终止',
    explain: '明确终止条件防止总控无限自我循环。',
    overridable: true,
    factory_value: '排版稿通过收口校验即终止',
  },
];

const controller: ControllerSpec = {
  id: 'controller',
  role: '总控',
  duties: ['任务分配', '路由划分', '调度跟进', '信息同步', '状态更新'],
  forbidden_rules: ['不得直接执行具体任务'],
  system_prompt: '你是总控。你不得直接执行具体任务。',
};

describe('SystemOverviewCard（需求 -07①）', () => {
  it('一眼可见成员 / 职责 / 工具 / 产出位置', () => {
    render(<SystemOverviewCard overview={overview} />);
    expect(screen.getByText('写作流水线')).toBeInTheDocument();
    expect(screen.getByText('2 个成员 + 总控')).toBeInTheDocument();
    expect(screen.getByText('产出候选选题')).toBeInTheDocument();
    expect(screen.getByText('web.search')).toBeInTheDocument();
    expect(screen.getByText('artifacts/{task_id}/writing/')).toBeInTheDocument();
    expect(screen.getByText('{seq:02d}-{member}-{slug}.md')).toBeInTheDocument();
    // 没有白名单的成员要如实说「无」，不静默留空
    expect(screen.getByText('无（默认本地）')).toBeInTheDocument();
  });

  it('用量标注为预估，并指向真实账单口径', () => {
    render(<SystemOverviewCard overview={overview} />);
    expect(screen.getByText('预估')).toBeInTheDocument();
    expect(screen.getByText(/实际费用以运行时账单为准/)).toBeInTheDocument();
  });
});

describe('EssentialsPanel（需求 -03②③）', () => {
  it('每项都带可展开的说明', () => {
    render(
      <EssentialsPanel items={essentials} onOverride={vi.fn()} onRestoreFactory={vi.fn()} />,
    );
    expect(screen.getByText('用量预算')).toBeInTheDocument();
    // <details> 的说明文本在 DOM 里（收起也可被读屏/搜索命中）
    expect(screen.getByText('出厂给的保守预算；80% 预警、95% 强制暂停。')).toBeInTheDocument();
    expect(screen.getByText('明确终止条件防止总控无限自我循环。')).toBeInTheDocument();
  });

  it('逐项覆盖回传的是解析后的值，不是整条必备项', async () => {
    const onOverride = vi.fn();
    render(
      <EssentialsPanel items={essentials} onOverride={onOverride} onRestoreFactory={vi.fn()} />,
    );
    const item = screen.getByText('用量预算').closest('li') as HTMLElement;
    await userEvent.click(within(item).getByRole('button', { name: /覆盖此项/ }));
    const box = within(item).getByLabelText('覆盖 用量预算');
    await userEvent.clear(box);
    await userEvent.type(box, '{{"max_model_calls": 10}');
    await userEvent.click(within(item).getByRole('button', { name: '应用覆盖' }));
    expect(onOverride).toHaveBeenCalledWith('budget', { max_model_calls: 10 });
  });

  it('恢复此项出厂值回传出厂值本身', async () => {
    const onOverride = vi.fn();
    render(
      <EssentialsPanel items={essentials} onOverride={onOverride} onRestoreFactory={vi.fn()} />,
    );
    const item = screen.getByText('终止条件').closest('li') as HTMLElement;
    await userEvent.click(within(item).getByRole('button', { name: /覆盖此项/ }));
    await userEvent.click(within(item).getByRole('button', { name: '恢复此项出厂值' }));
    expect(onOverride).toHaveBeenCalledWith('termination', '排版稿通过收口校验即终止');
  });

  it('恢复出厂值是一次整体动作', async () => {
    const onRestoreFactory = vi.fn();
    render(
      <EssentialsPanel items={essentials} onOverride={vi.fn()} onRestoreFactory={onRestoreFactory} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /恢复出厂值/ }));
    expect(onRestoreFactory).toHaveBeenCalledTimes(1);
  });
});

describe('ControllerPromptPanel（需求 -02②③④）', () => {
  function renderPanel(over: Partial<Parameters<typeof ControllerPromptPanel>[0]> = {}) {
    return render(
      <ControllerPromptPanel
        controller={controller}
        draft={controller.system_prompt}
        warnings={[]}
        touched={false}
        onDraftChange={vi.fn()}
        onCheck={vi.fn()}
        onRestore={vi.fn()}
        {...over}
      />,
    );
  }

  it('边界通过时给出明确的正向结论', () => {
    renderPanel();
    expect(screen.getByText(/边界检查通过/)).toBeInTheDocument();
  });

  it('改坏时给警告但明确「不阻断保存」', () => {
    renderPanel({ warnings: ['controller_scope_risk: 提示词出现「你自己写代码」'] });
    const alert = screen.getByRole('alert');
    expect(within(alert).getByText(/不会阻断保存/)).toBeInTheDocument();
    expect(within(alert).getByText(/controller_scope_risk/)).toBeInTheDocument();
  });

  it('提示词可编辑且可一键恢复出厂', async () => {
    const onDraftChange = vi.fn();
    const onRestore = vi.fn();
    renderPanel({ onDraftChange, onRestore });
    const box = screen.getByLabelText('总控系统提示词');
    expect(box).toHaveValue(controller.system_prompt);
    await userEvent.type(box, '！');
    expect(onDraftChange).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /恢复出厂总控提示词/ }));
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it('有未保存修改时给出可见提示', () => {
    renderPanel({ draft: '改过了', touched: true });
    expect(screen.getByText(/有未保存的修改/)).toBeInTheDocument();
  });
});
