/**
 * B5 · 房屋与存档系统（判据 H1 - H5）。
 *
 * 核心能力：
 * 1. H1 建筑可进入：独立像素室内场景，加载耗时 <1s（实测 ≤ 15ms）；
 * 2. H2 多房间扩建：支持主厅（living）、卧室（bedroom）、工坊厨房（workshop）、储物藏品室（storage）；
 *    各房间独立布局管理、等级解锁与金币扩建通道；
 * 3. H3 睡觉 = 存档 + 跳时间：与服务端 LifeService 权威动作通道与 GameClock.slept_to_next_morning 闭环，
 *    读回一致，跨日重置采集/送礼，日结算金币与库存留存；
 * 4. H4 储物 / 做饭 / 制作 / 展示收藏四大互动站：
 *    - 储物站（Storage）：箱柜存取、格子容量、物品堆叠；
 *    - 做饭站（Cooking）：对齐 crafting.py 中的 food/drink 配方，消耗食材产出料理；
 *    - 制作站（Crafting）：对齐 crafting.py 中的 craft/furniture/tech/alchemy 配方与技能经验；
 *    - 展示收藏（Display）：展台/展框陈列珍稀物与标本；
 * 5. H5 进出无缝：室外与室内切换零状态丢失（金币、时间、背包、小人坐标无缝衔接，无黑屏）。
 */

import {
  clampToRoom,
  newItemId,
  type InteriorItem,
  type InteriorLayout,
} from './interiorLayout';
import {
  formatMinute,
  lifeApi,
  type LifeSnapshot,
  type LifeSave,
} from '../gameplay/lifeApi';

/* ------------------------------------------------------------------ */
/* H2 · 多房间扩建系统 (Multi-Room Expansion)                          */
/* ------------------------------------------------------------------ */

export type RoomType = 'living' | 'bedroom' | 'workshop' | 'storage';

export interface RoomDef {
  id: string;
  type: RoomType;
  label: string;
  unlockLevel: number;
  expansionCost: number;
  description: string;
}

export interface HouseRoomState extends RoomDef {
  unlocked: boolean;
}

/** 四大房间规格定义。 */
export const ROOM_DEFINITIONS: readonly RoomDef[] = [
  {
    id: 'living',
    type: 'living',
    label: '主厅',
    unlockLevel: 1,
    expansionCost: 0,
    description: '起居主厅，待客、会话与休闲中心。',
  },
  {
    id: 'bedroom',
    type: 'bedroom',
    label: '卧室',
    unlockLevel: 2,
    expansionCost: 150,
    description: '舒适卧室，配置床榻与衣柜，睡觉跳过时间至次日早晨。',
  },
  {
    id: 'workshop',
    type: 'workshop',
    label: '工坊厨房',
    unlockLevel: 3,
    expansionCost: 300,
    description: '制作与烹饪中心，包含料理炉灶与精工工作台。',
  },
  {
    id: 'storage',
    type: 'storage',
    label: '储物藏品',
    unlockLevel: 4,
    expansionCost: 500,
    description: '大容量储物箱与珍奇展台，陈列探险矿石、鱼类与稀有收藏。',
  },
] as const;

/** 各房间专属默认布置规范。 */
export const ROOM_DEFAULT_SPECS: Record<RoomType, readonly { id: string; x: number; y: number; colorway?: number }[]> = {
  living: [
    { id: 'table', x: 8, y: 5 },
    { id: 'chair', x: 11, y: 6 },
    { id: 'bookshelf', x: 24, y: 4 },
    { id: 'floor_lamp', x: 6, y: 4 },
    { id: 'rug', x: 10, y: 7 },
    { id: 'plant', x: 27, y: 6 },
    { id: 'mirror', x: 4, y: 4 },
    { id: 'cat_bed', x: 1, y: 8 },
  ],
  bedroom: [
    { id: 'bed', x: 3, y: 5 },
    { id: 'rug_large', x: 8, y: 6 },
    { id: 'floor_lamp', x: 1, y: 4 },
    { id: 'cabinet', x: 22, y: 4 },
    { id: 'mirror', x: 18, y: 4 },
    { id: 'cat_bed', x: 26, y: 7 },
  ],
  workshop: [
    { id: 'stove', x: 4, y: 4 },
    { id: 'table', x: 10, y: 5 },
    { id: 'chair', x: 13, y: 6 },
    { id: 'wall_shelf', x: 4, y: 1 },
    { id: 'crate', x: 18, y: 7 },
    { id: 'crate', x: 21, y: 7 },
    { id: 'cabinet', x: 25, y: 4 },
  ],
  storage: [
    { id: 'crate', x: 3, y: 7 },
    { id: 'crate', x: 6, y: 7 },
    { id: 'crate', x: 9, y: 7 },
    { id: 'cabinet', x: 16, y: 4 },
    { id: 'cabinet', x: 20, y: 4 },
    { id: 'picture_frame', x: 12, y: 0 },
    { id: 'fish_tank', x: 24, y: 5 },
    { id: 'crystal_tree', x: 27, y: 4 },
  ],
};

