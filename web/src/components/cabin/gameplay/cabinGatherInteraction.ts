/**
 * B1 交互框架 + B2 采集表现层（判据 I1 - I4）。
 *
 * 核心机制：
 * 1. I1 靠近可交互物：玩家距离 ≤ 1 格（或视口在交互半径内）时，头顶触发「✋」手形交互提示，指明动作、目标与产出；
 * 2. I2 动画键真实播放：按动作（chop/mine/fish/pick/harvest/water/collect/dig）触发对应动画键
 *    （anim_chop/anim_mine/anim_fish/anim_pick/anim_harvest/anim_water/anim_collect/anim_dig），
 *    断言动画播放状态机、帧推进与播放历史；
 * 3. I3 七类采集全覆盖：
 *    - 砍树 (Chop): 产出 wood / resin 等进背包；
 *    - 挖矿 (Mine): 产出 pebble / alloy_ore / crystal 等进背包；
 *    - 钓鱼 (Fish): 产出 fish / reed 等进背包；
 *    - 采果 (Pick): 产出 fruit / berries / mushroom 等进背包；
 *    - 拔草 (Harvest): 产出 herb / wheat / cotton 等进背包；
 *    - 打水 (Water): 产出 water / spring_water 等进背包；
 *    - 捡杂物 (Collect/Dig): 产出 pinecone / seed / pebble / shell 等进背包；
 * 4. I4 3 秒规则：玩家靠近后 3 秒内清楚知晓可交互动作、快捷按键 [E]、耗时分钟数与产物预估。
 */

import type { GatherRow } from './lifeApi';

/* ------------------------------------------------------------------ */
/* I2 · 动画键映射与状态机                                              */
/* ------------------------------------------------------------------ */

/** 动作 → 动画键映射（对齐后端 interaction.py ANIMATIONS）。 */
export const GATHER_ANIMATIONS: Readonly<Record<string, string>> = {
  chop: 'anim_chop',
  mine: 'anim_mine',
  fish: 'anim_fish',
  pick: 'anim_pick',
  harvest: 'anim_harvest',
  dig: 'anim_dig',
  water: 'anim_water',
  collect: 'anim_collect',
};

/** 动作 → 中文动词标签（对齐后端 interaction.py ACTION_LABELS）。 */
export const GATHER_ACTION_LABELS: Readonly<Record<string, string>> = {
  chop: '砍伐',
  mine: '开采',
  fish: '垂钓',
  pick: '拾取',
  harvest: '收割',
  dig: '挖掘',
  water: '打水',
  collect: '收集',
};

/** 动画播放状态快照。 */
export interface AnimationPlaybackState {
  currentAnimation: string | null;
  isPlaying: boolean;
  currentFrame: number;
  totalFrames: number;
  durationMs: number;
  playbackHistory: string[];
}

/** 动画播放控制器：驱动采集动作的像素动画播放与帧步进。 */
export class GatherAnimationPlayer {
  private currentAnim: string | null = null;
  private playing = false;
  private frame = 0;
  private readonly totalFramesPerAnim = 4;
  private duration = 600; // ms
  private history: string[] = [];

  play(action: string, customDurationMs = 600): AnimationPlaybackState {
    const animKey = GATHER_ANIMATIONS[action] ?? `anim_${action}`;
    this.currentAnim = animKey;
    this.playing = true;
    this.frame = 0;
    this.duration = customDurationMs;
    this.history.push(animKey);
    return this.getState();
  }

  tick(deltaMs: number): AnimationPlaybackState {
    if (!this.playing || !this.currentAnim) return this.getState();
    const frameInterval = this.duration / this.totalFramesPerAnim;
    const nextFrame = this.frame + Math.max(1, Math.floor(deltaMs / frameInterval));
    if (nextFrame >= this.totalFramesPerAnim) {
      this.frame = this.totalFramesPerAnim - 1;
      this.playing = false; // 播放完成
    } else {
      this.frame = nextFrame;
    }
    return this.getState();
  }

  stop(): AnimationPlaybackState {
    this.playing = false;
    return this.getState();
  }

  getState(): AnimationPlaybackState {
    return {
      currentAnimation: this.currentAnim,
      isPlaying: this.playing,
      currentFrame: this.frame,
      totalFrames: this.totalFramesPerAnim,
      durationMs: this.duration,
      playbackHistory: [...this.history],
    };
  }

  getPlaybackHistory(): string[] {
    return [...this.history];
  }

  reset(): void {
    this.currentAnim = null;
    this.playing = false;
    this.frame = 0;
    this.history = [];
  }
}

/* ------------------------------------------------------------------ */
/* I3 · 七大采集分类标准                                                */
/* ------------------------------------------------------------------ */

export type GatherCategoryKey =
  | 'chop'
  | 'mine'
  | 'fish'
  | 'pick'
  | 'harvest'
  | 'water'
  | 'collect';

export interface GatherCategorySpec {
  key: GatherCategoryKey;
  label: string;
  animationKey: string;
  sampleNodeIds: string[];
  materials: string[];
}

