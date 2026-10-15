import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ModelOption, TeamMemberView } from '../../api/teams';
import { MemberDrawer } from './MemberDrawer';

vi.mock('../../hooks/useAutosave', () => ({ useBase: () => {} }));

const BASE_MEMBER = {
  role: 'implementer', title: '编码专家', agent_host: 'find_yourself',
  provider_id: '', session_id: 'team-t1-implementer-abc',
  independent_session: true, state: 'idle', blocked_reason: '',
  requested_model: '', effective_model: '', effective_confidence: 'not_executed',
  inherited_from: 'team', credential_ref: '', credential_configured: false,
  depends_on: [], run_batch: 0, current_goal: '', plan_version: 1,
  subtask_id: '', budget_reserved_usd: 0, budget_spent_usd: 0, version: 1,
  control: { disabled_operations: [], supports_per_member_model: true },
  capability_recorded: false, updated_at: null,
} as unknown as TeamMemberView;

function renderDrawer(models: ModelOption[]) {
  return render(
    <MemberDrawer
      member={BASE_MEMBER}
      binding={null}
      host={{ agent_host: 'find_yourself', supports_per_member_model: true } as never}
      models={models}
      events={[]}
      busy={false}
      onClose={() => {}}
      onApplyBinding={() => {}}
      onSwitchModel={() => {}}
      onControl={() => {}}
    />,
  );
}

describe('MemberDrawer 空模型目录', () => {
  it('无可选模型时明示去设置页接入供应商（不是抽屉坏了）', () => {
    renderDrawer([]);
    expect(screen.getByTestId('cv-node-model-empty').textContent).toContain('设置页');
  });

  it('有模型时渲染下拉框', () => {
    renderDrawer([{
      provider_id: 'local-synthetic', model_id: 'm-1',
      credential_configured: true, credential_ref: 'env:X',
      pricing_status: 'known', context_window: 16384, synthetic: false,
      supports_tools: false, supports_structured_output: true, supports_streaming: false,
      max_output_tokens: 4096, endpoint_ref: 'inprocess://m-1', note: '',
    }]);
    expect(screen.queryByTestId('cv-node-model-empty')).not.toBeInTheDocument();
    expect(screen.getByLabelText('请求模型（节点覆盖）')).toBeInTheDocument();
  });

  it('草稿态暂停/返工/取消禁用并说明原因（不让用户撞后端报错）', () => {
    render(
      <MemberDrawer
        member={{ ...BASE_MEMBER, state: 'draft' }}
        binding={null}
        host={{ agent_host: 'find_yourself', supports_per_member_model: true } as never}
        models={[]}
        events={[]}
        busy={false}
        onClose={() => {}}
        onApplyBinding={() => {}}
        onSwitchModel={() => {}}
        onControl={() => {}}
      />,
    );
    expect(screen.getByTestId('cv-control-draft-hint')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '暂停' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '要求返工' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  });
});
