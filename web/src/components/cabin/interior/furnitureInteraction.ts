import { getFurniture } from './furnitureCatalog';
import type { FurnitureInteract } from './furnitureCatalog';
import type { InteriorItem } from './interiorLayout';

/**
 * W1 · 家具交互台词与反馈（纯逻辑，可测）
 *
 * 诚实原则：这些是**预生成台词池**（与 cabinConfig.DIALOGUE_LINES 同一性质），
 * 页面必须标注来源，不得伪装成模型实时生成。真正的模型化交互留给 W2。
 */

/** 书架台词：点一下报一个书名，制造「家里有生活」的密度感。 */
const BOOK_TITLES: readonly string[] = [
  '《星际旅人食谱》',
  '《窗台植物养护手册》',
  '《像素地图考》',
  '《今天也好好吃饭》',
  '《木屋建造一百问》',
  '《会发光的角落》',
  '《雨天的第六个理由》',
  '《旧家具修复笔记》',
  '《云上的房间》',
  '《把光留住》',
];

/** 鱼缸台词。 */
const TANK_LINES: readonly string[] = [
  '小鱼游过去又游回来了。',
  '水草轻轻晃，像在打招呼。',
  '看一会儿鱼，时间会变慢。',
];

/** 盆栽 / 猫窝 / 地毯等治愈系。 */
const COZY_LINES: readonly string[] = [
  '待在这里真舒服。',
  '这个角落我喜欢。',
  '要是每天都能这样就好了。',
];

const SLEEP_LINES: readonly string[] = ['晚安…', '我先睡一会儿。', '灯留着，你随意。'];
const LAMP_LINES: readonly string[] = ['灯一开，屋子就暖了。', '影子被拉得长长的。'];
const SIT_LINES: readonly string[] = ['坐一会儿吧。', '歇会儿再忙。'];

/**
 * 更衣镜（W11 角色工坊入口）提示语。
 * 诚实说明：W11 的「角色工坊」页尚未落地，这里**只提示入口、不假装能换装**——
 * 真正的换装由 W11 完成后调用 `onOpenAvatarWorkshop` 打开工坊。
 */
export const MIRROR_LINES: readonly string[] = [
  '照一照镜子（角色工坊施工中）。',
  '镜子里那个人是你。（角色工坊施工中）',
];

/** 睡眠演出的持续时长（毫秒），供页面锁定交互态。 */
export const SLEEP_DURATION_MS = 2600;

export interface FurnitureInteraction {
  /** 交给场景层播放的演出。 */
  readonly kind: 'sleep' | 'say';
  /** kind='say' 时的气泡文本。 */
  readonly text: string;
}

export type RandomSource = () => number;

function pick(pool: readonly string[], rng: RandomSource): string {
  const i = Math.min(pool.length - 1, Math.max(0, Math.floor(rng() * pool.length)));
  return pool[i] ?? '';
}

/**
 * 解析一次家具点击 → 演出指令。
 *
 * 未注册家具或 `interact='none'` 返回 `null`：调用方应给出「这里没什么可互动」
 * 的轻提示或直接忽略，**不得凭空演出一个假的互动效果**。
 */
export function resolveFurnitureInteraction(
  item: InteriorItem,
  rng: RandomSource = Math.random,
): FurnitureInteraction | null {
  const def = getFurniture(item.furnitureId);
  if (!def) return null;
  const kind: FurnitureInteract = def.interact;
  switch (kind) {
    case 'sleep':
      return { kind: 'sleep', text: pick(SLEEP_LINES, rng) };
    case 'read':
      return { kind: 'say', text: pick(BOOK_TITLES, rng) };
    case 'water':
      return { kind: 'say', text: pick(TANK_LINES, rng) };
    case 'cozy':
      return { kind: 'say', text: pick(COZY_LINES, rng) };
    case 'lamp':
      return { kind: 'say', text: pick(LAMP_LINES, rng) };
    case 'sit':
      return { kind: 'say', text: pick(SIT_LINES, rng) };
    case 'mirror':
      // 只提示入口，不假装已能换装（W11 未落地）。
      return { kind: 'say', text: pick(MIRROR_LINES, rng) };
    case 'none':
    default:
      return null;
  }
}

/** 落位成功时的即时反馈文案（≤100ms 呈现，蔚蓝式手感）。 */
export const PLACE_FEEDBACK_LINES: readonly string[] = [
  '就放这儿吧。',
  '这个位置不错。',
  '嗯，看着就舒服。',
  '小屋又好看了一点。',
];

/** 该家具是否有可交互行为（家具栏/详情可据此显示「可互动」标记）。 */
export function isInteractive(furnitureId: string): boolean {
  const def = getFurniture(furnitureId);
  return Boolean(def && def.interact !== 'none');
}
