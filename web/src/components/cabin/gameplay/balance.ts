/**
 * W2 · 玩法数值与展示格式化（纯函数层，无副作用、无随机、无网络）
 *
 * 设计意图：把「数值怎么显示」「解锁了没」「倒计时怎么写」这些**可确定性验证**
 * 的规则从 React 组件里抽出来单测。前端不做任何权威判定——所有数值都来自
 * `GET /api/cabin/save`，这里只负责渲染与本地派生展示（例如家具解锁态）。
 *
 * 诚实原则：
 *   - 服务端没给的字段一律显示为「未知 / —」，绝不凭空补一个好看的假值；
 *   - 未解锁的家具必须带原因（还没做任务 / 图纸没凑齐），不静默灰显；
 *   - 随机事件若为「一无所获」，如实展示，不美化文案。
 *
 * 契约真源：`services/cabin_gameplay.py`（数值表）与
 * `components/cabin/interior/furnitureCatalog.ts`（家具解锁字段）。
 */

/* ------------------------------------------------------------------ */
/* 常量（与后端 LOCAL_TZ / 上限保持一致，用于前端护栏与展示）           */
/* ------------------------------------------------------------------ */

/** 后端 THEME 白名单顺序即页签顺序（服务端权威，前端不自行排序）。 */
export const THEME_ORDER = ['forest', 'garden', 'stream', 'field', 'planet'] as const;
export type ThemeId = (typeof THEME_ORDER)[number];

export const THEME_LABELS: Record<ThemeId, string> = {
  forest: '老林子',
  garden: '后花园',
  stream: '溪水边',
  field: '金黄田野',
  planet: '观星台',
};

export const MAX_MATERIAL_QTY = 99;
export const MAX_INTIMACY = 100;
export const MAX_HOUSE_LEVEL = 5;

/** 服务端每日重置按 Asia/Shanghai (+08:00) 固定偏移计算。 */
export const DAILY_RESET_OFFSET_HOURS = 8;

/* ------------------------------------------------------------------ */
/* 基础类型                                                            */
/* ------------------------------------------------------------------ */

export interface MaterialInfo {
  id: string;
  label: string;
  theme: ThemeId;
  tier: number;
}

export interface SpotStatus {
  id: string;
  theme: ThemeId;
  label: string;
  material: string;
  material_label: string;
  qty: number;
  coins: number;
  intimacy: number;
  cooldown_hours: number;
  fx: number;
  fy: number;
  available: boolean;
  ready_at: string | null;
  remaining_seconds: number;
  collect_count: number;
  dust: boolean;
}

export interface QuestView {
  id: string;
  title: string;
  /** 进度记账的事件名（explore/feed/decorate/level…），来自服务端 meta。 */
  event: string;
  target: number;
  reward_label: string;
  desc?: string;
}

export interface TutorialProgress {
  step: number;
  progress: Record<string, number>;
  claimed: string[];
  completed: boolean;
  baseline_items: number | null;
}

export interface DailyView {
  date: string;
  ids: string[];
  progress: Record<string, number>;
  claimed: string[];
}

export interface WishView {
  id: string;
  kind: 'material' | 'craft';
  target: string;
  need: number;
  text: string;
  reward_label: string;
  progress: number;
  done: boolean;
  claimed: boolean;
}

export interface CompanionView {
  behavior: string;
  label: string;
  away_hours: number;
  trinkets: string[];
  unlocked_count: number;
}

export interface OfflineView {
  away_hours: number;
  real_away_hours: number;
  capped: boolean;
  refreshed_spots: string[];
  text: string;
}

export interface PreferencesView {
  personality: string;
  /** 服务端是否真的识别到传入的 personality；false 时 UI 不得声称「命中喜好」。 */
  recognized: boolean;
  person: { food: string; food_label: string; interaction: string; touch: string; note: string };
  pet: { food: string; food_label: string; interaction: string; touch: string; note: string };
}