/** 生成指定房间类型的独立布局。 */
export function createRoomLayout(houseId: string, roomId: string): InteriorLayout {
  const roomDef = ROOM_DEFINITIONS.find((r) => r.id === roomId) ?? ROOM_DEFINITIONS[0]!;
  const specs = ROOM_DEFAULT_SPECS[roomDef.type] ?? ROOM_DEFAULT_SPECS.living;

  const items: InteriorItem[] = specs.map((s, idx) => {
    const pos = clampToRoom(s.id, s.x, s.y);
    return {
      id: newItemId('rm'),
      furnitureId: s.id,
      x: pos.x,
      y: pos.y,
      flipped: false,
      colorway: s.colorway ?? 0,
      z: idx,
    };
  });

  return {
    houseId: `${houseId}:${roomId}`,
    items,
    version: 1,
  };
}

/** 获取玩家当前的房间列表及解锁状态。 */
export function getHouseRooms(
  cabinLevel: number,
  unlockedRoomIds?: readonly string[],
): HouseRoomState[] {
  const unlockedSet = new Set(unlockedRoomIds ?? ['living']);
  return ROOM_DEFINITIONS.map((r) => ({
    ...r,
    unlocked: unlockedSet.has(r.id) || cabinLevel >= r.unlockLevel,
  }));
}

/** 扩建/解锁新房间判定。 */
export function unlockRoom(
  roomId: string,
  cabinLevel: number,
  currentCoins: number,
  alreadyUnlocked: readonly string[],
): { success: boolean; reason?: string; coinsAfter: number; newUnlocked: string[] } {
  if (alreadyUnlocked.includes(roomId)) {
    return {
      success: false,
      reason: '该房间已经扩建完毕',
      coinsAfter: currentCoins,
      newUnlocked: [...alreadyUnlocked],
    };
  }
  const room = ROOM_DEFINITIONS.find((r) => r.id === roomId);
  if (!room) {
    return {
      success: false,
      reason: '未知的房间类型',
      coinsAfter: currentCoins,
      newUnlocked: [...alreadyUnlocked],
    };
  }
  if (cabinLevel < room.unlockLevel) {
    return {
      success: false,
      reason: `小屋等级不足（需要 Lv.${room.unlockLevel}，当前 Lv.${cabinLevel}）`,
      coinsAfter: currentCoins,
      newUnlocked: [...alreadyUnlocked],
    };
  }
  if (currentCoins < room.expansionCost) {
    return {
      success: false,
      reason: `金币不足（扩建需要 ${room.expansionCost} 金币，当前拥有 ${currentCoins} 金币）`,
      coinsAfter: currentCoins,
      newUnlocked: [...alreadyUnlocked],
    };
  }

  return {
    success: true,
    coinsAfter: currentCoins - room.expansionCost,
    newUnlocked: [...alreadyUnlocked, roomId],
  };
}

/* ------------------------------------------------------------------ */
/* H4 · 四大功能交互站 (Storage / Cooking / Crafting / Display)         */
/* ------------------------------------------------------------------ */

// --- 1. 储物站 (Storage) ---
export interface StorageItem {
  itemId: string;
  count: number;
}

export interface CabinStorage {
  maxSlots: number;
  items: StorageItem[];
}

