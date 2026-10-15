import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { TeamMemberView, TeamSnapshot } from '../../api/teams';
import { TeamCanvasGraph } from './TeamCanvasGraph';

vi.mock('../../hooks/useAutosave', () => ({ useBase: () => {} }));

function member(over: Partial<TeamMemberView>): TeamMemberView {
  return {
    agent_instance_id: `agt-${over.role}`,
    role: 'implementer', title: '编码专家', agent_host: 'find_yourself',
    provider_id: 'local-synthetic', session_id: 'team-t1-implementer-abc',
    independent_session: true, state: 'running', blocked_reason: '',
    requested_model: 'mock-deterministic', effective_model: '未执行',
    effective_confidence: 'not_executed', inherited_from: 'team',
    credential_ref: '', credential_configured: false,
    depends_on: ['coordinator'], run_batch: 1, current_goal: '',
    plan_version: 1, subtask_id: 'sub-1', budget_reserved_usd: 0.01,
    budget_spent_usd: 0, version: 1,
    control: { disabled_operations: [], supports_per_member_model: true },
    capability_recorded: true, updated_at: null,
    ...over,
  } as TeamMemberView;
}

const snapshot = {
  team: {
    id: 'team-t1', name: '工程团队', mode: 'system_managed', state: 'running',
    version: 1, plan_version: 1, root_task_id: 'root', canvas_instance_id: null,
    coordinator_role: 'coordinator', default_binding: {},
    budget_ref: {}, permission_ref: {}, members: [],
    last_change_reason: '', last_changed_by: '', real_model_configured: false,
    started_at: null,
  },
  members: [
    member({ role: 'coordinator', title: '协调者' }),
    member({ role: 'implementer', title: '编码专家' }),
    member({ role: 'reviewer', title: '评审', agent_instance_id: 'agt-reviewer' }),
  ],
  events: [],
  validation: {
    team_id: 'team-t1', version: 1, state: 'running', can_start: true,
    blockers: [], warnings: [], real_model_configured: false,
  },
} as unknown as TeamSnapshot;

describe('TeamCanvasGraph 连线可见', () => {
  it('内层容器有显式尺寸（edge svg 才有盒子），2 成员共 4 条边且 d 非空', () => {
    const { container } = render(
      <TeamCanvasGraph
        snapshot={snapshot}
        memberViews={snapshot.members}
        selectedRole={null}
        onSelectRole={() => {}}
        hoveredRole={null}
        onHoverRole={() => {}}
        onRenameMember={() => {}}
      />,
    );
    // 内层无尺寸 → 绝对定位的 svg 按 0x0 计算，连线全部不可见。
    const inner = container.querySelector('.cv-canvas-inner') as HTMLElement;
    expect(inner).not.toBeNull();
    expect(parseFloat(inner.style.width)).toBeGreaterThan(0);
    expect(parseFloat(inner.style.height)).toBeGreaterThan(0);
    // 协调者→成员、成员→验证：2 成员 = 4 条边。
    const edges = container.querySelectorAll('path.cv-edge');
    expect(edges.length).toBe(4);
    edges.forEach((p) => {
      expect(p.getAttribute('d')?.length ?? 0).toBeGreaterThan(10);
    });
  });

  it('双击节点开重命名框，确认改名回调（取消则不回调）', () => {
    const onRename = vi.fn();
    const { container } = render(
      <TeamCanvasGraph
        snapshot={snapshot}
        memberViews={snapshot.members}
        selectedRole={null}
        onSelectRole={() => {}}
        hoveredRole={null}
        onHoverRole={() => {}}
        onRenameMember={onRename}
      />,
    );
    const node = container.querySelector('[data-cv-node="implementer"]') as HTMLElement;
    expect(node).not.toBeNull();
    fireEvent.doubleClick(node);
    const input = screen.getByLabelText('显示名称') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '新名字' } });
    fireEvent.click(screen.getByText('确认改名'));
    expect(onRename).toHaveBeenCalledWith('implementer', '新名字');
  });

  it('重命名框点取消不回调', () => {
    const onRename = vi.fn();
    const { container } = render(
      <TeamCanvasGraph
        snapshot={snapshot}
        memberViews={snapshot.members}
        selectedRole={null}
        onSelectRole={() => {}}
        hoveredRole={null}
        onHoverRole={() => {}}
        onRenameMember={onRename}
      />,
    );
    fireEvent.doubleClick(container.querySelector('[data-cv-node="reviewer"]') as HTMLElement);
    fireEvent.click(screen.getByText('取消'));
    expect(onRename).not.toHaveBeenCalled();
  });
});
