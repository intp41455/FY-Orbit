// Typed client for the W11 个性化像素角色 API (mirrors api/routes/avatar.py).
//
// Honest contract (FROZEN_CONTRACT §11 / 交接总纲铁律 3):
//  - 本文件只做「把后端 JSON 原样搬成 TS 类型 + 发起带 CSRF 的请求」，
//    **不做任何前端兜底生成**：后端没给矩阵，前端绝不自己画一个假的。
//  - 失败由后端错误码驱动（ApiError.code / .status），页面必须原样展示，
//    绝不 catch 之后 return 空对象冒充成功。
//  - 404 有两种完全不同的含义，必须分开处理，不要混为一谈：
//      · GET /me    404 = 你还没生成过小人（正常空态，不是错误）
//      · GET /house 404 = 小屋还没有专属小人（小屋回退默认小人，不是错误）
//    因此提供 isAvatarNotFound() 让页面能区分「空态」与「真故障」。
//  - CSRF/同源/credentials 全部由 client.ts 的 request() 统一处理，此处不重复实现。
import { request } from './client';

// ======================================================================
// 类型（与 avatar_gen.py / avatar_profile.py 的返回结构逐字段对应）
// ======================================================================

/** 8 层独立矩阵：每层 32 行 × 24 列。层间不互相覆盖，前端按层做动效。 */
export type AvatarLayers = Record<string, string[]>;

/**
 * **字符 → #RRGGBB**：前端渲染唯一需要的色板形状。
 *
 * 必须用这个而不是 `palette`：`palette` 的键是**语义名**（skin/hair/rim_light），
 * 而 matrix/layers 里是**单字符键**（s/h/x）。拿语义名去查字符会一个都命中不了，
 * 渲染结果全透明。后端已把映射合成好下发（avatar_gen.char_palette）。
 */
export type AvatarCharPalette = Record<string, string>;

/** 语义色键 → #RRGGBB（引擎内部的调色板，用于配色自检与调试展示）。 */
export type AvatarPalette = Record<string, string>;

/** 字符 → 语义名（引擎的 CHAR_KEYS，供需要语义名的地方使用）。 */
export type AvatarCharKeys = Record<string, string>;

/** 可分享字段白名单（后端 SHARE_BADGE_FIELDS）。白名单外一律 422，不静默忽略。 */
export const SHARE_BADGE_FIELDS = [
  'mbti',
  'element',
  'day_master',
  'sun_sign',
  'moon_sign',
  'asc_sign',
  'name',
] as const;
export type ShareBadgeField = (typeof SHARE_BADGE_FIELDS)[number];

/** 微调滑杆可提交的字段（后端 _apply_overrides 的 allowed 集合）。 */
export const TUNING_FIELDS = [
  'hair_style',
  'hair_tone',
  'outfit',
  'mouth',
  'eye',
  'hue_shift',
] as const;
export type TuningField = (typeof TUNING_FIELDS)[number];

export interface AvatarTuning {
  hair_style?: string;
  hair_tone?: string;
  outfit?: string;
  mouth?: string;
  eye?: string;
  /** 色相偏移，当前以明度偏移近似，后端限定 [-2, 2] 整数。 */
  hue_shift?: number;
}

export interface AvatarParams {
  hair_style: string;
  hair_tone: string;
  palette_id: string;
  eye: string;
  mouth: string;
  accessory: string;
  outfit: string;
  emblem: string;
  texture: string;
  height_scale: number;
  colors: AvatarPalette;
  labels: Record<string, string>;
  /** 每个维度来自哪个画像字段（可回溯）。 */
  sources: Record<string, string>;
  /** 走中性默认的画像字段。 */
  missing: string[];
  fingerprint: string;
  engine_version: string;
}

/**
 * 诚实标注。
 * 三个键（pending / notes / age_band_label）**恒定存在**，页面可无分支读取：
 * 曾经完整态缺 pending，页面会拿到 undefined。
 */
export interface AvatarAdvisory {
  /** 画像是否完整。false 时 pending 非空。 */
  complete: boolean;
  /** 缺项字段名。完整时为空数组（不是 undefined）。 */
  pending: string[];
  /** 「缺项走了中性默认（非你的真实数据）」的说明。 */
  note: string;
  /** 「性别/年龄档已收到但刻意不改剪影」的说明。恒定非空。 */
  notes: string;
  /** 归一化后的年龄档中文标签；未提供/非法时为 null。 */
  age_band_label: string | null;
}

/** 一个完整可渲染的角色包（generate / confirm / me / house 都会带上）。 */
export interface AvatarPackage {
  /** 底稿指纹：谁的画像。微调不改变。 */
  fingerprint: string;
  /** 呈现指纹：现在长什么样。微调会改变。分享卡短码用它。 */
  params_fingerprint: string;
  engine_version: string;
  params: AvatarParams;
  /** AI 底稿签名。永远随包返回，供「还原底稿」比对。 */
  base_signature: Record<string, string>;
  tuned: boolean;
  layers: AvatarLayers;
  /** 8 层合成后的扁平矩阵（渲染与小屋都用这个）。 */
  matrix: string[];
  width: number;
  height: number;
  /** 语义色板（skin/hair/… → hex），用于配色展示与自检。 */
  palette: AvatarPalette;
  /** 字符色板（s/h/x → hex），**前端渲染用这个**。 */
  char_palette: AvatarCharPalette;
  /** 参数空间大小（真实常量推导，≥10^6）。 */
  param_space_size: number;
  advisory: AvatarAdvisory;
}

