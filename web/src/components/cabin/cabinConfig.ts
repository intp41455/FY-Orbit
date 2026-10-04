import { useCallback, useEffect, useState } from 'react';

/**
 * 数码小屋（P2）纯逻辑层：配置常量、localStorage 持久化、台词池与模型接口位。
 *
 * 分层约定：本文件不依赖 PixiJS / DOM 渲染，必须在 jsdom + vitest 下可直接测
 * （模板切换 / 配置持久化 / 台词挑选）。渲染层见同目录 cabinScene.ts。
 *
 * 持久化 key：fy.cabin.config.v1 —— 结构即下方 CabinConfig JSON。
 * 未来迁移：整包搬到后端 cabin_config JSON 字段（key 加 .v1 即为升级留位），
 * sanitizeCabinConfig 的字段白名单校验可原样复用于后端响应。
 */

export const CABIN_STORAGE_KEY = 'fy.cabin.config.v1';

/* ------------------------------------------------------------------ */
/* 房屋模板：6 种，全部由 cabinScene.ts 用 Graphics 程序化绘制（零素材） */
/* ------------------------------------------------------------------ */

export type CabinHouseId = 'villa' | 'cabin' | 'cave' | 'snowcave' | 'bunker' | 'castle';

export interface CabinHouseMeta {
  id: CabinHouseId;
  label: string;
}

export const CABIN_HOUSES: readonly CabinHouseMeta[] = [
  { id: 'villa', label: '别墅' },
  { id: 'cabin', label: '小木屋' },
  { id: 'cave', label: '山洞' },
  { id: 'snowcave', label: '雪洞' },
  { id: 'bunker', label: '地堡' },
  { id: 'castle', label: '城堡' },
] as const;

/* ------------------------------------------------------------------ */
/* 背景：5 种，渐变天空 + 程序化元素（树林/花/水面/田垄/星球+星星）      */
/* ------------------------------------------------------------------ */

export type CabinBackgroundId =
  | 'forest'
  | 'garden'
  | 'stream'
  | 'field'
  | 'planet'
  | 'magic_continent'
  | 'scifi_planet'
  | 'pastoral_countryside'
  | 'peach_blossom_spring';

export interface CabinBackgroundMeta {
  id: CabinBackgroundId;
  label: string;
}

export const CABIN_BACKGROUNDS: readonly CabinBackgroundMeta[] = [
  { id: 'forest', label: '树林' },
  { id: 'garden', label: '花园' },
  { id: 'stream', label: '小溪旁' },
  { id: 'field', label: '田野' },
  { id: 'planet', label: '宇宙星球' },
  { id: 'magic_continent', label: '魔法大陆' },
  { id: 'scifi_planet', label: '科幻星球' },
  { id: 'pastoral_countryside', label: '田园乡村' },
  { id: 'peach_blossom_spring', label: '古风桃源' },
] as const;

/* ------------------------------------------------------------------ */
/* 宠物：色板 + 性格（性格同时驱动小人/宠物的台词挑选）                  */
/* ------------------------------------------------------------------ */

export interface PetColorMeta {
  id: string;
  label: string;
  /** CSS 十六进制，色板 UI 与 PixiJS 填色共用（Pixi 侧转 number）。 */
  hex: string;
}

export const PET_COLORS: readonly PetColorMeta[] = [
  { id: 'flame', label: '火焰红', hex: '#ef5350' },
  { id: 'lemon', label: '柠檬黄', hex: '#ffd54f' },
  { id: 'mint', label: '薄荷绿', hex: '#66bb8a' },
  { id: 'sky', label: '天空蓝', hex: '#4fc3f7' },
  { id: 'sakura', label: '樱花粉', hex: '#f48fb1' },
  { id: 'moon', label: '月光白', hex: '#eceff1' },
] as const;

export type PersonalityId = 'lively' | 'cool' | 'melancholy' | 'chatty';

export interface PersonalityMeta {
  id: PersonalityId;
  label: string;
}

export const PERSONALITIES: readonly PersonalityMeta[] = [
  { id: 'lively', label: '活泼' },
  { id: 'cool', label: '高冷' },
  { id: 'melancholy', label: '忧郁' },
  { id: 'chatty', label: '话痨' },
] as const;

/* ------------------------------------------------------------------ */
/* A5 光影体系：昼夜时段与色温映射                                      */
/* ------------------------------------------------------------------ */

export type TimeOfDay = 'dawn' | 'day' | 'dusk' | 'night';