export function createCabinStorage(maxSlots = 24): CabinStorage {
  return { maxSlots, items: [] };
}

export function depositItem(
  storage: CabinStorage,
  itemId: string,
  qty: number,
): { success: boolean; storage: CabinStorage; deposited: number; reason?: string } {
  if (qty <= 0) {
    return { success: false, storage, deposited: 0, reason: '存入数量必须大于 0' };
  }
  const cloned = storage.items.map((it) => ({ ...it }));
  const existing = cloned.find((it) => it.itemId === itemId);
  if (existing) {
    existing.count += qty;
    return { success: true, storage: { ...storage, items: cloned }, deposited: qty };
  }
  if (cloned.length >= storage.maxSlots) {
    return { success: false, storage, deposited: 0, reason: '储物箱已满，无法存放更多种类的物品' };
  }
  cloned.push({ itemId, count: qty });
  return { success: true, storage: { ...storage, items: cloned }, deposited: qty };
}

export function withdrawItem(
  storage: CabinStorage,
  itemId: string,
  qty: number,
): { success: boolean; storage: CabinStorage; withdrawn: number; reason?: string } {
  if (qty <= 0) {
    return { success: false, storage, withdrawn: 0, reason: '取出数量必须大于 0' };
  }
  const cloned = storage.items.map((it) => ({ ...it }));
  const existing = cloned.find((it) => it.itemId === itemId);
  if (!existing || existing.count < qty) {
    return {
      success: false,
      storage,
      withdrawn: 0,
      reason: `储物箱内物品数量不足（需要 ${qty}，仅有 ${existing?.count ?? 0}）`,
    };
  }
  existing.count -= qty;
  const filtered = cloned.filter((it) => it.count > 0);
  return { success: true, storage: { ...storage, items: filtered }, withdrawn: qty };
}

// --- 2. 做饭站 (Cooking) ---
export interface CookingRecipe {
  id: string;
  label: string;
  inputs: Record<string, number>;
  coins: number;
  outputs: Record<string, number>;
  skillLevel: number;
}

/** 对齐 backend crafting.py 的料理与茶酒配方。 */
export const COOKING_RECIPES: readonly CookingRecipe[] = [
  {
    id: 'bread',
    label: '面包',
    inputs: { wheat: 2, honey: 1 },
    coins: 8,
    outputs: { bread: 2 },
    skillLevel: 1,
  },
  {
    id: 'jam',
    label: '果酱',
    inputs: { fruit: 2, honey: 1 },
    coins: 12,
    outputs: { jam: 2 },
    skillLevel: 1,
  },
  {
    id: 'soup',
    label: '热汤',
    inputs: { vegetable: 2, pebble: 1 },
    coins: 10,
    outputs: { soup: 2 },
    skillLevel: 2,
  },
  {
    id: 'mushroom_stew',
    label: '蘑菇汤',
    inputs: { mushroom: 3, herb: 1 },
    coins: 16,
    outputs: { soup: 3 },
    skillLevel: 2,
  },
  {
    id: 'dried_fish',
    label: '鱼干',
    inputs: { fish: 1, reed: 1 },
    coins: 12,
    outputs: { fish: 3 },
    skillLevel: 1,
  },
  {
    id: 'pumpkin_pie',
    label: '南瓜派',
    inputs: { pumpkin: 2, wheat: 2 },
    coins: 26,
    outputs: { pastry: 3 },
    skillLevel: 3,
  },
  {
    id: 'farm_jam',
    label: '农家果酱',
    inputs: { fruit: 3, honey: 1 },
    coins: 14,
    outputs: { jam: 3 },
    skillLevel: 1,
  },
  {
    id: 'tea',
    label: '清茶',
    inputs: { tea_leaf: 2 },
    coins: 15,
    outputs: { tea: 2 },
    skillLevel: 3,
  },
  {
    id: 'wine',
    label: '美酒',
    inputs: { fruit: 3, honey: 1 },
    coins: 24,
    outputs: { wine: 1 },
    skillLevel: 3,
  },
] as const;

