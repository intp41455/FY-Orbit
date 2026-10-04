/**
 * W1 · 家具注册表（前后端契约唯一真源）
 *
 * 分层约定：本文件是**纯数据 + 纯逻辑**，不 import PixiJS / 不碰 DOM，
 * 因此可在 jsdom + vitest 与后端 pytest 两侧共用同一套 id 语义。
 *
 * ★ 前后端契约（服务端 cabin_interior.py 的白名单校验以本表 id 为准）：
 *   1. `id` 一旦发布**永不改动**（后端已落库的 layout JSON 依赖它）；
 *   2. `sizeCells`（占格宽高）单位是 16px 网格格，`mount` 决定可落位区域；
 *   3. `unlockedBy` 是给 W2「材料 → 图纸 → 制造」链预留的字段：
 *      `default` 开局可得 / `level` 小屋等级解锁 / `quest` 任务解锁 / `craft` 图纸制造。
 *      W1 已按任务书要求落地 ≥4 件 `level` 解锁家具（后端不校验此字段，仅前端可见性）。
 *
 * 诚实原则：`interact` 声明的交互能力必须真的实现；未实现的交互不得写入注册表冒充已完成。
 */

/** 家具落位方式：地板家具 / 墙面家具（画框、壁挂架）/ 天花板家具（吊灯）。 */
export type FurnitureMount = 'floor' | 'wall' | 'ceiling';

/** 交互类型。`none` = v1 仅作陈设，不可点击交互。
 *  `mirror` = 更衣镜：W11「角色工坊」入口（点击后由页面跳转/弹层，W1 只负责交互点与文案）。 */
export type FurnitureInteract = 'none' | 'sleep' | 'read' | 'sit' | 'lamp' | 'water' | 'cozy' | 'mirror';

export type FurnitureUnlockSource = 'default' | 'level' | 'quest' | 'craft';

export interface FurnitureDef {
  /** 稳定唯一 id —— 后端白名单与已落库 layout 的键。 */
  readonly id: string;
  readonly label: string;
  /** 占位尺寸（单位：16px 网格格）。渲染与吸附共用，避免前后端理解不一致。 */
  readonly sizeCells: { readonly w: number; readonly h: number };
  readonly mount: FurnitureMount;
  readonly interact: FurnitureInteract;
  readonly unlockedBy: FurnitureUnlockSource;
  /** 解锁所需的小屋等级（unlockedBy='level' 时有效，其余为 0）。 */
  readonly unlockLevel: number;
  /** 主题限定：仅在指定房屋风格可放置（对应 11 号文档 DNA-9 主题一致性）。
   *  empty = 全风格通用。 */
  readonly themes: readonly string[];
}

/* ------------------------------------------------------------------ */
/* 家具目录：18 种（任务书要求 ≥12；含 4 件等级解锁 + 2 件墙面 + 1 件天花板） */
/* ------------------------------------------------------------------ */