/** 档案行 = 角色包 + 持久化元数据。 */
export interface AvatarProfile {
  id: string;
  state: 'draft' | 'confirmed';
  owner_id: string;
  portrait: Record<string, unknown>;
  params: AvatarParams;
  base_signature: Record<string, string>;
  overrides: Record<string, string | number> | null;
  fingerprint: string;
  params_fingerprint: string;
  engine_version: string;
  likeness_score: number | null;
  likeness_note: string | null;
  is_house_avatar: boolean;
  /** 乐观锁版本号，confirm 必须回传当前值。 */
  version: number;
  avatar: AvatarPackage;
  advisory: AvatarAdvisory;
  created_at: string;
  updated_at: string;
}

/**
 * 小屋消费端点 `/api/avatar/house` 的真实返回结构 —— **扁平 9 键**，刻意与
 * `AvatarProfile`（含嵌套 `avatar: AvatarPackage`）**不共享**形状。
 *
 * 诚实契约铁律（FROZEN_CONTRACT §11 / 交接总纲铁律 3）：后端 `house_avatar()`
 * （avatar_profile.py:234）只下发「已确认且已设为专属」的那一份的扁平矩阵 / 分层 /
 * 调色板，**没有** `AvatarPackage` 嵌套，也**没有** `params_fingerprint` 字段
 * （只有 `fingerprint`，其值等于呈现指纹）。前端历史上错读成
 * `houseAvatar.avatar.layers` 和 `houseAvatar.params_fingerprint`，导致小屋白屏
 * （G1-1）。因此本类型单独定义，从类型层面杜绝再次误用嵌套字段。
 */
export interface HouseAvatar {
  /** 呈现指纹（= 后端 `row.params_fingerprint`）。短码展示用它。 */
  fingerprint: string;
  /** 8 层独立矩阵（每层 32 行 × 24 列）；小屋只认矩阵，按层做行走动效。 */
  layers: AvatarLayers;
  /** 8 层合成后的扁平矩阵（渲染与小屋都用这个）。 */
  matrix: string[];
  width: number;
  height: number;
  /** 语义色键 → #RRGGBB：引擎内部调色板，前端渲染用 `char_palette`。 */
  palette: AvatarPalette;
  /** 字符 → 语义名（引擎 `CHAR_KEYS`）。后端恒定下发，前端渲染不直接消费。 */
  char_keys: AvatarCharKeys;
  /** 字符 → #RRGGBB：前端渲染唯一需要的色板。 */
  char_palette: AvatarCharPalette;
  /** 参数语义标签（如 day_master / element），供小屋卡片展示。 */
  labels: Record<string, string>;
}

export interface ShareBadge {
  field: ShareBadgeField;
  label: string;
  value: string;
}

export interface ShareCard {
  width: number;
  height: number;
  avatar: {
    matrix: string[];
    layers: AvatarLayers;
    palette: AvatarPalette;
    char_palette: AvatarCharPalette;
    width: number;
    height: number;
  };
  /** 只含用户勾选的项；零勾选就是空数组（默认零隐私泄露）。 */
  badges: ShareBadge[];
  caption: string;
  /** 呈现指纹前 8 位。 */
  fingerprint_short: string;
  /** 底稿指纹前 8 位（用于区分底稿与微调版本）。 */
  base_fingerprint_short: string;
  tuned: boolean;
  brand: { product: string; tagline: string };
  privacy_note: string;
  /** 未勾选、因而上卡不含的字段名。 */
  excluded_fields: string[];
}

// ======================================================================
// 请求
// ======================================================================

/** 画像输入。缺项由引擎走中性默认并在 advisory 里如实标注。 */
export interface PortraitInput {
  mbti?: string;
  bazi_element?: string;
  bazi_day_master?: string;
  sun_sign?: string;
  moon_sign?: string;
  asc_sign?: string;
  name?: string;
  nickname?: string;
  mood?: string;
  gender?: string;
  age_band?: string;
}

/** 后端肖像白名单（与 avatar_profile.PORTRAIT_FIELDS 对应）。 */
const PORTRAIT_WHITELIST = [
  'mbti',
  'bazi_element',
  'bazi_day_master',
  'sun_sign',
  'moon_sign',
  'asc_sign',
  'name',
  'nickname',
  'mood',
  'gender',
  'age_band',
] as const;