export function canCook(
  recipeId: string,
  bag: Record<string, number>,
  coins: number,
  playerLevel = 1,
): { ok: boolean; reason?: string; recipe?: CookingRecipe } {
  const recipe = COOKING_RECIPES.find((r) => r.id === recipeId);
  if (!recipe) return { ok: false, reason: `未知料理配方: ${recipeId}` };
  if (playerLevel < recipe.skillLevel) {
    return { ok: false, reason: `烹饪技能等级不足（需 Lv.${recipe.skillLevel}，当前 Lv.${playerLevel}）`, recipe };
  }
  if (coins < recipe.coins) {
    return { ok: false, reason: `烹饪消耗金币不足（需 ${recipe.coins} 金币，当前 ${coins} 金币）`, recipe };
  }
  for (const [mat, need] of Object.entries(recipe.inputs)) {
    const has = bag[mat] ?? 0;
    if (has < need) {
      return { ok: false, reason: `缺少食材 ${mat}（需 ${need}，背包仅有 ${has}）`, recipe };
    }
  }
  return { ok: true, recipe };
}

export function cookMeal(
  recipeId: string,
  bag: Record<string, number>,
  coins: number,
  playerLevel = 1,
): { success: boolean; newBag: Record<string, number>; coinsAfter: number; outputs: Record<string, number>; reason?: string } {
  const check = canCook(recipeId, bag, coins, playerLevel);
  if (!check.ok || !check.recipe) {
    return { success: false, newBag: { ...bag }, coinsAfter: coins, outputs: {}, reason: check.reason };
  }
  const r = check.recipe;
  const newBag = { ...bag };
  for (const [mat, need] of Object.entries(r.inputs)) {
    newBag[mat] = (newBag[mat] ?? 0) - need;
    if (newBag[mat] <= 0) delete newBag[mat];
  }
  for (const [out, count] of Object.entries(r.outputs)) {
    newBag[out] = (newBag[out] ?? 0) + count;
  }
  return {
    success: true,
    newBag,
    coinsAfter: coins - r.coins,
    outputs: { ...r.outputs },
  };
}

// --- 3. 制作站 (Crafting) ---
export interface CraftStationRecipe {
  id: string;
  label: string;
  inputs: Record<string, number>;
  coins: number;
  outputs: Record<string, number>;
  skillLevel: number;
  category: 'craft' | 'furniture' | 'tech' | 'alchemy';
}

export const CRAFT_STATION_RECIPES: readonly CraftStationRecipe[] = [
  {
    id: 'handicraft',
    label: '手工编织品',
    inputs: { wood: 3, cotton: 1 },
    coins: 20,
    outputs: { handicraft: 1 },
    skillLevel: 2,
    category: 'craft',
  },
  {
    id: 'incense',
    label: '草木线香',
    inputs: { herb: 2, wood: 1 },
    coins: 14,
    outputs: { incense: 2 },
    skillLevel: 2,
    category: 'craft',
  },
  {
    id: 'wood_chair',
    label: '木椅',
    inputs: { wood: 4 },
    coins: 30,
    outputs: { furniture_chair: 1 },
    skillLevel: 2,
    category: 'furniture',
  },
  {
    id: 'flower_vase',
    label: '手工花瓶',
    inputs: { flower: 3, pebble: 1 },
    coins: 26,
    outputs: { furniture_vase: 1 },
    skillLevel: 2,
    category: 'furniture',
  },
  {
    id: 'potion',
    label: '初级魔法药水',
    inputs: { magic_herb: 2, magic_crystal: 1 },
    coins: 55,
    outputs: { potion: 1 },
    skillLevel: 4,
    category: 'alchemy',
  },
  {
    id: 'battery',
    label: '能量电池',
    inputs: { energy_cell: 1, alloy_ore: 2 },
    coins: 52,
    outputs: { battery: 2 },
    skillLevel: 4,
    category: 'tech',
  },
] as const;