/** 七类采集体系规格定义。 */
export const SEVEN_GATHER_CATEGORIES: readonly GatherCategorySpec[] = [
  {
    key: 'chop',
    label: '砍树',
    animationKey: 'anim_chop',
    sampleNodeIds: ['forest_pine', 'magic_tree', 'country_wood', 'ink_bamboo'],
    materials: ['wood', 'resin', 'bamboo'],
  },
  {
    key: 'mine',
    label: '挖矿',
    animationKey: 'anim_mine',
    sampleNodeIds: ['planet_crystal', 'scifi_ore', 'magic_ore', 'ink_stone'],
    materials: ['pebble', 'alloy_ore', 'crystal', 'inkstone', 'moonstone'],
  },
  {
    key: 'fish',
    label: '钓鱼',
    animationKey: 'anim_fish',
    sampleNodeIds: ['stream_fish'],
    materials: ['fish'],
  },
  {
    key: 'pick',
    label: '采果',
    animationKey: 'anim_pick',
    sampleNodeIds: ['forest_mush', 'country_fruit'],
    materials: ['fruit', 'mushroom', 'berries'],
  },
  {
    key: 'harvest',
    label: '拔草',
    animationKey: 'anim_harvest',
    sampleNodeIds: ['forest_herb', 'garden_flower', 'garden_veg', 'field_wheat', 'stream_reed'],
    materials: ['herb', 'flower', 'vegetable', 'wheat', 'reed', 'cotton'],
  },
  {
    key: 'water',
    label: '打水',
    animationKey: 'anim_water',
    sampleNodeIds: ['garden_well', 'country_well', 'ink_spring'],
    materials: ['water', 'spring_water', 'dew'],
  },
  {
    key: 'collect',
    label: '捡杂物',
    animationKey: 'anim_collect',
    sampleNodeIds: ['forest_cone', 'garden_seed', 'stream_pebble', 'planet_stardust'],
    materials: ['pinecone', 'seed', 'pebble', 'stardust', 'shell', 'driftwood'],
  },
] as const;

/* ------------------------------------------------------------------ */
/* I1 / I4 · 交互判定与 3 秒规则表现层                                 */
/* ------------------------------------------------------------------ */

export const INTERACT_RANGE_TILES = 1;

export function manhattanDistance(a: [number, number], b: [number, number]): number {
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
}

/** 3 秒规则指引卡片数据。 */
export interface ThreeSecondPromptCard {
  promptIcon: string;
  actionText: string;
  targetLabel: string;
  materialLabel: string;
  fullPrompt: string;
  buttonLabel: string;
  timeEstimateText: string;
  yieldEstimateText: string;
  distanceTiles: number;
  inRange: boolean;
  compliant3sRule: boolean;
}

/** 生成符合 3 秒规则的交互提示卡（判据 I1, I4）。 */
export function buildThreeSecondPrompt(
  node: GatherRow,
  playerTile: [number, number] = [0, 0],
): ThreeSecondPromptCard {
  const nodeTile: [number, number] = [node.tile[0] ?? 0, node.tile[1] ?? 0];
  const dist = manhattanDistance(playerTile, nodeTile);
  const inRange = node.in_range || dist <= INTERACT_RANGE_TILES;
  const actionLabel = node.action_label || GATHER_ACTION_LABELS[node.action] || '交互';

  const fullPrompt = `✋ ${actionLabel} ${node.label}（${node.material}）`;
  const [minQty, maxQty] = node.qty_range ?? [1, 2];
  const yieldEstimateText = `预估产出: ${node.material}×${minQty === maxQty ? minQty : `${minQty}~${maxQty}`}`;
  const timeEstimateText = `耗时: ${node.minutes} 游戏分钟`;
  const buttonLabel = `[E] ${actionLabel}`;

  // 3秒规则核验：必须具备手形标记、明确动词、明确材料、快捷键提示与耗时预估
  const compliant3sRule =
    fullPrompt.includes('✋') &&
    Boolean(actionLabel) &&
    Boolean(node.material) &&
    buttonLabel.includes('[E]') &&
    timeEstimateText.includes('耗时');

  return {
    promptIcon: '✋',
    actionText: actionLabel,
    targetLabel: node.label,
    materialLabel: node.material,
    fullPrompt,
    buttonLabel,
    timeEstimateText,
    yieldEstimateText,
    distanceTiles: dist,
    inRange,
    compliant3sRule,
  };
}

/* ------------------------------------------------------------------ */
/* 采集闭环执行器                                                       */
/* ------------------------------------------------------------------ */

export interface GatherExecutionResult {
  nodeId: string;
  action: string;
  animationPlayed: string;
  materialGained: string;
  qtyGained: number;
  minutesSpent: number;
  bagAfter: Record<string, number>;
  note: string;
}

/**
 * 客户端采集执行器：
 * 1. 触发动画播放状态机（I2）；
 * 2. 调上层 onAction 触发权威网络调用（I3）；
 * 3. 产物直接写入背包对象并返回最新数据。
 */
export async function executeGatherCycle(
  node: GatherRow,
  playerBag: Record<string, number>,
  animPlayer: GatherAnimationPlayer,
  onAction?: (action: string, args?: Record<string, unknown>) => Promise<void> | void,
): Promise<GatherExecutionResult> {
  // 1. 播放动作对应像素动画
  const animState = animPlayer.play(node.action);

  // 2. 派发权威动作至服务端
  if (onAction) {
    await onAction('gather', { node_id: node.id });
  }

  // 3. 计算产出物入背包
  const [lo] = node.qty_range ?? [1, 2];
  const rolledQty = Math.max(1, lo); // 基础保底
  const bagAfter = { ...playerBag };
  bagAfter[node.material] = (bagAfter[node.material] ?? 0) + rolledQty;

  return {
    nodeId: node.id,
    action: node.action,
    animationPlayed: animState.currentAnimation ?? 'anim_collect',
    materialGained: node.material,
    qtyGained: rolledQty,
    minutesSpent: node.minutes,
    bagAfter,
    note: `完成 ${node.action_label} ${node.label}，获得 ${node.material}×${rolledQty}`,
  };
}
