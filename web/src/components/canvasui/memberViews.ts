/**
 * 包 B · 统一成员视图构造。
 *
 * 后端 snapshot 有两份成员数据：
 * - `snapshot.team.members`（TeamMemberSpec）：静态成员**定义**，draft 状态即存在；
 * - `snapshot.members`（TeamMemberView）：运行时 agent 实例视图，含实时
 *   state / current_goal / updated_at，团队启动后才有实例。
 *
 * 画布与抽屉需要一个在两种状态下都完整的列表：以静态定义为权威角色清单，
 * 用运行时视图按 role 覆盖实时字段；尚无实例的成员合成「草稿」视图。
 */
import type {
  TeamMemberSpec,
  TeamMemberView,
  TeamSnapshot,
} from '../../api/teams';

function draftView(
  spec: TeamMemberSpec,
  team: TeamSnapshot['team'],
): TeamMemberView {
  const binding = (team.default_binding ?? {}) as Record<string, string>;
  return {
    agent_instance_id: '',
    role: spec.role,
    title: spec.title || spec.role,
    agent_host: spec.agent_host || 'find_yourself',
    provider_id: spec.provider_id || binding.provider_id || '',
    session_id: '',
    independent_session: false,
    state: 'draft',
    blocked_reason: '',
    requested_model: spec.model_id || binding.model_id || '',
    effective_model: '未执行',
    effective_confidence: '',
    inherited_from: '',
    credential_ref: '',
    credential_configured: false,
    depends_on: spec.depends_on || [],
    run_batch: 0,
    current_goal: spec.goal || '待命',
    plan_version: team.plan_version,
    subtask_id: null,
    budget_reserved_usd: 0,
    budget_spent_usd: 0,
    version: team.version,
    control: { disabled_operations: [], supports_per_member_model: false },
    capability_recorded: false,
    updated_at: null,
  };
}

/**
 * 合并静态定义与运行时视图，返回画布/抽屉可直接渲染的完整成员列表。
 */
export function buildMemberViews(snapshot: TeamSnapshot): TeamMemberView[] {
  const runtimeByRole = new Map(
    snapshot.members.map((m) => [m.role, m] as const),
  );
  return snapshot.team.members.map(
    (spec) => runtimeByRole.get(spec.role) ?? draftView(spec, snapshot.team),
  );
}
