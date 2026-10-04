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

export type CabinBackgroundId = 'forest' | 'garden' | 'stream' | 'field' | 'planet';

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
}

export const DEFAULT_CABIN_CONFIG: CabinConfig = {
  house: 'cabin',
  background: 'forest',
  petColor: 'sky',
  petPersonality: 'lively',
  personPersonality: 'chatty',
  personName: '小寻',
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
  const name = typeof r.personName === 'string' ? r.personName.trim().slice(0, 16) : '';
  return {
    house: pickWhitelisted(r.house, houseIds, DEFAULT_CABIN_CONFIG.house),
    background: pickWhitelisted(r.background, bgIds, DEFAULT_CABIN_CONFIG.background),
    petColor: pickWhitelisted(r.petColor, colorIds, DEFAULT_CABIN_CONFIG.petColor),
    petPersonality: pickWhitelisted(r.petPersonality, personalityIds, DEFAULT_CABIN_CONFIG.petPersonality),
    personPersonality: pickWhitelisted(r.personPersonality, personalityIds, DEFAULT_CABIN_CONFIG.personPersonality),
    personName: name || DEFAULT_CABIN_CONFIG.personName,
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