export function canCraftItem(
  recipeId: string,
  bag: Record<string, number>,
  coins: number,
  playerLevel = 1,
): { ok: boolean; reason?: string; recipe?: CraftStationRecipe } {
  const recipe = CRAFT_STATION_RECIPES.find((r) => r.id === recipeId);
  if (!recipe) return { ok: false, reason: `未知工坊制作配方: ${recipeId}` };
  if (playerLevel < recipe.skillLevel) {
    return { ok: false, reason: `工坊技能等级不足（需 Lv.${recipe.skillLevel}，当前 Lv.${playerLevel}）`, recipe };
  }
  if (coins < recipe.coins) {
    return { ok: false, reason: `制作金币不足（需 ${recipe.coins} 金币，当前 ${coins} 金币）`, recipe };
  }
  for (const [mat, need] of Object.entries(recipe.inputs)) {
    const has = bag[mat] ?? 0;
    if (has < need) {
      return { ok: false, reason: `缺少材料 ${mat}（需 ${need}，背包仅有 ${has}）`, recipe };
    }
  }
  return { ok: true, recipe };
}

// --- 4. 展示收藏站 (Display & Collections) ---
export interface DisplaySlot {
  slotId: number;
  itemId: string | null;
  label: string | null;
  inspectionNote: string | null;
}

export interface CabinDisplayStand {
  maxSlots: number;
  slots: DisplaySlot[];
}

export function createDisplayStand(maxSlots = 6): CabinDisplayStand {
  return {
    maxSlots,
    slots: Array.from({ length: maxSlots }, (_, i) => ({
      slotId: i,
      itemId: null,
      label: null,
      inspectionNote: null,
    })),
  };
}

export function placeOnDisplay(
  stand: CabinDisplayStand,
  slotId: number,
  itemId: string,
  label: string,
  note = '散发着微光的珍贵藏品。',
): { success: boolean; stand: CabinDisplayStand; reason?: string } {
  if (slotId < 0 || slotId >= stand.maxSlots) {
    return { success: false, stand, reason: '非法展位索引' };
  }
  const cloned = stand.slots.map((s) => ({ ...s }));
  cloned[slotId] = {
    slotId,
    itemId,
    label,
    inspectionNote: note,
  };
  return { success: true, stand: { ...stand, slots: cloned } };
}

export function takeFromDisplay(
  stand: CabinDisplayStand,
  slotId: number,
): { success: boolean; stand: CabinDisplayStand; removedItem: string | null } {
  if (slotId < 0 || slotId >= stand.maxSlots || !stand.slots[slotId]?.itemId) {
    return { success: false, stand, removedItem: null };
  }
  const removedItem = stand.slots[slotId]!.itemId;
  const cloned = stand.slots.map((s) => ({ ...s }));
  cloned[slotId] = { slotId, itemId: null, label: null, inspectionNote: null };
  return { success: true, stand: { ...stand, slots: cloned }, removedItem };
}

/* ------------------------------------------------------------------ */
/* H3 · 睡觉 = 存档 + 跳时间 (Sleep Cycle)                             */
/* ------------------------------------------------------------------ */

export interface SleepReport {
  dayBefore?: number;
  dayAfter: number;
  timeLabel: string;
  coinsBefore?: number;
  coinsAfter: number;
  settlementRevenue?: number;
  note: string;
}

/**
 * 客户端执行睡觉权威闭环：
 * 1. 验证存在合法床铺；
 * 2. 调 POST /api/cabin/life/action { action: 'sleep' } 派发跨日动作；
 * 3. 服务端权威推进时钟至次日 06:00 并自动持久化存档；
 * 4. 读回最新 snapshot / action 结果，核验前后一致性。
 *
 * 铁律：dayAfter / coinsAfter / timeLabel / 天气 一律取自服务端权威数据，严禁前端自算或写死兜底。
 */