/**
 * 显式白名单过滤后再发请求。
 *
 * 为什么必须过滤而不是原样透传：`portrait` 是 `Record<string, unknown>` 形状的
 * 用户输入，若某个调用方（或后续改动）顺手把 owner_id / role 之类字段塞进来，
 * 原样透传就等于**主动**把越权字段发出去。后端 `extra="forbid"` 会 422 挡住，
 * 但那时请求已经带着身份字段飞了一圈 —— 客户端自己就不该发。
 */
function whitelistPortrait(portrait: PortraitInput): PortraitInput {
  const out: Record<string, string> = {};
  for (const key of PORTRAIT_WHITELIST) {
    const v = (portrait as Record<string, unknown>)[key];
    if (typeof v === 'string' && v.length > 0) out[key] = v;
  }
  return out as PortraitInput;
}

/** 微调白名单过滤（同上：只发后端允许的字段与合法取值）。 */
function whitelistTuning(tuning?: AvatarTuning): AvatarTuning | null {
  if (!tuning) return null;
  const out: Record<string, string | number> = {};
  for (const key of TUNING_FIELDS) {
    const v = (tuning as Record<string, unknown>)[key];
    if (key === 'hue_shift') {
      if (typeof v === 'number' && Number.isFinite(v)) out.hue_shift = v;
      continue;
    }
    if (typeof v === 'string' && v.length > 0) out[key] = v;
  }
  return out as AvatarTuning;
}

/**
 * POST /api/avatar/generate —— 画像 → 角色包（落草稿档案，重复调用是 upsert）。
 *
 * 注意：请求体里塞 owner_id 会被后端 422 拒绝（extra="forbid"），
 * owner 只从认证层取 —— 前端**从不**传 owner_id。
 */
export function generateAvatar(
  portrait: PortraitInput,
  overrides?: AvatarTuning,
): Promise<AvatarProfile> {
  return request<AvatarProfile>('/api/avatar/generate', {
    method: 'POST',
    body: { portrait: whitelistPortrait(portrait), overrides: whitelistTuning(overrides) },
  });
}

/**
 * PUT /api/avatar/confirm —— 草稿 → 已确认，并可记录自评与设为小屋专属小人。
 *
 * expectedVersion 必须等于当前档案 version，否则后端 409（乐观锁）。
 */
export function confirmAvatar(input: {
  expectedVersion: number;
  likenessScore?: number | null;
  likenessNote?: string | null;
  isHouseAvatar?: boolean;
}): Promise<AvatarProfile> {
  return request<AvatarProfile>('/api/avatar/confirm', {
    method: 'PUT',
    body: {
      expected_version: input.expectedVersion,
      likeness_score: input.likenessScore ?? null,
      likeness_note: input.likenessNote ?? null,
      is_house_avatar: input.isHouseAvatar ?? true,
    },
  });
}

/**
 * GET /api/avatar/me —— 读当前档案。
 * **未生成过时后端返回 404**，这是正常空态，调用方须用 isAvatarNotFound 区分，
 * 不得当成故障弹错误条。
 */
export function getMyAvatar(): Promise<AvatarProfile> {
  return request<AvatarProfile>('/api/avatar/me');
}

/**
 * POST /api/avatar/share-card —— 生成分享卡渲染数据。
 * 必须已确认（草稿后端会拒绝），且只含 badges 里勾选的字段。
 */
export function createShareCard(input: {
  badges: ShareBadgeField[];
  displayName?: string | null;
  overrides?: AvatarTuning;
}): Promise<ShareCard> {
  return request<ShareCard>('/api/avatar/share-card', {
    method: 'POST',
    body: {
      badges: input.badges.filter((b): b is ShareBadgeField =>
        (SHARE_BADGE_FIELDS as readonly string[]).includes(b)),
      display_name: input.displayName ?? null,
      overrides: whitelistTuning(input.overrides),
    },
  });
}

/**
 * GET /api/avatar/house —— 小屋消费端点。
 * 只有 confirmed 且 is_house_avatar 的档案才会被返回；其余一律 404。
 * 小屋在 404 时**回退默认小人**即可，不该阻塞小屋渲染。
 */
export function getHouseAvatar(): Promise<HouseAvatar> {
  return request<HouseAvatar>('/api/avatar/house');
}

// ======================================================================
// 错误语义辅助
// ======================================================================

/**
 * 判断一个异常是否为「还没有专属小人」的正常空态（后端 404）。
 *
 * 与 ApiError 的其它错误严格区分：
 *  · 404 → 空态，回退默认 / 显示引导；
 *  · 401/403 → 真的没登录 / 被拒，必须提示；
 *  · 409 → 版本冲突，需要重新拉取；
 *  · 422 → 请求不合法（例如白名单外徽章），必须原样展示 code。
 */
export function isAvatarNotFound(err: unknown): boolean {
  return typeof err === 'object' && err !== null && (err as { status?: number }).status === 404;
}

/** 取后端错误码（统一信封 error.code），取不到返回 null。 */
export function avatarErrorCode(err: unknown): string | null {
  if (typeof err !== 'object' || err === null) return null;
  const body = (err as { body?: { code?: unknown } }).body;
  const code = body?.code;
  return typeof code === 'string' ? code : null;
}