export interface TimeOfDayMeta {
  id: TimeOfDay;
  label: string;
  tint: number;
  alpha: number;
}

export const TIME_OF_DAY_LIST: readonly TimeOfDayMeta[] = [
  { id: 'dawn', label: '清晨', tint: 0xffeedb, alpha: 0.18 },
  { id: 'day', label: '白天', tint: 0xffffff, alpha: 0 },
  { id: 'dusk', label: '黄昏', tint: 0xffaa66, alpha: 0.28 },
  { id: 'night', label: '夜晚', tint: 0x334466, alpha: 0.45 },
] as const;

/* ------------------------------------------------------------------ */
/* 配置对象 + 持久化                                                    */
/* ------------------------------------------------------------------ */

export interface CabinConfig {
  house: CabinHouseId;
  background: CabinBackgroundId;
  petColor: string;
  petPersonality: PersonalityId;
  /** 小人性格同样参与台词挑选；工具条提供下拉（v1 与宠物共用性格集）。 */
  personPersonality: PersonalityId;
  personName: string;
  /** A5 昼夜色温，默认白天 */
  timeOfDay?: TimeOfDay;
}

export const DEFAULT_CABIN_CONFIG: CabinConfig = {
  house: 'cabin',
  background: 'forest',
  petColor: 'sky',
  petPersonality: 'lively',
  personPersonality: 'chatty',
  personName: '小寻',
  timeOfDay: 'day',
};

function pickWhitelisted<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

/** 字段白名单校验 + 逐字段回退默认值（向后兼容，新字段自动补齐）。 */
export function sanitizeCabinConfig(raw: unknown): CabinConfig {
  if (typeof raw !== 'object' || raw === null) return { ...DEFAULT_CABIN_CONFIG };
  const r = raw as Record<string, unknown>;
  const houseIds = CABIN_HOUSES.map((h) => h.id);
  const bgIds = CABIN_BACKGROUNDS.map((b) => b.id);
  const colorIds = PET_COLORS.map((c) => c.id);
  const personalityIds = PERSONALITIES.map((p) => p.id);
  const timeIds = TIME_OF_DAY_LIST.map((t) => t.id);
  const name = typeof r.personName === 'string' ? r.personName.trim().slice(0, 16) : '';
  return {
    house: pickWhitelisted(r.house, houseIds, DEFAULT_CABIN_CONFIG.house),
    background: pickWhitelisted(r.background, bgIds, DEFAULT_CABIN_CONFIG.background),
    petColor: pickWhitelisted(r.petColor, colorIds, DEFAULT_CABIN_CONFIG.petColor),
    petPersonality: pickWhitelisted(r.petPersonality, personalityIds, DEFAULT_CABIN_CONFIG.petPersonality),
    personPersonality: pickWhitelisted(r.personPersonality, personalityIds, DEFAULT_CABIN_CONFIG.personPersonality),
    personName: name || DEFAULT_CABIN_CONFIG.personName,
    timeOfDay: pickWhitelisted(r.timeOfDay, timeIds, DEFAULT_CABIN_CONFIG.timeOfDay ?? 'day'),
  };
}

function safeStorage(): Storage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage;
  } catch {
    return null;
  }
}

/** 从 localStorage 读取并校验；损坏/缺字段一律回退默认值，绝不抛错。 */
export function loadCabinConfig(storage: Storage | null = safeStorage()): CabinConfig {
  if (!storage) return { ...DEFAULT_CABIN_CONFIG };
  try {
    const raw = storage.getItem(CABIN_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CABIN_CONFIG };
    return sanitizeCabinConfig(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_CABIN_CONFIG };
  }
}

/** 写入 localStorage（结构 = 未来后端 cabin_config JSON，见文件头注释）。 */
export function saveCabinConfig(config: CabinConfig, storage: Storage | null = safeStorage()): void {
  if (!storage) return;
  try {
    storage.setItem(CABIN_STORAGE_KEY, JSON.stringify(config));
  } catch {
    // 配额满 / 隐私模式：静默降级为会话内状态，不阻塞游戏。
  }
}

/** React 侧状态钩子：初始化读盘，变更即写盘（实时生效 + 持久化）。 */
export function useCabinConfig(): [CabinConfig, (patch: Partial<CabinConfig>) => void] {
  const [config, setConfig] = useState<CabinConfig>(() => loadCabinConfig());
  useEffect(() => {
    saveCabinConfig(config);
  }, [config]);
  const update = useCallback((patch: Partial<CabinConfig>) => {
    setConfig((prev) => sanitizeCabinConfig({ ...prev, ...patch }));
  }, []);
  return [config, update];
}

