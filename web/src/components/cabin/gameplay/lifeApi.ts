/**
 * B 包（系统与内容）· 前端数据消费层。
 *
 * 与 `src/find_yourself/services/cabin_life/*`（后端纯逻辑）**一一对应**：
 * 后端是规则的唯一真源，本文件只做「类型镜像 + 展示派生」，**不重算任何规则**。
 *
 * 诚实原则（对齐后端）：
 *   - 天气/时间/售价/销量都由后端算好后下发，前端**只显示**；
 *   - 后端没给的字段显示为「未知 / —」，不补好看的默认值；
 *   - 「材料不足 / 金币不足 / 未解锁」必须带原因文案，不能静默灰显。
 *
 * 为什么不直接 import 后端 Python：前端构建链不含 Python，且规则必须单源。
 * `lifeApi.test.ts` 用真实快照 JSON 做契约测试，两端字段对不上就红。
 */
import { request, ApiError, NetworkError } from '../../../api/client';

export { ApiError, NetworkError };
/** 与 `cabin_life.themes.THEME_IDS` 对齐（5 背景 + 四大主题）。 */
export const THEME_IDS = [
  'forest',
  'garden',
  'stream',
  'field',
  'planet',
  'magic',
  'scifi',
  'country',
  'ink',
] as const;

export type ThemeId = (typeof THEME_IDS)[number];

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value);
}

export const THEME_LABELS: Record<ThemeId, string> = {
  forest: '老林子',
  garden: '后花园',
  stream: '溪水边',
  field: '金黄田野',
  planet: '观星台',
  magic: '魔法大陆',
  scifi: '科幻星球',
  country: '田园乡村',
  ink: '古风桃源',
};

/** 昼夜时段（与后端 `DAY_PARTS` 同名同序）。 */
export type DayPart =
  | 'dawn'
  | 'morning'
  | 'noon'
  | 'afternoon'
  | 'dusk'
  | 'night'
  | 'late_night';

export interface WeatherBlock {
  id: string;
  label: string;
  icon: string;
  /** 色温 0..1（A5 光影消费；前端可用于 HUD 背光） */
  light: number;
  yield_pct: number;
  pace_pct: number;
  demand_pct: number;
  blocks_gather: boolean;
}

export interface ClockBlock {
  day: number;
  minute: number;
  part: DayPart;
  part_label: string;
}

export interface QuestEntryView {
  quest_id: string;
  progress: number;
  done: boolean;
  claimed: boolean;
}

export interface LifeSave {
  owner: string;
  theme: ThemeId;
  clock: ClockBlock;
  weather: WeatherBlock;
  bag: Record<string, number>;
  coins: number;
  skill_exp: number;
  /** npc_id -> 好感点数（0..1000，10 心封顶） */
  affinity: Record<string, number>;
  /** npc_id -> 今日已送礼次数 */
  gifts_today: Record<string, number>;
  shop: { kind: string; level: number; stock: Record<string, number>; ask_prices: Record<string, number> };
  quest_log: { day: number; entries: QuestEntryView[] };
  gather_counts: Record<string, number>;
  version: number;
}

/** NPC 一览行（后端 `cabin_life.state.npc_rows` 输出）。 */
export interface NpcRow {
  id: string;
  name: string;
  role: string;
  place: string;
  activity: string;
  awake: boolean;
  hearts: number;
  hearts_display: string;
  /** '!' 有可接 / '?' 可交付 / '·' 进行中 / '' 无 */
  marker: string;
}

/** 经营一览行（后端 `cabin_life.shop.shop_rows` 输出）。 */
export interface ShopRow {
  id: string;
  label: string;
  unlocked: boolean;
  buy_price: number;
  fair_price: number;
  ask_price: number;
  stock: number;
  taste: number;
}

/** 制作台行（后端 `cabin_life.crafting.unlock_state` 输出）。 */
export interface CraftRow {
  id: string;
  label: string;
  category: string;
  unlocked: boolean;
  required_level: number;
  current_level: number;
  need_level: number | null;
}

/** 采集点行（后端 `cabin_life.interaction.gatherable_rows` 输出）。 */
export interface GatherRow {
  id: string;
  label: string;
  action: string;
  action_label: string;
  animation: string;
  material: string;
  qty_range: number[];
  minutes: number;
  tile: number[];
  distance: number | null;
  in_range: boolean;
}