export interface CabinSaveView {
  owner_scoped: boolean;
  version: number;
  coins: number;
  intimacy: number;
  house_level: number;
  max_house_level: number;
  materials: Record<string, number>;
  material_catalog: MaterialInfo[];
  spots: SpotStatus[];
  quests: { tutorial: TutorialProgress; daily: DailyView; wish: WishView | null };
  companion: CompanionView;
  unlocked_furniture: string[];
  chest_keys: number;
  login_streak: number;
  dust: string[];
  offline: OfflineView | null;
  daily_rotated: boolean;
  level_up: { from: number; to: number } | null;
  preferences: PreferencesView;
  server_time: string;
  local_date: string;
  settings: Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/* 格式化                                                              */
/* ------------------------------------------------------------------ */

/** 千分位；非有限数返回 '—'（诚实：不知道就说不知道）。 */
export function formatNumber(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return value.toLocaleString('zh-CN');
}

/**
 * 冷却倒计时：秒 → 「2 小时 5 分」/「12 分 30 秒」。
 * remaining<=0 视为可采集，返回 null（调用方显示「可采集」）。
 */
export function formatCooldown(remainingSeconds: number): string | null {
  if (typeof remainingSeconds !== 'number' || !Number.isFinite(remainingSeconds)) return null;
  const total = Math.max(0, Math.floor(remainingSeconds));
  if (total <= 0) return null;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

/** 亲密度 → 5 颗心（整数分档，不显示小数）。 */
export function intimacyHearts(intimacy: number): string {
  if (typeof intimacy !== 'number' || !Number.isFinite(intimacy)) return '';
  const clamped = Math.max(0, Math.min(MAX_INTIMACY, intimacy));
  return '♥'.repeat(Math.round((clamped / MAX_INTIMACY) * 5));
}

/** 亲密度分档文案（前端只做展示，分档阈值随服务端返回，不自行判定加成）。 */
export function intimacyTierLabel(intimacy: number): string {
  if (typeof intimacy !== 'number' || !Number.isFinite(intimacy)) return '未知';
  if (intimacy >= 80) return '亲密无间';
  if (intimacy >= 50) return '很亲近';
  if (intimacy >= 20) return '熟悉';
  return '还在观察';
}

/** 距离下一个小屋等级还差几件家具（对齐 W1 deriveCabinLevel 阈值 3/7/11/16）。 */
export const LEVEL_THRESHOLDS = [3, 7, 11, 16] as const;

export function nextLevelProgress(
  furnitureCount: number,
  houseLevel: number,
): { nextLevel: number | null; need: number; have: number } {
  const safeLevel = Number.isFinite(houseLevel) ? houseLevel : 1;
  if (safeLevel >= MAX_HOUSE_LEVEL) {
    return { nextLevel: null, need: 0, have: 0 };
  }
  const nextLevel = safeLevel + 1;
  const need = LEVEL_THRESHOLDS[nextLevel - 2] ?? LEVEL_THRESHOLDS[LEVEL_THRESHOLDS.length - 1]!;
  return { nextLevel, need, have: Math.max(0, furnitureCount) };
}

/** 背包：按服务端 catalog 顺序列出，qty<=0 的材料折叠掉。 */
export function visibleMaterials(
  save: Pick<CabinSaveView, 'materials' | 'material_catalog'>,
): { id: string; label: string; theme: ThemeId; qty: number; capped: boolean }[] {
  const owned = save.materials ?? {};
  return (save.material_catalog ?? [])
    .map((m) => ({
      id: m.id,
      label: m.label,
      theme: m.theme,
      qty: Number(owned[m.id] ?? 0),
      capped: Number(owned[m.id] ?? 0) >= MAX_MATERIAL_QTY,
    }))
    .filter((m) => m.qty > 0);
}

/** 按主题分组背包，供背包面板分区展示（DNA-9：不串戏）。 */
export function groupMaterialsByTheme<
  T extends { theme: ThemeId; qty: number },
>(items: readonly T[]): { theme: ThemeId; label: string; items: T[] }[] {
  return THEME_ORDER.map((theme) => ({
    theme,
    label: THEME_LABELS[theme],
    items: items.filter((i) => i.theme === theme && i.qty > 0),
  })).filter((g) => g.items.length > 0);
}

/* ------------------------------------------------------------------ */
/* 家具解锁（复用 W1 furnitureCatalog 的 unlockedBy 契约）              */
/* ------------------------------------------------------------------ */

export type UnlockSource = 'default' | 'level' | 'quest' | 'craft';

export interface FurnitureUnlockRule {
  id: string;
  label: string;
  unlockedBy: UnlockSource;
  unlockLevel: number;
  themes: readonly string[];
}

export type UnlockState =
  | { unlocked: true; reason: null }
  | { unlocked: false; reason: string; hint: string };

/**
 * 判断一件家具当前是否解锁。
 *
 * 判定顺序刻意与真源一致：`level` 看小屋等级，`quest`/`craft` 看服务端
 * 下发的 `unlocked_furniture` 白名单（**服务端权威**，前端不自行推演
 * 「做完任务就一定解锁」——那是伪造成功）。
 *
 * @param unlockedFurniture 服务端存档里的已解锁 id 列表
 * @param blueprintsCost   图纸材料需求（用于给 craft 家具补「还差什么」提示）
 */
export function resolveUnlock(
  rule: FurnitureUnlockRule,
  ctx: { houseLevel: number; unlockedFurniture: readonly string[]; hasMaterials?: (id: string) => number },
): UnlockState {
  const unlockedSet = new Set(ctx.unlockedFurniture ?? []);
  switch (rule.unlockedBy) {
    case 'default':
      return { unlocked: true, reason: null };
    case 'level': {
      const need = rule.unlockLevel;
      if (ctx.houseLevel >= need) return { unlocked: true, reason: null };
      return {
        unlocked: false,
        reason: `需要小屋 Lv${need}`,
        hint: `当前 Lv${ctx.houseLevel}，再摆 ${Math.max(0, need)} 件家具升级`,
      };
    }
    case 'quest':
    case 'craft':
    default: {
      if (unlockedSet.has(rule.id)) return { unlocked: true, reason: null };
      const kindLabel = rule.unlockedBy === 'quest' ? '任务' : '图纸';
      const missing = blueprintMissing(rule.id, ctx.hasMaterials);
      return {
        unlocked: false,
        reason: `尚未解锁（${kindLabel}）`,
        hint: missing ?? `完成相关${kindLabel}后由服务端解锁`,
      };
    }
  }
}

/** 图纸缺料提示（诚实：只说还差什么，不假装能造）。 */
const BLUEPRINT_COSTS: Record<string, Record<string, number>> = {
  herb_shelf: { wood: 3, flower: 2, moss: 1 },
  snow_lamp: { crystal: 2, pebble: 2, stardust: 1 },
  crystal_tree: { crystal: 3, stardust: 3, moonstone: 1 },
};

export function blueprintCost(id: string): Record<string, number> {
  return BLUEPRINT_COSTS[id] ?? {};
}

/** 返回「还差：木头×2 星尘×1」这样的提示；已够料返回 null。 */
export function blueprintMissing(
  id: string,
  hasMaterials?: (id: string) => number,
): string | null {
  const cost = blueprintCost(id);
  const keys = Object.keys(cost);
  if (keys.length === 0) return null;
  if (!hasMaterials) return '在制造面板查看所需材料';
  const missing = keys
    .map((k) => ({ k, lack: cost[k]! - (hasMaterials(k) ?? 0) }))
    .filter((x) => x.lack > 0)
    .map((x) => `${x.k}×${x.lack}`);
  return missing.length > 0 ? `还差 ${missing.join(' ')}` : '材料已齐，可在制造面板打造';
}

/* ------------------------------------------------------------------ */
/* 任务展示                                                            */
/* ------------------------------------------------------------------ */

/** 当前该展示新手链第几步（step 从 0 起；完成返回 null）。 */
export function currentTutorialStepIndex(t: TutorialProgress | null | undefined): number | null {
  if (!t || t.completed) return null;
  const idx = Number.isFinite(t.step) ? t.step : 0;
  if (idx < 0) return null;
  return idx;
}

/** 日常任务合并展示：把 spec（来自 meta）与服务端进度对齐。 */
export function mergeDailyQuests(
  daily: DailyView | null | undefined,
  pool: readonly QuestView[] | undefined,
): (QuestView & { progress: number; claimed: boolean; ready: boolean })[] {
  if (!daily) return [];
  const byId = new Map((pool ?? []).map((q) => [q.id, q]));
  return (daily.ids ?? [])
    .map((id) => byId.get(id))
    .filter((q): q is QuestView => Boolean(q))
    .map((q) => {
      const progress = Number(daily.progress?.[q.id] ?? 0);
      const claimed = (daily.claimed ?? []).includes(q.id);
      return { ...q, progress, claimed, ready: !claimed && progress >= q.target };
    });
}

/** 探索点按服务端给的 fx/fy 排序（后端已定视觉位，前端不重排）。 */
export function sortSpotsForDisplay(spots: readonly SpotStatus[]): SpotStatus[] {
  return [...(spots ?? [])].sort((a, b) => (a.fy - b.fy) || (a.fx - b.fx));
}

/** 材料 → 产出该材料的探险点（制造面板提示「去哪儿采」）。 */
export function spotsForMaterial(
  spots: readonly SpotStatus[],
  materialId: string,
): SpotStatus[] {
  return (spots ?? []).filter((s) => s.material === materialId);
}

/* ------------------------------------------------------------------ */
/* 事件展示                                                            */
/* ------------------------------------------------------------------ */

export interface EventView {
  id: string;
  title?: string;
  text: string;
  feedback?: string;
  material?: string | null;
  items?: Record<string, number> | null;
  coins?: number | null;
  intimacy?: number | null;
}

/** 「一无所获」是诚实状态：必须原样告诉玩家，不能替换成好听的假文案。 */
export function isNothingEvent(ev: EventView | null | undefined): boolean {
  if (!ev) return false;
  return ev.id === 'nothing';
}

/** 把事件收益渲染成「木耳×2 · 12 金币」；无收益返回 null（不编造）。 */
export function formatEventReward(ev: EventView | null | undefined): string | null {
  if (!ev || isNothingEvent(ev)) return null;
  const parts: string[] = [];
  const items = ev.items ?? {};
  for (const [k, v] of Object.entries(items)) {
    if (typeof v === 'number' && v > 0) parts.push(`${k}×${v}`);
  }
  if (typeof ev.coins === 'number' && ev.coins !== 0) {
    parts.push(`${ev.coins > 0 ? '' : ''}${formatNumber(Math.abs(ev.coins))} 金币`);
  }
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** 好感提示文案：仅在服务端确认 recognized 时才说「命中喜好」。 */
export function preferenceHint(prefs: PreferencesView | null | undefined, who: 'person' | 'pet'): string {
  if (!prefs) return '喜好未知（服务端未返回）';
  const target = who === 'person' ? prefs.person : prefs.pet;
  if (!prefs.recognized) {
    return `${target.food_label}似乎很合他胃口（性格未识别，按默认喜好显示）`;
  }
  return `他喜欢${target.food_label}：${target.note}`;
}