/* ------------------------------------------------------------------ */
/* G3 · 开放世界：世界坐标 + 摄像机（纯逻辑，可在 jsdom 直测）          */
/* ------------------------------------------------------------------ */
/* A1 · 分辨率与网格地基（一次冻结）                                    */
/* ------------------------------------------------------------------ */

export const VIRTUAL_W = 640;
export const VIRTUAL_H = 360;
export const TILE = 32;

/** 把世界像素 x 坐标吸附到 32px 地面格（整数格）。 */
export function snapToGrid(v: number, tile = TILE): number {
  return Math.round(v / tile) * tile;
}

/** 像素坐标 -> 整数格坐标 */
export function toGrid(coord: number, tile = TILE): number {
  return Math.round(coord / tile);
}

/** 整数格坐标 -> 像素坐标 */
export function fromGrid(gridCoord: number, tile = TILE): number {
  return Math.round(gridCoord * tile);
}

/* ------------------------------------------------------------------ */
/* G3 · 开放世界：世界坐标 + 摄像机（纯逻辑，可在 jsdom 直测）          */
/* ------------------------------------------------------------------ */

/**
 * 世界与摄像机配置。**单位一律是「虚拟像素」**（虚拟分辨率见 VIRTUAL_W/H；
 * 虚拟宽度 = 视口宽 / worldScale）。渲染层只负责把虚拟像素乘
 * worldScale 画到屏幕，本文件不依赖 Pixi / DOM，可直接单测。
 */
export interface CabinWorld {
  /** 世界宽度（虚拟像素）。5120 = 640 虚拟宽 × 8 屏。 */
  width: number;
  /** 房屋中心的**世界** x（虚拟像素）。按 32px 网格对齐（65 格 = 2080px）。 */
  houseX: number;
  /** 小人出生点的**世界** x（虚拟像素，按 32px 网格对齐，69 格 = 2208px）。 */
  spawnX: number;
  /** 人物/宠物距世界左右边缘的最小留白（虚拟像素，按 32px 网格对齐，1 格 = 32px）。 */
  edgeMargin: number;
  /** 边界视觉收束（渐隐）的触发距离（虚拟像素）：相机距世界边缘小于它就渐隐。 */
  edgeFadePx: number;
  /** 边界渐隐的最大不透明度（0-1）。 */
  edgeFadeMax: number;
  /** 相机 lerp 基数（越小越黏）。每帧插值量 = 1 - lag^(dt/1000)。 */
  cameraLag: number;
  /** 相机相对人物的前瞻比例（0.5 = 人物居中）。 */
  cameraLead: number;
}

export const WORLD: CabinWorld = {
  width: 5120,
  houseX: 2080,
  spawnX: 2208,
  edgeMargin: 32,
  edgeFadePx: 160,
  edgeFadeMax: 0.82,
  cameraLag: 0.0015,
  cameraLead: 0.5,
};

/**
 * 人物必须留在视口内的最小边距（虚拟像素）。
 * 相机是 lerp 平滑的，长距离快走时相机会滞后于人物；
 * 不加这道钳制，人物会被甩到视口外。
 */
export const CAMERA_SAFE_MARGIN = 32;

/**
 * 把相机硬钳到「人物必在视口内」的区间，同时不越世界边界。
 *
 * 与 lerpCameraX 的分工：lerp 负责**手感平滑**，本函数负责**硬约束**。
 * 小步移动时 lerp 结果本来就在安全区内，本函数是恒等映射（不改变手感）；
 * 只有快走滞后时才真正生效，把人物拉回画面内。
 *
 * 退化情形（人物贴世界边缘、或视口比世界还宽）：此时「人物居中」与
 * 「相机不出世界」矛盾，区间可能空（hi <= lo）。此时**取 lo** ——
 * lo 恒等于「人物贴在视口右缘（或视口左缘）的最小合法相机」，
 * 是唯一保证人物仍然在画面内的选择（实测人物在世界左缘时，
 * 取 hi 会把人甩到视口外 476px，取 lo 则 person 屏幕偏移 = 24px）。
 */
export function clampCameraToPerson(
  cameraX: number,
  personWorldX: number,
  viewWidth: number,
  world: CabinWorld = WORLD,
  margin = CAMERA_SAFE_MARGIN,
): number {
  const lo = Math.max(0, personWorldX - (viewWidth - margin));
  const hi = Math.max(lo, Math.min(cameraMaxX(viewWidth, world), personWorldX - margin));
  return clampNum(cameraX, lo, hi);
}