export const MAX_HEARTS = 10;

/** 点数 → 心数（与后端 `npcs.hearts_for_points` 同公式，每 100 点 1 心）。 */
export function heartsForPoints(points: number): number {
  if (!Number.isFinite(points) || points < 0) return 0;
  return Math.min(MAX_HEARTS, Math.floor(points / 100));
}

/** 心数 → 心形字符串（满心不额外加符号）。 */
export function heartsDisplay(points: number): string {
  const h = heartsForPoints(points);
  return '♥'.repeat(h) + '♡'.repeat(MAX_HEARTS - h);
}

/** 头顶标记语义（供 aria-label / title 使用，UI 不能只靠颜色区分）。 */
export const MARKER_MEANING: Record<string, string> = {
  '!': '有可接的委托',
  '?': '有可以交付的任务',
  '·': '任务进行中',
  '': '',
};

/** 分钟 → HH:MM（补零；负数不猜测，按未知处理）。 */
export function formatMinute(minute: number): string {
  if (!Number.isFinite(minute) || minute < 0) return '--:--';
  const h = Math.floor(minute / 60) % 24;
  const m = Math.floor(minute % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** 顶部状态栏文案：`第3天 上午 晴 · 120 金币`。 */
export function hudLine(save: LifeSave): string {
  if (!save.clock || !save.weather) return '状态未知';
  return `第${save.clock.day}天 ${save.clock.part_label} ${save.weather.label} · ${save.coins} 金币`;
}

/** 未解锁行的原因文案（不静默灰显）。 */
export function craftLockReason(row: CraftRow): string {
  if (row.unlocked) return '已解锁';
  return `需要技能 Lv${row.need_level ?? row.required_level}（当前 Lv${row.current_level}）`;
}

/** 采集提示：`✋ 收割 农田（wheat）`；不在范围内返回 null（UI 不显示提示）。 */
export function gatherPrompt(row: GatherRow | null | undefined): string | null {
  if (!row || !row.in_range) return null;
  return `✋ ${row.action_label} ${row.label}（${row.material}）`;
}

/** 背包条目按数量降序（同量按 id 排序 → 渲染稳定，不跳动）。 */
export function sortedBag(bag: Record<string, number>): Array<{ id: string; qty: number }> {
  return Object.entries(bag ?? {})
    .filter(([, qty]) => Number.isFinite(qty) && qty > 0)
    .map(([id, qty]) => ({ id, qty }))
    .sort((a, b) => b.qty - a.qty || a.id.localeCompare(b.id));
}

/** 日结算回执行：把「没卖掉的」也如实展示出来。 */
export interface SettlementLine {
  good_id: string;
  label: string;
  unit_price: number;
  sold: number;
  revenue: number;
  leftover: number;
}

export interface Settlement {
  day: number;
  weather: string;
  theme: string;
  lines: SettlementLine[];
  total_revenue: number;
  cost_of_goods_sold: number;
  profit: number;
  coins_after: number;
  next_day: number;
}

/** 校验日结算是否自洽（前端展示前先自查，对不上就显示「结算异常」而不是照单全收）。 */
export function settlementConsistent(s: Settlement): boolean {
  const sum = s.lines.reduce((acc, l) => acc + l.revenue, 0);
  if (sum !== s.total_revenue) return false;
  if (s.profit !== s.total_revenue - s.cost_of_goods_sold) return false;
  if (s.coins_after === undefined) return false;
  return s.lines.every((l) => l.revenue === l.sold * l.unit_price);
}

export function settlementSummary(s: Settlement): string {
  if (!settlementConsistent(s)) return '结算异常：数值对不上，请反馈（本次结果不可信）';
  const left = s.lines.reduce((acc, l) => acc + l.leftover, 0);
  const base = `第${s.day}天 收入 ${s.total_revenue} / 成本 ${s.cost_of_goods_sold} / 利润 ${s.profit}`;
  return left > 0 ? `${base}（另有 ${left} 件没卖掉，留在仓库）` : base;
}

/* ------------------------------------------------------------------ */
/* HTTP 客户端通道（/api/cabin/life/* 六端点契约）                      */
/* ------------------------------------------------------------------ */

/** 完整快照：包含存档、HUD文案以及四大面板数据 */
export interface LifeSnapshot {
  save: LifeSave;
  hud_line: string;
  npcs: NpcRow[];
  shop: ShopRow[];
  craft: CraftRow[];
  gather: GatherRow[];
  version: number;
}

export interface LifeMeta {
  themes: Array<{ id: string; label: string }>;
  actions: string[];
  writable_settings: string[];
  max_hearts: number;
  max_gifts_per_day: number;
  daily_node_limit: number;
  daily_reset_minute: number;
}

export interface LifeActionPayload {
  action: string;
  node_id?: string;
  recipe_id?: string;
  good_id?: string;
  npc_id?: string;
  gift_id?: string;
  quest_id?: string;
  giver?: string;
  price?: number;
  qty?: number;
  [key: string]: unknown;
}

export interface LifeActionResult {
  action: string;
  save: LifeSave;
  hud_line: string;
  receipt?: unknown;
  settlement?: Settlement;
  [key: string]: unknown;
}

export interface CreateSavePayload {
  theme: string;
  day?: number;
}

export interface PutSettingsPayload {
  settings?: Record<string, unknown>;
  expected_version?: number;
  [key: string]: unknown;
}

/** 识别 409 / 412 乐观锁冲突或版本冲突 */
export function isConflictError(err: unknown): boolean {
  if (err instanceof ApiError) {
    return err.status === 409 || err.status === 412 || err.body?.code === 'life_version_conflict';
  }
  return false;
}

/** 读取完整面板快照 (GET /api/cabin/life/save) */
export async function fetchSnapshot(
  playerTile?: [number, number] | null,
  signal?: AbortSignal,
): Promise<LifeSnapshot> {
  const query: Record<string, number | undefined> = {};
  if (playerTile) {
    query.player_x = playerTile[0];
    query.player_y = playerTile[1];
  }
  return request<LifeSnapshot>('/api/cabin/life/save', {
    method: 'GET',
    query,
    signal,
  });
}

/** 建新档 (POST /api/cabin/life/save) */
export async function createSave(
  themeOrPayload: string | CreateSavePayload,
  day?: number,
  signal?: AbortSignal,
): Promise<{ save: LifeSave; hud_line: string }> {
  const body = typeof themeOrPayload === 'string'
    ? { theme: themeOrPayload, day: day ?? 1 }
    : { theme: themeOrPayload.theme, day: themeOrPayload.day ?? 1 };
  return request<{ save: LifeSave; hud_line: string }>('/api/cabin/life/save', {
    method: 'POST',
    body,
    signal,
  });
}

/** 保存玩家偏好设置 (PUT /api/cabin/life/save) */
export async function save(
  payload: PutSettingsPayload,
  signal?: AbortSignal,
): Promise<{ save: LifeSave; hud_line: string }> {
  return request<{ save: LifeSave; hud_line: string }>('/api/cabin/life/save', {
    method: 'PUT',
    body: payload,
    signal,
  });
}

/** 删档 (DELETE /api/cabin/life/save) */
export async function deleteSave(
  signal?: AbortSignal,
): Promise<{ deleted: boolean; owner: string }> {
  return request<{ deleted: boolean; owner: string }>('/api/cabin/life/save', {
    method: 'DELETE',
    signal,
  });
}

/** 服务端权威动作通道 (POST /api/cabin/life/action) */
export async function act(
  actionOrPayload: string | LifeActionPayload,
  args?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<LifeActionResult> {
  const body: LifeActionPayload = typeof actionOrPayload === 'string'
    ? { action: actionOrPayload, ...(args ?? {}) }
    : actionOrPayload;
  return request<LifeActionResult>('/api/cabin/life/action', {
    method: 'POST',
    body,
    signal,
  });
}

/** 元数据查询 (GET /api/cabin/life/meta) */
export async function meta(signal?: AbortSignal): Promise<LifeMeta> {
  return request<LifeMeta>('/api/cabin/life/meta', {
    method: 'GET',
    signal,
  });
}

/** lifeApi 客户端聚合对象 */
export const lifeApi = {
  fetchSnapshot,
  createSave,
  save,
  deleteSave,
  act,
  meta,
  isConflictError,
};