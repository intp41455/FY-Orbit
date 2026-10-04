import { request } from '../../../api/client';
import type { CabinSaveView, ThemeId } from './balance';

/**
 * W2 · 玩法后端通道（对应 api/routes/cabin_gameplay.py）
 *
 * 服务端权威：产出、随机事件、亲密度、任务进度、离线收益**全部**由后端结算，
 * 前端只负责渲染返回的结算明细。任何写入都必须走 `POST /api/cabin/save/action`
 * （白名单动作），`PUT /api/cabin/save` 仅允许改 settings 偏好类字段。
 *
 * 诚实原则：
 *   - 409（刷新窗口未到 / 乐观锁冲突）与 422（防作弊字段）原样抛出，
 *     由 UI 显示真实原因（如「老松树 refreshes in 7199s」），绝不静默吞掉；
 *   - 网络失败抛 NetworkError，UI 标注「本次操作未生效」，不假装成功。
 */

const BASE = '/api/cabin';

export interface GameplayMeta {
  themes: ThemeId[];
  materials: { id: string; label: string; theme: ThemeId; tier: number }[];
  spots: {
    id: string;
    theme: ThemeId;
    label: string;
    material: string;
    qty: number;
    coins: number;
    intimacy: number;
    cooldown_hours: number;
    fx: number;
    fy: number;
  }[];
  blueprints: Record<string, { label: string; cost: Record<string, number>; unlock_level: number }>;
  events: Record<string, { weight: number; title: string; template: string }>;
  tutorial_steps: {
    id: string; title: string; desc: string; event: string; target: number; reward_label: string;
  }[];
  daily_pool: {
    id: string; title: string; desc: string; event: string; target: number; reward_label: string;
  }[];
  limits: {
    max_material_qty: number;
    max_coins: number;
    max_intimacy: number;
    max_house_level: number;
    offline_cap_hours: number;
    level_thresholds: number[];
  };
  actions: string[];
}

/** 动作白名单（后端 GAMEPLAY_ACTIONS 的前端镜像；后端才是权威）。 */
export type GameplayAction = 'explore' | 'feed' | 'water' | 'clean' | 'claim' | 'craft';

export interface ActionRequest {
  action: GameplayAction;
  spot_id?: string;
  target?: string;
  quest_kind?: 'tutorial' | 'daily' | 'wish';
  quest_id?: string;
  /** 传了才会拿到按性格个性化的随机事件文案（DNA-3 好感度内容引擎）。 */
  person_name?: string;
  personality?: string;
}

/** 动作返回 = 存档快照 + 本次结算明细（rewarded 一律以后端为准）。 */
export type ActionResponse = Partial<CabinSaveView> & {
  action: GameplayAction;
  quest_progress?: Record<string, { id: string; title: string; ready: boolean }>;
  [key: string]: unknown;
};

/** 拉取玩法静态元数据（数值表 / 图纸 / 事件 / 任务，单一真源）。 */
export function fetchGameplayMeta(): Promise<GameplayMeta> {
  return request<GameplayMeta>(`${BASE}/gameplay/meta`);
}

/**
 * 读取存档（服务端权威结算入口）。
 *
 * 这一步会触发：每日重置轮换、离线收益结算、宠物自主行为 roll、小屋等级回填。
 * 所以它**不是纯读**，但仍然幂等（同一天重复读不会重复发奖）。
 */
export function fetchSave(params: {
  theme?: ThemeId;
  personality?: string;
  person_name?: string;
} = {}): Promise<CabinSaveView> {
  const q = new URLSearchParams();
  if (params.theme) q.set('theme', params.theme);
  if (params.personality) q.set('personality', params.personality);
  if (params.person_name) q.set('person_name', params.person_name);
  const qs = q.toString();
  return request<CabinSaveView>(`${BASE}/save${qs ? `?${qs}` : ''}`);
}

/**
 * 写入玩家偏好（settings）。
 *
 * 只允许 settings 白名单字段；试图写 coins / materials / intimacy 等
 * 服务端权威字段会被后端 422 `cabin_server_authoritative` 拒绝——
 * 这是防作弊护栏，**不要**在前端绕过或隐藏该错误。
 */
export function putSave(
  settings: Record<string, unknown>,
  expectedVersion: number,
): Promise<Partial<CabinSaveView>> {
  return request<Partial<CabinSaveView>>(`${BASE}/save`, {
    method: 'PUT',
    body: { settings, expected_version: expectedVersion },
  });
}

/**
 * 执行一次玩法动作（服务端结算）。
 *
 * `person_name` / `personality` 会带上，否则后端只能给通用文案
 * （而不是命中该性格的喜好台词）。
 */
export function postAction(body: ActionRequest): Promise<ActionResponse> {
  return request<ActionResponse>(`${BASE}/save/action`, { method: 'POST', body });
}