/** 当前虚拟宽度下世界横向可探索的屏数（观测用；虚拟宽度默认 VIRTUAL_W 640）。 */
export function worldScreens(world: CabinWorld = WORLD, virtualWidth = VIRTUAL_W): number {
  return virtualWidth > 0 ? world.width / virtualWidth : 0;
}

function clampNum(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/**
 * G3-2：把人物/宠物钳制到**世界**范围，而不是屏幕范围。
 * 合法区间 [edgeMargin, width - edgeMargin]；若世界比视口还窄（极小世界 + 超宽屏），
 * 退化为以世界中心为唯一合法点，绝不返回区间外的值。
 */
export function clampToWorld(worldX: number, world: CabinWorld = WORLD): number {
  const lo = world.edgeMargin;
  const hi = world.width - world.edgeMargin;
  if (hi <= lo) return world.width / 2;
  return clampNum(worldX, lo, hi);
}

/** 世界 x（虚拟像素）→ 屏幕偏移（虚拟像素，未乘 worldScale）。 */
export function worldToScreenX(worldX: number, cameraX: number): number {
  return worldX - cameraX;
}

/** 屏幕偏移（虚拟像素）→ 世界 x。指针点击反投影用（G3-2 第 2 处 clamp）。 */
export function screenToWorldX(screenX: number, cameraX: number): number {
  return screenX + cameraX;
}

/** 相机 x 的合法上界：世界右缘对齐视口右缘。视口比世界宽时为 0。 */
export function cameraMaxX(viewWidth: number, world: CabinWorld = WORLD): number {
  return Math.max(0, world.width - viewWidth);
}

/** 钳制相机到 [0, cameraMaxX]，保证画面不越出世界左右缘。 */
export function clampCameraX(cameraX: number, viewWidth: number, world: CabinWorld = WORLD): number {
  return clampNum(cameraX, 0, cameraMaxX(viewWidth, world));
}

/**
 * 相机目标位置：把人物按 cameraLead 比例放到视口内，再钳到世界范围。
 * 站在世界边缘时目标被钳住，相机不会追出世界 —— 这是「不硬弹回」的来源：
 * 人物自己被 clampToWorld 挡住，相机只是平滑地停在边界。
 */
export function cameraTargetX(personWorldX: number, viewWidth: number, world: CabinWorld = WORLD): number {
  return clampCameraX(personWorldX - viewWidth * world.cameraLead, viewWidth, world);
}

/**
 * 帧率无关的指数平滑（G3-5）。dtMs<=0 或非有限值时原样返回，
 * 保证 resize / 大坐标 / NaN 防御下相机不会跳到 NaN。
 */
export function lerpCameraX(current: number, target: number, dtMs: number, world: CabinWorld = WORLD): number {
  if (!Number.isFinite(current) || !Number.isFinite(target) || !(dtMs > 0)) return current;
  const t = 1 - Math.pow(world.cameraLag, Math.min(dtMs, 250) / 1000);
  return current + (target - current) * clampNum(t, 0, 1);
}

/**
 * G3-6 边界视觉收束强度（0-1）：相机距左右世界边缘越近越强，两侧同时生效。
 * 玩家到边缘时看到的是渐隐（像走进雾里），而不是撞墙反弹。
 */
export function edgeFadeAlpha(cameraX: number, viewWidth: number, world: CabinWorld = WORLD): number {
  const toLeft = cameraX;
  const toRight = world.width - (cameraX + viewWidth);
  const nearest = Math.min(toLeft, toRight);
  if (!(world.edgeFadePx > 0)) return 0;
  return clampNum(1 - nearest / world.edgeFadePx, 0, 1) * world.edgeFadeMax;
}

/**
 * G3-3：平铺图层的虚拟宽度。
 *
 * **有意不按 worldWidth*parallax 放大**：TilingSprite 的 addressMode='repeat'
 * 会无限平铺，tilePosition 可以任意大，几何宽度只要盖住视口就永不露缝。
 * 按世界宽放大只会成倍增加填充率，直接冲掉 SRE 的 渲染≤6ms 帧预算
 * （docs/handoff-tasks/13-G1-G5-游戏与美术任务书.md §8），没有任何视觉收益。
 * margin 用于 resize 后的一帧内不留硬边。
 */
export function layerVirtualWidth(viewWidth: number, margin: number): number {
  return viewWidth + Math.max(2, Math.round(margin * 2));
}

/* ------------------------------------------------------------------ */
/* 台词池（预生成台词 · 模型生成待接入）+ 模型接口位                     */
/* ------------------------------------------------------------------ */

export type DialogueSpeaker = 'person' | 'pet';

/**
 * 内置台词池，按「性格」字段挑选。诚实原则：这些是预生成静态台词，
 * 页面必须展示「预生成台词 · 模型生成待接入」角标/说明，不得伪装成模型实时生成。
 */
export const DIALOGUE_LINES: Record<PersonalityId, Record<DialogueSpeaker, readonly string[]>> = {
  lively: {
    person: [
      '今天也是元气满满的一天！',
      '要一起去溪边转转吗？',
      '嘿嘿，我的小屋是不是越来越好看啦？',
      '跑起来！风都是甜的！',
    ],
    pet: [
      '汪汪！我最喜欢你啦！',
      '快看我快看我，尾巴摇出影子了！',
      '新地方！好想闻一闻每个角落！',
      '一起玩球！现在！马上！',
    ],
  },
  cool: {
    person: [
      '……还行。',
      '布局尚可，无需调整。',
      '有话直说，我在听。',
      '静谧挺好，别太吵。',
    ],
    pet: [
      '喵。（瞥了一眼）',
      '……不是想理你，只是恰好路过。',
      '哼，摸可以，限三秒。',
      '（尾巴慢慢晃了一下，算是回应）',
    ],
  },
  melancholy: {
    person: [
      '有时候觉得，小屋也会孤单吧。',
      '风把叶子吹落的时候，我总会想起从前。',
      '今天的天空，有一点像那年秋天。',
      '没关系……我等你，多久都行。',
    ],
    pet: [
      '（望着远方）……你也会离开吗？',
      '雨天的屋檐下，最适合安静地想你。',
      '我把最喜欢的小球埋起来了，怕弄丢。',
      '（轻轻靠过来，什么也没说）',
    ],
  },
  chatty: {
    person: [
      '跟你说，屋顶昨天差一点就换了颜色！',
      '你知道吗，山洞冬暖夏凉，其实很科学！',
      '我数过了，这棵树一共有 247 片叶子！',
      '对了对了，还有一件事，就是……算了见面再说！',
    ],
    pet: [
      '喵呜喵呜喵呜！（翻译：今天发生了三件事）',
      '第一件事，我看见了蝴蝶；第二件事，还是那只蝴蝶；第三件事，算了太长了。',
      '你的鞋带开了——哦你没穿鞋。',
      '我有一个绝妙的主意，虽然还没想好是什么。',
    ],
  },
};

/** 可注入随机源，测试可传 () => 0 做确定性断言。 */
export type RandomSource = () => number;

export function pickDialogueLine(
  speaker: DialogueSpeaker,
  personality: PersonalityId,
  rng: RandomSource = Math.random,
): string {
  const pool = DIALOGUE_LINES[personality][speaker];
  const index = Math.min(pool.length - 1, Math.max(0, Math.floor(rng() * pool.length)));
  return pool[index];
}

/**
 * ★ 模型生成台词接口位（未接入）★
 * 当前默认实现 = 上面预生成台词池。未来接入方式：
 *   1) /api/chat（FROZEN_CONTRACT 内既有端点）：setDialogueProvider(async (speaker, personality) => {
 *        const res = await fetch('/api/chat', { method: 'POST', body: JSON.stringify({ system: cabinPrompt(personality), content: speaker }) });
 *        ... return text;
 *      });
 *   2) 本地 inference 网关：同理替换 provider 即可，渲染层无感（只认 string / Promise<string>）。
 * 接入前页面文案保持「预生成台词 · 模型生成待接入」，不得伪装成模型实时生成。
 */
export type DialogueProvider = (speaker: DialogueSpeaker, personality: PersonalityId) => string | Promise<string>;

let dialogueProvider: DialogueProvider = (speaker, personality) => pickDialogueLine(speaker, personality);

export function getDialogueProvider(): DialogueProvider {
  return dialogueProvider;
}

export function setDialogueProvider(provider: DialogueProvider): void {
  dialogueProvider = provider;
}

/** 统一入口：渲染层只调这里，异步 provider 返回 Promise 时由调用方 await。 */
export function resolveDialogue(speaker: DialogueSpeaker, personality: PersonalityId): string | Promise<string> {
  return dialogueProvider(speaker, personality);
}