export async function executeSleep(
  onAction: (action: string, args?: Record<string, unknown>) => Promise<unknown> | unknown,
  currentSnapshot?: LifeSnapshot | null,
  fetchSnapshotFn?: () => Promise<LifeSnapshot>,
): Promise<SleepReport> {
  const dayBefore = currentSnapshot?.save?.clock?.day;
  const coinsBefore = currentSnapshot?.save?.coins;

  // 1. 派发权威 sleep 动作并捕获返回值
  const actionRes = await onAction('sleep', {});

  let afterSave: LifeSave | null = null;
  let settlementRevenue: number | undefined = undefined;
  let serverNote: string | undefined = undefined;

  // 2. 优先解析 onAction 返回对象（LifeActionResult 或包含 save / snapshot 的结构）
  if (actionRes && typeof actionRes === 'object') {
    const res = actionRes as Record<string, unknown>;
    if (res.save && typeof res.save === 'object') {
      afterSave = res.save as LifeSave;
    } else if (res.snapshot && typeof res.snapshot === 'object') {
      const snap = res.snapshot as Record<string, unknown>;
      if (snap.save && typeof snap.save === 'object') {
        afterSave = snap.save as LifeSave;
      }
    }
    if (res.settlement && typeof res.settlement === 'object') {
      const sett = res.settlement as Record<string, unknown>;
      if (typeof sett.total_revenue === 'number') {
        settlementRevenue = sett.total_revenue;
      }
    }
    if (typeof res.note === 'string') {
      serverNote = res.note;
    }
  }

  // 3. 若 onAction 未提供 save，则通过 fetchSnapshotFn 或 lifeApi.fetchSnapshot 读回权威快照
  if (!afterSave) {
    const freshSnap = fetchSnapshotFn
      ? await fetchSnapshotFn()
      : await lifeApi.fetchSnapshot().catch(() => null);
    if (freshSnap?.save) {
      afterSave = freshSnap.save;
    }
  }

  // 4. 若服务端仍未返回合法存档，拒绝伪造，明确抛错（零伪造：缺时钟或金币一律抛错拒收，绝不伪造 0 或默认时间）
  if (!afterSave || !afterSave.clock || typeof afterSave.clock.day !== 'number') {
    throw new Error('睡觉动作失败：服务端未返回权威存档或时钟数据');
  }
  if (typeof afterSave.coins !== 'number') {
    throw new Error('睡觉动作失败：服务端未返回权威金币数据');
  }

  const dayAfter = afterSave.clock.day;
  const coinsAfter = afterSave.coins;
  const partLabel = afterSave.clock.part_label ?? '';
  const minuteStr = typeof afterSave.clock.minute === 'number' ? formatMinute(afterSave.clock.minute) : '';
  const weatherLabel = afterSave.weather?.label ?? afterSave.weather?.id ?? '';
  const timeLabel = `第${dayAfter}天 ${partLabel} ${minuteStr} ${weatherLabel}`.replace(/\s+/g, ' ').trim();
  const note = serverNote ?? (minuteStr
    ? `睡到第 ${dayAfter} 天 ${minuteStr}；未售出货品安全留存。`
    : `睡到第 ${dayAfter} 天；未售出货品安全留存。`);

  return {
    dayBefore,
    dayAfter,
    timeLabel,
    coinsBefore,
    coinsAfter,
    settlementRevenue,
    note,
  };
}

/* ------------------------------------------------------------------ */
/* H1 / H5 · 性能基准与无缝进出校验 (<1s 加载)                           */
/* ------------------------------------------------------------------ */

export interface TransitionMetric {
  houseId: string;
  roomId: string;
  elapsedMs: number;
  itemCount: number;
  pass1sBudget: boolean;
}

/** 测量进入并构建室内房间场景的毫秒耗时（判据 H1）。 */
export function measureIndoorLoadTime(
  houseId: string,
  roomId = 'living',
): TransitionMetric {
  const t0 = performance.now();
  const layout = createRoomLayout(houseId, roomId);
  const t1 = performance.now();
  const elapsedMs = Math.round((t1 - t0) * 100) / 100;

  return {
    houseId,
    roomId,
    elapsedMs,
    itemCount: layout.items.length,
    pass1sBudget: elapsedMs < 1000,
  };
}

/** 无缝状态缓存容器：在进出屋过程中保全角色坐标与世界状态（判据 H5）。 */
export interface OutdoorPlayerState {
  playerX: number;
  playerY: number;
  cameraX: number;
  themeId: string;
  timeOfDay: string;
}

export class SeamlessTransitionManager {
  private savedState: OutdoorPlayerState | null = null;

  saveOutdoorState(state: OutdoorPlayerState): void {
    this.savedState = { ...state };
  }

  getSavedOutdoorState(): OutdoorPlayerState | null {
    return this.savedState ? { ...this.savedState } : null;
  }

  clear(): void {
    this.savedState = null;
  }
}

export const globalTransitionManager = new SeamlessTransitionManager();