export const FURNITURE_CATALOG: readonly FurnitureDef[] = [
  { id: 'bed', label: '床', sizeCells: { w: 3, h: 2 }, mount: 'floor', interact: 'sleep', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'table', label: '餐桌', sizeCells: { w: 2, h: 2 }, mount: 'floor', interact: 'none', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'chair', label: '椅子', sizeCells: { w: 1, h: 1 }, mount: 'floor', interact: 'sit', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'bookshelf', label: '书架', sizeCells: { w: 2, h: 2 }, mount: 'floor', interact: 'read', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'floor_lamp', label: '落地灯', sizeCells: { w: 1, h: 2 }, mount: 'floor', interact: 'lamp', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'rug', label: '地毯', sizeCells: { w: 3, h: 2 }, mount: 'floor', interact: 'cozy', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'plant', label: '盆栽', sizeCells: { w: 1, h: 1 }, mount: 'floor', interact: 'none', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'cabinet', label: '柜子', sizeCells: { w: 2, h: 1 }, mount: 'floor', interact: 'none', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'cat_bed', label: '猫窝', sizeCells: { w: 1, h: 1 }, mount: 'floor', interact: 'cozy', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'fish_tank', label: '鱼缸', sizeCells: { w: 2, h: 1 }, mount: 'floor', interact: 'water', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'crate', label: '木箱', sizeCells: { w: 1, h: 1 }, mount: 'floor', interact: 'none', unlockedBy: 'default', unlockLevel: 0, themes: [] },
  { id: 'picture_frame', label: '画框', sizeCells: { w: 1, h: 1 }, mount: 'wall', interact: 'none', unlockedBy: 'default', unlockLevel: 0, themes: [] },

  // --- W11 衔接：更衣镜（角色工坊入口）。开局默认可得，作为「我的小人」入口常驻。 ---
  { id: 'mirror', label: '更衣镜', sizeCells: { w: 1, h: 2 }, mount: 'floor', interact: 'mirror', unlockedBy: 'default', unlockLevel: 0, themes: [] },

  // --- 以下 4 件为「小屋等级」解锁（任务书增补第 3 条：v1 至少 4 件） ---
  { id: 'ceiling_lamp', label: '吊灯', sizeCells: { w: 2, h: 1 }, mount: 'ceiling', interact: 'lamp', unlockedBy: 'level', unlockLevel: 2, themes: [] },
  { id: 'wall_shelf', label: '壁架', sizeCells: { w: 1, h: 1 }, mount: 'wall', interact: 'none', unlockedBy: 'level', unlockLevel: 2, themes: [] },
  { id: 'rug_large', label: '大块地毯', sizeCells: { w: 4, h: 3 }, mount: 'floor', interact: 'cozy', unlockedBy: 'level', unlockLevel: 3, themes: [] },
  { id: 'stove', label: '炉子', sizeCells: { w: 2, h: 2 }, mount: 'floor', interact: 'cozy', unlockedBy: 'level', unlockLevel: 4, themes: [] },

  // --- 主题限定家具（11 号文档 DNA-9：树林不该出现宇宙水晶） ---
  { id: 'crystal_tree', label: '水晶树', sizeCells: { w: 2, h: 3 }, mount: 'floor', interact: 'none', unlockedBy: 'quest', unlockLevel: 0, themes: ['planet'] },
  { id: 'snow_lamp', label: '冰晶灯', sizeCells: { w: 1, h: 2 }, mount: 'floor', interact: 'lamp', unlockedBy: 'quest', unlockLevel: 0, themes: ['snowcave'] },
  { id: 'herb_shelf', label: '草药架', sizeCells: { w: 2, h: 1 }, mount: 'wall', interact: 'none', unlockedBy: 'craft', unlockLevel: 0, themes: ['garden', 'forest'] },
] as const;

/* ------------------------------------------------------------------ */
/* 查询工具                                                             */
/* ------------------------------------------------------------------ */

const BY_ID = new Map<string, FurnitureDef>(FURNITURE_CATALOG.map((f) => [f.id, f]));

/** 全部家具 id（后端白名单 = 该集合；顺序稳定，便于测试断言）。 */
export const FURNITURE_IDS: readonly string[] = FURNITURE_CATALOG.map((f) => f.id);

export function getFurniture(id: string): FurnitureDef | undefined {
  return BY_ID.get(id);
}

export function isFurnitureId(id: unknown): id is string {
  return typeof id === 'string' && BY_ID.has(id);
}

/** 按落位方式筛选（家具栏分「地板 / 墙面 / 天花板」三组展示）。 */
export function furnitureByMount(mount: FurnitureMount): readonly FurnitureDef[] {
  return FURNITURE_CATALOG.filter((f) => f.mount === mount);
}

/**
 * 当前可放置的家具：等级 + 主题双重过滤。
 *
 * 诚实说明：W1 的小屋等级固定按「已布置家具数」推导（Lv1..Lv5，见 cabinInteriorState.ts
 * `deriveCabinLevel`），真正的等级经验值来自 W2 的探险/任务线。`craft`（图纸制造）
 * 在 W1 不可得 —— 这里**不返回**任何 craft 家具，避免把没做的系统伪装成已解锁。
 */
export function unlockedFurniture(opts: {
  level: number;
  theme: string;
  /** W2 制造链已完成的家具 id（W1 恒为空集）。 */
  craftedIds?: readonly string[];
}): readonly FurnitureDef[] {
  const crafted = new Set(opts.craftedIds ?? []);
  return FURNITURE_CATALOG.filter((f) => {
    if (f.themes.length > 0 && !f.themes.includes(opts.theme)) return false;
    switch (f.unlockedBy) {
      case 'default':
        return true;
      case 'level':
        return opts.level >= f.unlockLevel;
      case 'quest':
        // W2 任务线未接入：诚实隐藏，不假装已解锁。
        return false;
      case 'craft':
        return crafted.has(f.id);
      default:
        return false;
    }
  });
}

/** 某家具在当前小屋等级下的解锁状态（家具栏用灰显 + 锁标记展示）。 */
export function isFurnitureUnlocked(def: FurnitureDef, level: number): boolean {
  return unlockedFurniture({ level, theme: '__all__' }).some((f) => f.id === def.id);
}
