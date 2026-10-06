/**
 * 数码小屋（P2）全地图专属手绘 NPC 体系与交互系统（看板任务 T5.1, T5.2）。
 *
 * 彻底解决“各大地图没有 NPC，每个地图应该有不一样的专属手绘 NPC”的用户诉求。
 *
 * 5 大地图专属手绘 NPC：
 * 1. 【老林子 forest】：【守林隐士 · 青松老者】（身披草蓑，手持木杖，讲述森林古老传说与草药知识）
 * 2. 【后花园 garden】：【花艺精灵少女 · 洛丽塔/艾莉】（头戴向日葵草帽，身穿围裙，教导庭院园艺与花语）
 * 3. 【黄金田野 golden_field】：【麦田守望者 · 活泼稻草人 / 丰收老农】（头戴尖顶帽，微风中轻晃打招呼，讲述节气农谚）
 * 4. 【溪水边 stream】：【碧水钓叟 · 渔隐客】（坐在码头木墩上甩杆，评定渔获品级与传授垂钓技巧）
 * 5. 【观星台 observatory】：【观星学者 · 塞莱斯特】（深蓝星纹法袍，解析黄道十二宫与心境星盘）
 *
 * 系统功能：
 * - 专属外观描述与手绘像素字符画矩阵（可无缝转换为纹理渲染）；
 * - 微表情台词池（按心情表情、时段、好感心数动态解析）；
 * - 好感度系统（0-10心，0-1000点数）与日常多样互动（闲聊、请教传授、致意）；
 * - 每日赠礼系统（喜好、厌恶、普通礼物阶梯好感与独家答谢台词，每日防刷限额）；
 * - 本地持久化与生命快照 NpcRow 格式双向互转。
 */

import type { CanonicalThemeId } from './cabinThemedWorlds';
import type { TimeOfDay } from './cabinConfig';
import type { NpcRow } from './gameplay/lifeApi';
import type { PixelPalette } from './cabinPixels';

export const NPC_STORAGE_KEY = 'fy.cabin.npc_system.v1';
export const MAX_HEARTS = 10;
export const POINTS_PER_HEART = 100;
export const MAX_GIFTS_PER_DAY = 3;

/* ------------------------------------------------------------------ */
/* 微表情定义                                                         */
/* ------------------------------------------------------------------ */

export type MicroExpression =
  | 'smile'   // 亲切微笑
  | 'ponder'  // 抚须/轻抚额头沉思
  | 'joy'     // 开心欢畅
  | 'teach'   // 传授点拨
  | 'care'    // 关怀叮咛
  | 'mystic'  // 神秘莫测
  | 'greet';  // 热情打招呼

export interface ExpressionDialoguePool {
  expression: MicroExpression;
  label: string;
  emoji: string;
  lines: readonly string[];
}

/* ------------------------------------------------------------------ */
/* 赠礼喜好定义                                                       */
/* ------------------------------------------------------------------ */

export interface NpcGiftReaction {
  loved: readonly string[];     // 最爱：+50 点
  liked: readonly string[];     // 喜欢：+35 点
  disliked: readonly string[];  // 厌恶：+5 点
  thankLines: {
    loved: string;
    liked: string;
    neutral: string;
    disliked: string;
  };
}

/* ------------------------------------------------------------------ */
/* 专属 NPC 完整原型规范                                               */
/* ------------------------------------------------------------------ */

export interface ExclusiveNpcMeta {
  id: string;
  name: string;
  title: string;
  role: string;
  themeId: CanonicalThemeId;
  themeLabel: string;
  avatarIcon: string;
  /** 外观细腻手绘描述 */
  appearanceDescription: string;
  /** 背景身世与专精知识 */
  loreAndKnowledge: string;
  /** 性格标签 */
  personalityTags: readonly string[];
  /** 默认场景站位（世界坐标虚拟 px / 地面深度） */
  defaultSpawn: { worldX: number; depth: number };
  /** 昼夜分时问候 */
  timeGreetings: Record<TimeOfDay, string>;
  /** 微表情台词池（多心境动态挑选） */
  microExpressions: Record<MicroExpression, ExpressionDialoguePool>;
  /** 好感心数（0-10心）专属阶段心声 */
  heartMilestones: readonly string[];
  /** 赠礼机制与反应台词 */
  gifts: NpcGiftReaction;
  /** 手绘像素矩阵（24行×16列）与配色板 */
  pixelFrames: readonly (readonly string[])[];
  pixelPalette: PixelPalette;
}

/* ------------------------------------------------------------------ */
/* 交互状态（持久化存储）                                              */
/* ------------------------------------------------------------------ */

export interface NpcState {
  npcId: string;
  points: number;       // 0..1000
  hearts: number;       // 0..10
  giftsToday: number;   // 当日已送次数
  lastGiftDay: number;  // 记录赠礼日期天数
  interactCount: number;
}

/* ------------------------------------------------------------------ */
/* 1. 【守林隐士 · 青松老者】像素图与元数据                           */
/* ------------------------------------------------------------------ */

const PINE_ELDER_PALETTE: PixelPalette = {
  '.': 0x00000000,
  H: 0x4e342e, // 深褐斗笠
  h: 0x795548, // 浅褐斗笠
  W: 0xf5f5f5, // 白发白眉白须
  w: 0xe0e0e0, // 须发阴影
  S: 0xffcc80, // 面部肤色
  E: 0x2e7d32, // 深草绿蓑衣
  e: 0x4caf50, // 亮绿草叶
  T: 0x3e2723, // 枯木法杖
  G: 0xd7ccc8, // 灰布裤
  B: 0x263238, // 麻编草鞋
  O: 0xff9800, // 腰间老葫芦
};

const PINE_ELDER_FRAME_1: readonly string[] = [
  '.....hhhhhh.....',
  '....hHHHHHHh....',
  '..hhHHHHHHHHhh..',
  '.hHHHHHHHHHHHHh.',
  '..hhhhhhhhhhhh..',
  '....WWSSSSWW....',
  '....WWS..SWW....',
  '....WWSS..WW....',
  '....WWWWWWWW....',
  '....WWWWWWWW....',
  '...eeEEEEEEeeT..',
  '..eEEEEEEEEEEeT.',
  '..eEEEEOEEEEEeTT',
  '..eEEEEEEEEEEe.T',
  '..eEEEEEEEEEEe.T',
  '..eEEEEEEEEEEe.T',
  '...EEEEEEEEEE..T',
  '....GG....GG...T',
  '....GG....GG...T',
  '....GG....GG...T',
  '....GG....GG...T',
  '....BB....BB....',
  '....BB....BB....',
  '................',
];

const PINE_ELDER_FRAME_2: readonly string[] = [
  '.....hhhhhh.....',
  '....hHHHHHHh....',
  '..hhHHHHHHHHhh..',
  '.hHHHHHHHHHHHHh.',
  '..hhhhhhhhhhhh..',
  '....WWSSSSWW....',
  '....WWS..SWW....',
  '....WWSS..WW....',
  '....WWWWWWWW....',
  '....WWWWWWWW....',
  '...eeEEEEEEee..T',
  '..eEEEEEEEEEEe.T',
  '..eEEEEOEEEEEeTT',
  '..eEEEEEEEEEEeTT',
  '..eEEEEEEEEEEe.T',
  '..eEEEEEEEEEEe.T',
  '...EEEEEEEEEE..T',
  '.....GG..GG....T',
  '.....GG..GG....T',
  '.....GG..GG....T',
  '.....GG..GG....T',
  '....BB....BB....',
  '....BB....BB....',
  '................',
];

/* ------------------------------------------------------------------ */
/* 2. 【花艺精灵少女 · 洛丽塔/艾莉】像素图与元数据                     */
/* ------------------------------------------------------------------ */

const FAIRY_ELLIE_PALETTE: PixelPalette = {
  '.': 0x00000000,
  Y: 0xfbc02d, // 向日葵金黄草帽
  y: 0xfff176, // 草帽浅金亮边
  G: 0xffd54f, // 浅金双麻花辫
  S: 0xffe0b2, // 少女温润肤色
  E: 0x42a5f5, // 灵动水蓝眼眸
  C: 0xf8bbd0, // 樱粉发带与两颊红晕
  W: 0xffffff, // 纯白蕾丝围裙
  P: 0xba68c8, // 紫丁香轻纱裙摆
  B: 0x8d6e63, // 手提编织花篮
  R: 0xe91e63, // 篮中红玫瑰花蕊
  K: 0x5d4037, // 玛丽珍小皮鞋
};

const FAIRY_ELLIE_FRAME_1: readonly string[] = [
  '....yyyyyyyy....',
  '..yyYYYYYYYYyy..',
  '.yYYYYYCCYYYYYy.',
  '..yyyyyyyyyyyy..',
  '...GGSSSSSSGG...',
  '...GGSE..ESGG...',
  '...GGSC..CSGG...',
  '...GGSSSSSSGG...',
  '....GSSSSSSG....',
  '....G.WWWW.G....',
  '...GGWWWWWWGG.B.',
  '..GG.WWWWWW.GGBR',
  '..G..WWWWWW..GBR',
  '.....PPPPPP...B.',
  '....PPPPPPPP....',
  '...PPPPPPPPPP...',
  '..PPPPPPPPPPPP..',
  '.....SS..SS.....',
  '.....SS..SS.....',
  '.....SS..SS.....',
  '.....SS..SS.....',
  '.....KK..KK.....',
  '.....KK..KK.....',
  '................',
];

const FAIRY_ELLIE_FRAME_2: readonly string[] = [
  '....yyyyyyyy....',
  '..yyYYYYYYYYyy..',
  '.yYYYYYCCYYYYYy.',
  '..yyyyyyyyyyyy..',
  '...GGSSSSSSGG...',
  '...GGSE..ESGG...',
  '...GGSC..CSGG...',
  '...GGSSSSSSGG...',
  '....GSSSSSSG....',
  '....G.WWWW.G....',
  '...GGWWWWWWGG...',
  '..GG.WWWWWW.GG..',
  '..G..WWWWWW..G.B',
  '.....PPPPPP...BR',
  '....PPPPPPPP..BR',
  '...PPPPPPPPPP..B',
  '..PPPPPPPPPPPP..',
  '....SS....SS....',
  '....SS....SS....',
  '....SS....SS....',
  '....SS....SS....',
  '....KK....KK....',
  '....KK....KK....',
  '................',
];

/* ------------------------------------------------------------------ */
/* 3. 【麦田守望者 · 丰收老农】像素图与元数据                         */
/* ------------------------------------------------------------------ */

const FARMER_PALETTE: PixelPalette = {
  '.': 0x00000000,
  H: 0xe65100, // 尖顶秋色草帽
  h: 0xffb74d, // 草帽麦秆色
  S: 0xffcc80, // 健康日晒肤色
  E: 0x3e2723, // 憨厚眼眸
  C: 0xd32f2f, // 暖红格子棉衬衫
  c: 0xffcdd2, // 衬衫白格线
  O: 0x1976d2, // 工装背带裤
  o: 0x1565c0, // 背带裤深色
  F: 0x8d6e63, // 节气木草叉木柄
  f: 0xb0bec5, // 铁草叉齿
  B: 0x5d4037, // 厚实泥工长靴
};

const FARMER_FRAME_1: readonly string[] = [
  '.......hh.......',
  '......hHHh......',
  '.....hHHHHh.....',
  '....hHHHHHHh....',
  '...hHHHHHHHHh...',
  '..hhhhhhhhhhhh..',
  '....SSSSSSSS....',
  '....SE....ES....',
  '....SSSSSSSS....',
  '....SS.SS.SS....',
  '...CcCOOOOcC.fff',
  '..CcCCOOOOCCcf.f',
  '..CcCCOOOOCCc.F.',
  '..CcCCOOOOCCc.F.',
  '..CcCOOOOOOcC.F.',
  '....OOOOOOOO..F.',
  '....OOOOOOOO..F.',
  '....OOOOOOOO..F.',
  '....Oo....oO..F.',
  '....Oo....oO..F.',
  '....Oo....oO....',
  '....BB....BB....',
  '....BB....BB....',
  '................',
];

const FARMER_FRAME_2: readonly string[] = [
  '.......hh.......',
  '......hHHh......',
  '.....hHHHHh.....',
  '....hHHHHHHh....',
  '...hHHHHHHHHh...',
  '..hhhhhhhhhhhh..',
  '....SSSSSSSS....',
  '....SE....ES....',
  '....SSSSSSSS....',
  '....SS.SS.SS....',
  '...CcCOOOOcC..ff',
  '..CcCCOOOOCCc.ff',
  '..CcCCOOOOCCc.F.',
  '..CcCCOOOOCCc.F.',
  '..CcCOOOOOOcC.F.',
  '....OOOOOOOO..F.',
  '....OOOOOOOO..F.',
  '....OOOOOOOO..F.',
  '.....Oo..oO...F.',
  '.....Oo..oO...F.',
  '.....Oo..oO.....',
  '....BB....BB....',
  '....BB....BB....',
  '................',
];

/* ------------------------------------------------------------------ */
/* 4. 【碧水钓叟 · 渔隐客】像素图与元数据                             */
/* ------------------------------------------------------------------ */

const FISHERMAN_PALETTE: PixelPalette = {
  '.': 0x00000000,
  H: 0x546e7a, // 青灰箬笠
  h: 0x78909c, // 箬笠边
  S: 0xffcc80, // 风霜红润面庞
  E: 0x37474f, // 专注睿智的眼
  W: 0xeeeeee, // 白须白眉
  R: 0x607d8b, // 青蓑衣
  r: 0x455a64, // 蓑衣暗纹
  F: 0x8d6e63, // 细竹钓竿
  L: 0x81d4fa, // 钓线
  B: 0x3e2723, // 码头木墩
  K: 0x37474f, // 竹芒鞋
};

const FISHERMAN_FRAME_1: readonly string[] = [
  '.....hhhhhh.....',
  '....hHHHHHHh....',
  '..hhHHHHHHHHhh..',
  '.hHHHHHHHHHHHHh.',
  '..hhhhhhhhhhhh..',
  '....WWSSSSWW....',
  '....WSE..ESW....',
  '....WWSSSSWW....',
  '....WWWWWWWW....',
  '...rrRRRRRRrr.F.',
  '..rRRRRRRRRRRr.F',
  '..rRRRRRRRRRRr.F',
  '..rRRRRRRRRRRr.F',
  '..rRRRRRRRRRRr.F',
  '...RRRRRRRRRR..F',
  '...BBBBBBBBBB.F.',
  '..BBBBBBBBBBBBF.',
  '..BBBBBBBBBBBBF.',
  '..BBBBBBBBBBBB.L',
  '...BBBBBBBBBB..L',
  '....KK....KK...L',
  '....KK....KK...L',
  '...............L',
  '................',
];

const FISHERMAN_FRAME_2: readonly string[] = [
  '.....hhhhhh.....',
  '....hHHHHHHh....',
  '..hhHHHHHHHHhh..',
  '.hHHHHHHHHHHHHh.',
  '..hhhhhhhhhhhh..',
  '....WWSSSSWW....',
  '....WSE..ESW....',
  '....WWSSSSWW....',
  '....WWWWWWWW....',
  '...rrRRRRRRrr..F',
  '..rRRRRRRRRRRr.F',
  '..rRRRRRRRRRRr.F',
  '..rRRRRRRRRRRr.F',
  '..rRRRRRRRRRRr.F',
  '...RRRRRRRRRR.F.',
  '...BBBBBBBBBB.F.',
  '..BBBBBBBBBBBBF.',
  '..BBBBBBBBBBBB.L',
  '..BBBBBBBBBBBB.L',
  '...BBBBBBBBBB..L',
  '....KK....KK...L',
  '....KK....KK...L',
  '...............L',
  '................',
];

/* ------------------------------------------------------------------ */
/* 5. 【观星学者 · 塞莱斯特】像素图与元数据                           */
/* ------------------------------------------------------------------ */

const CELESTE_PALETTE: PixelPalette = {
  '.': 0x00000000,
  H: 0x311b92, // 星夜法冠
  S: 0xffe0b2, // 沉稳哲思肤色
  E: 0x7c4dff, // 星芒紫色瞳眸
  M: 0xffd54f, // 黄铜单片镜
  G: 0xe0e0e0, // 银白星丝卷发
  R: 0x1a237e, // 深蓝星纹法袍
  r: 0x283593, // 丝绒暗纹
  Z: 0xffd700, // 银线刺绣与金星轨
  P: 0xede7f6, // 手持古星盘
  B: 0x121858, // 幽深法履
};

const CELESTE_FRAME_1: readonly string[] = [
  '.....HHHHHH.....',
  '....HHHHHHHH....',
  '...GGSSSSSSGG...',
  '...GGSE..ESGG...',
  '...GGSM..MSGG...',
  '...GGSSSSSSGG...',
  '....GGGGGGGG....',
  '....GG....GG....',
  '...rRRRRRRRRr...',
  '..rRRRRZZRRRRr..',
  '..rRRRZZZZRRRr.P',
  '..rRRRRZZRRRRrPP',
  '..rRRRRRRRRRRr.P',
  '..rRRRRZZRRRRr..',
  '..rRRRZZZZRRRr..',
  '..rRRRRRRRRRRr..',
  '..rRRRRRRRRRRr..',
  '..rRRRRRRRRRRr..',
  '...RRRRRRRRRR...',
  '...RRRRRRRRRR...',
  '...RRRRRRRRRR...',
  '....BB....BB....',
  '....BB....BB....',
  '................',
];

const CELESTE_FRAME_2: readonly string[] = [
  '.....HHHHHH.....',
  '....HHHHHHHH....',
  '...GGSSSSSSGG...',
  '...GGSE..ESGG...',
  '...GGSM..MSGG...',
  '...GGSSSSSSGG...',
  '....GGGGGGGG....',
  '....GG....GG....',
  '...rRRRRRRRRr..P',
  '..rRRRRZZRRRRrPP',
  '..rRRRZZZZRRRr.P',
  '..rRRRRZZRRRRr..',
  '..rRRRRRRRRRRr..',
  '..rRRRRZZRRRRr..',
  '..rRRRZZZZRRRr..',
  '..rRRRRRRRRRRr..',
  '..rRRRRRRRRRRr..',
  '..rRRRRRRRRRRr..',
  '...RRRRRRRRRR...',
  '...RRRRRRRRRR...',
  '...RRRRRRRRRR...',
  '....BB....BB....',
  '....BB....BB....',
  '................',
];

/* ------------------------------------------------------------------ */
/* 5 大全地图专属手绘 NPC 完整定义库                                  */
/* ------------------------------------------------------------------ */

export const EXCLUSIVE_NPCS: Record<string, ExclusiveNpcMeta> = {
  /* ================================================================ */
  /* 1. 【老林子 forest】：守林隐士 · 青松老者                        */
  /* ================================================================ */
  forest_elder_pine: {
    id: 'forest_elder_pine',
    name: '青松老者',
    title: '守林隐士',
    role: '守林隐士 · 药学宿老',
    themeId: 'forest',
    themeLabel: '老林子',
    avatarIcon: '👴🌲',
    appearanceDescription:
      '身披草蓑，手持老杉木杖，白发白须如霜雪般飘洒，目光清澈如深潭，腰间悬着一只磨得温润的油亮老葫芦与几串风干的灵芝草药。',
    loreAndKnowledge:
      '在这座老林子里结庐隐居了逾甲子，熟知每一棵千年古松的年轮与脾性。通晓全林奇花异草与百草配伍，常常在晨雾中点拨迷途求药者。',
    personalityTags: ['睿智淡泊', '仙风道骨', '见多识广', '慈和沉静'],
    defaultSpawn: { worldX: 1420, depth: 0.52 },
    timeGreetings: {
      dawn: '晨雾最是养人，小友起得这般早，随老朽听听古松漱石的声响吧。',
      day: '阳光透下林隙了，此时采摘树根下的灵芝与薄荷，药性最为饱满。',
      dusk: '晚霞染透松冠了，林鸟归巢，你也该回小屋添一把暖炉柴薪了。',
      night: '万籁俱寂，月映青松。夜风稍凉，若是睡不着，老朽有安神松针茶。',
    },
    microExpressions: {
      greet: {
        expression: 'greet',
        label: '轻抚木杖问好',
        emoji: '🌿',
        lines: [
          '小友，你来了。这老林子草木通灵，你身上带着令人安心的草木清气。',
          '莫急，莫燥。坐在原木桩上歇歇脚，听林风为你解去心中凡俗喧扰。',
          '老朽今晨在古树下为你留了一捧甘冽山泉，且尝一口甘冽。',
        ],
      },
      smile: {
        expression: 'smile',
        label: '慈和微笑',
        emoji: '🍵',
        lines: [
          '呵呵，看着你踏着晨露而来，倒让老朽想起当年初来此林的情景了。',
          '草木无言，却最知人心温凉。见你心境平稳，老朽很是欣慰。',
          '瞧，那边的红白小蘑菇今晨又探出头来了，煞是可爱。',
        ],
      },
      ponder: {
        expression: 'ponder',
        label: '捻须沉思',
        emoji: '🤔',
        lines: [
          '天地一逆旅，同悲万古尘……这老杉树的第千道年轮，记下了许多过往。',
          '方才老朽在推演灵芝与寒泉的配伍，似乎还少了一抹引药的朝露。',
          '万物各得其和以生，各得其养以成。急功近利，反倒失了本真。',
        ],
      },
      joy: {
        expression: 'joy',
        label: '欣然展颜',
        emoji: '✨',
        lines: [
          '哈哈哈，甚好！你对这片林子的爱护，连林间的灵蝶与松鼠都感应到了。',
          '老朽这葫芦里的百花灵酒今日刚巧酿熟，当与小友同饮半盏！',
          '善哉，今日林风柔顺，必定是个草药繁茂、心旷神怡的好日子！',
        ],
      },
      teach: {
        expression: 'teach',
        label: '传授草木知识',
        emoji: '📜',
        lines: [
          '记好：灵芝仙菇采摘不可伤及菌索根髓，留得一分余脉，来年新芽更盛。',
          '薄荷野草需趁朝阳初升未晞时采其尖端嫩叶，入茶方有透骨清凉之效。',
          '木杖不仅用以涉险探路，更是丈量土地与倾听树脉跳动的知己之物。',
        ],
      },
      care: {
        expression: 'care',
        label: '关切叮咛',
        emoji: '🍂',
        lines: [
          '林深露重，衣衫可曾湿润？老朽屋后有些风干的干姜片，带些去吧。',
          '尘世烦忧若太重，便常回这林中坐坐。参天古木自会替你遮风挡雨。',
          '天色渐暮，回小屋的路上小心脚下青苔石阶，莫要贪玩滑了脚。',
        ],
      },
      mystic: {
        expression: 'mystic',
        label: '古老传说',
        emoji: '🔮',
        lines: [
          '相传此林深处沉睡着远古森之精灵，唯有赤子之心方能闻其浅吟低唱。',
          '古树上的每一道裂纹，都是千百年来云雷与岁月交织的神秘符印。',
          '静心凝神，闭上双眼……你听到脚下大地深处奔涌的生机暗流了吗？',
        ],
      },
    },
    heartMilestones: [
      '初见：老朽在这老林中住了不知甲子，难得见有这般懂林木之意的小友。',
      '相知：你每次来，脚步都这般轻缓，连胆小的鹿儿都不再避你了。',
      '熟络：老朽腰间这只葫芦，是我年轻时亲手种下的葫芦藤所结。',
      '信任：当年老朽避世隐居，原以为世外清冷，没成想遇上了你。',
      '默契：今日林风未动，老朽便知你已在山下渡口了。',
      '知心：这卷手抄的《百草古经》，老朽留了半卷批注，赠与你翻阅。',
      '挚友：天地虽大，知己难逢。有你在小屋相伴，老朽不再寂寥。',
      '托付：老林中的七大神木阵眼，老朽已尽数写入图谱，传于你心。',
      '宗师：你如今辨识草木的造诣，已然不在当年的老朽之下了。',
      '归一：老朽与你，便如这林中双生古木，相望相守，岁岁长青。',
    ],
    gifts: {
      loved: ['lingzhi', '灵芝仙菇', '百年松果', 'spring_water', '山泉水'],
      liked: ['mint_herb', '薄荷野草', 'wood', '木材', 'tea_leaf', '茶叶'],
      disliked: ['alloy_ore', '合金矿', 'quantum_chip', '量子芯片'],
      thankLines: {
        loved: '善哉！这般年份与成色的灵物，正是老朽梦寐以求的药引，多谢小友厚意！',
        liked: '清香淡雅，正合老朽心意。小友有心了，老朽定好生珍藏。',
        neutral: '承蒙小友挂念，老朽收下了，且坐下同饮一杯清泉。',
        disliked: '此等金铁机巧戾气太重，与这片老林清净格格不入，小友还是收回吧。',
      },
    },
    pixelFrames: [PINE_ELDER_FRAME_1, PINE_ELDER_FRAME_2],
    pixelPalette: PINE_ELDER_PALETTE,
  },

  /* ================================================================ */
  /* 2. 【后花园 garden】：花艺精灵少女 · 洛丽塔/艾莉                 */
  /* ================================================================ */
  garden_fairy_ellie: {
    id: 'garden_fairy_ellie',
    name: '艾莉',
    title: '花艺精灵少女',
    role: '庭院园艺师 · 花语精灵',
    themeId: 'garden',
    themeLabel: '后花园',
    avatarIcon: '👧🌸',
    appearanceDescription:
      '头戴金黄向日葵草帽，身穿沾着晶莹晨露的米白蕾丝围裙，浅金长发编成两束轻盈麻花辫，发尾系着紫丁香缎带，手提一只盛满鲜花的小竹篮。',
    loreAndKnowledge:
      '自幼在蔷薇与茉莉香气中长大的花语使者。能够感知每一朵花苞的心声与情绪，擅长将不同花草修剪扦插为治愈心灵的芬芳艺术。',
    personalityTags: ['活泼甜美', '灵动可爱', '热忱浪漫', '体贴入微'],
    defaultSpawn: { worldX: 1850, depth: 0.58 },
    timeGreetings: {
      dawn: '早上好呀！快来看，大马士革玫瑰刚展开第一片花瓣，上面还有草莓香气呢！',
      day: '阳光正好，喷泉边有七彩的小彩虹！要来紫藤花架下一起修剪枝丫吗？',
      dusk: '傍晚的花园最温柔啦，晚风吹来茉莉香，我们要不要在遮阳伞下喝杯花果茶？',
      night: '嘘……夜茉莉正悄悄说悄悄话呢。夜晚的小花也要盖好露珠被子睡觉啦。',
    },
    microExpressions: {
      greet: {
        expression: 'greet',
        label: '轻摇花篮打招呼',
        emoji: '🎀',
        lines: [
          '呀，你来啦！今天想挑选哪一朵刚摘下的小花插在窗台上呢？',
          '扑棱扑棱~看这只彩蝶，它刚才停在我的草帽上，现在飞向你啦！',
          '欢迎来到艾莉的秘密花园！今天每一朵小花都在为你盛开哦！',
        ],
      },
      smile: {
        expression: 'smile',
        label: '甜美抿嘴笑',
        emoji: '💐',
        lines: [
          '嘻嘻，每次看到你微笑，园子里原本垂头的小雏菊都会立刻打起精神呢！',
          '告诉你一个小秘密：白石喷泉里的水滴，其实是花园小仙子的竖琴音符哦。',
          '今天的阳光像焦糖饼干一样甜，我们一起来编织一顶向日葵花冠吧！',
        ],
      },
      ponder: {
        expression: 'ponder',
        label: '轻点脸颊思索',
        emoji: '🌸',
        lines: [
          '唔……紫藤花和粉白蔷薇插在一起，是不是再配两枝薄荷叶会更清爽呢？',
          '艾莉在想，花朵为什么会散发香气呢？一定是因为它们想拥抱整个世界吧！',
          '那株从石阶缝隙里钻出来的酢浆草，今天似乎也开出了小黄花呢。',
        ],
      },
      joy: {
        expression: 'joy',
        label: '雀跃转圈',
        emoji: '🎉',
        lines: [
          '太棒啦！你看你看，我亲手培育的双色大马士革玫瑰终于盛开啦！',
          '哇！彩蝶在你掌心跳舞了！它们真的好喜欢你温柔的气息！',
          '好开心呀！能和你一起照料这个满是花香的花园，艾莉觉得好幸福！',
        ],
      },
      teach: {
        expression: 'teach',
        label: '花语与园艺点拨',
        emoji: '📖',
        lines: [
          '采摘大马士革玫瑰一定要用锋利的花剪，倾斜四十五度角，花茎才能喝足清水。',
          '晨露茉莉的花语是“纯洁的心与不变的守候”，泡茶前要先用温水轻吻它哦。',
          '若是遇到捕逗彩蝶，动作一定要像羽毛落地一样轻柔，手心摊开它就会停下。',
        ],
      },
      care: {
        expression: 'care',
        label: '温柔轻抚安慰',
        emoji: '💖',
        lines: [
          '你今天看起来有一点点累呢……来，把这束刚摘的洋甘菊放在枕边，能做个好梦哦。',
          '别难过啦，就算阴天落雨，根系深处的种子也正在悄悄蓄力呀。',
          '艾莉一直在花园里等你，不管什么时候想歇歇脚，茶歇遮阳伞永远为你留座！',
        ],
      },
      mystic: {
        expression: 'mystic',
        label: '精灵花之秘语',
        emoji: '✨',
        lines: [
          '当满月的光芒照进喷泉底池时，池水里会映出你心底最思念之人的倒影哦。',
          '花精灵们告诉我，每一阵吹过花架的暖风，都是远方寄来的无字情书。',
          '用心倾听……闭上眼睛，你听到紫藤花穗在风里窃窃私语了吗？',
        ],
      },
    },
    heartMilestones: [
      '初见：你好呀！我叫艾莉，这顶向日葵草帽是我最宝贝的招牌标志哦！',
      '相知：你每次来都会轻轻关好木栅栏，小花们都夸你是个温柔的人。',
      '熟络：我把一株新培育的小玫瑰命名为了你的名字，它长得可有活力了！',
      '信任：小时候别的孩子都嫌我总和花说话，只有你认真听我讲花语故事。',
      '默契：不需要你说出口，看你的眼神我就知道今天该泡茉莉还是玫瑰茶啦。',
      '知心：这只用紫藤藤蔓亲手编织的小花篮，送给你装每天采集的果实吧！',
      '挚友：有你在身边，整个花园的花朵似乎比往年开得都要娇艳灿烂呢！',
      '托付：就算到了寒冬落雪的时节，只要你在，艾莉的小屋四季都会是春暖花开。',
      '灵犀：我们种下的双生花并蒂盛开啦！传说这代表着永不分离的心灵羁绊。',
      '永恒：愿做你永远的花之精灵，把整个世间最动人的浪漫与芬芳全部捧给你！',
    ],
    gifts: {
      loved: ['damask_rose', '香水大马士革玫瑰', 'dew_jasmine', '晨露茉莉', 'sweet_honey', '花蜜'],
      liked: ['flower', '鲜花', 'seed', '种子', 'jam', '果酱', 'butterfly_dust', '彩蝶灵粉'],
      disliked: ['pebble', '碎石', 'alloy_ore', '合金矿', 'weed', '杂草'],
      thankLines: {
        loved: '哇啊啊！天哪天哪！这是艾莉梦寐以求的最喜欢的花草！超级无敌开心！谢谢你！',
        liked: '好漂亮呀！香气扑鼻，艾莉马上就把它插在围裙口袋的小花瓶里！',
        neutral: '嘻嘻，谢谢你的心意，艾莉会把它好好收在小花篮里的。',
        disliked: '哎呀呀……这个硬邦邦冷冰冰的，小花们看了会吓哭的啦，快收起来吧！',
      },
    },
    pixelFrames: [FAIRY_ELLIE_FRAME_1, FAIRY_ELLIE_FRAME_2],
    pixelPalette: FAIRY_ELLIE_PALETTE,
  },

  /* ================================================================ */
  /* 3. 【黄金田野 golden_field】：麦田守望者 · 丰收老农              */
  /* ================================================================ */
  field_scarecrow_farmer: {
    id: 'field_scarecrow_farmer',
    name: '麦芒叔',
    title: '麦田守望者',
    role: '老庄稼把式 · 节气农叟',
    themeId: 'golden_field',
    themeLabel: '黄金田野',
    avatarIcon: '👨🌾',
    appearanceDescription:
      '头戴尖顶橙黄草帽，在微风中轻晃向你爽朗招手，身穿红白细格子工装衬衫与蓝色背带裤，手中握着光洁温润的木草叉，面容黝黑憨厚，笑容爽朗开阔。',
    loreAndKnowledge:
      '守望这片麦田已不知几十度春秋，通晓二十四节气变迁与物候农谚。风车磨坊里飘出的麦香，是他毕生劳作与守候的骄傲。',
    personalityTags: ['憨厚朴实', '豪爽豁达', '勤劳坚韧', '风趣健谈'],
    defaultSpawn: { worldX: 2350, depth: 0.54 },
    timeGreetings: {
      dawn: '早起三时光，一天顶两天！小掌柜，早晨麦芒上的白霜最是亮堂！',
      day: '风车转得真有劲儿！大太阳晒得麦谷咯咯响，今天保管是个收成好日头！',
      dusk: '收工喽！晚霞落进南瓜堆，走，去草垛边啃两个热乎甜窝窝头！',
      night: '月光照在麦田上像铺了一层白银。老汉我在草垛守夜，安心睡吧小掌柜！',
    },
    microExpressions: {
      greet: {
        expression: 'greet',
        label: '挥动草叉热情招呼',
        emoji: '🌾',
        lines: [
          '哈哈！小掌柜来田头遛弯啦！快看老汉这片齐腰深的金麦子！',
          '来来来，坐草垛上歇歇！刚晒透的干草，比城里的大软沙发还舒坦！',
          '麦浪滚滚满田香，风调雨顺好年光！小掌柜今日气色真不错！',
        ],
      },
      smile: {
        expression: 'smile',
        label: '爽朗憨笑',
        emoji: '😄',
        lines: [
          '嘿嘿嘿！庄稼人没啥巧嘴，只要土地里长出沉甸甸的实惠，老汉打心眼里高兴！',
          '瞧那个大南瓜，圆滚滚胖嘟嘟的，抱起来得压得老汉腰板直晃悠！',
          '土地是世上最实诚的伙伴，你流一滴汗，它就还你一捧金黄麦粒！',
        ],
      },
      ponder: {
        expression: 'ponder',
        label: '手搭凉棚看天候',
        emoji: '🌤️',
        lines: [
          '云彩往西跑，马车不用套；云彩往东走，大雨下成狗……得盯着点天色。',
          '白露身不露，秋分夜见凉。该给苗圃里的耐寒蔬菜备些干草帘子了。',
          '这架老木风车陪老汉转了三十年，每个齿轮转到哪声响，我闭着眼都知道。',
        ],
      },
      joy: {
        expression: 'joy',
        label: '拍腿大笑',
        emoji: '🚜',
        lines: [
          '好收成！大丰收！今年磨出的第一袋白面粉，先给你的数码小屋送去！',
          '哈哈！今年南瓜甜度超标啦，煮熟了直接流蜜糖汁，香死个人！',
          '庄稼人逢着丰年，胜过金山银山！今晚老汉要就着炒花生喝两盅！',
        ],
      },
      teach: {
        expression: 'teach',
        label: '传授农事谚语',
        emoji: '🌽',
        lines: [
          '收割麦穗可得手脚麻利：左手揽穗，右手镰刀贴地切，穗头不落泥！',
          '南瓜要挑瓜皮泛白霜、敲着咚咚闷响的，那才叫熟透蜜甜！',
          '草垛要层层扎实、外伞内实，这样哪怕下三天连阴雨，里头也是焦干喷香！',
        ],
      },
      care: {
        expression: 'care',
        label: '质朴关切',
        emoji: '🍠',
        lines: [
          '田间秋风硬，小掌柜穿得单薄，仔细受了凉！来，揣个烤熟的热番薯暖暖手！',
          '累了就靠着草垛打个盹，有老汉在田埂守着，连只麻雀都偷不走你的麦子！',
          '人活着就跟麦子一样，熬过了严冬的冻雪，开春才能拔节抽穗长得壮实！',
        ],
      },
      mystic: {
        expression: 'mystic',
        label: '大地恩泽',
        emoji: '🍂',
        lines: [
          '老一辈人讲，谷神就在这千万根麦穗里巡行，脚步沙沙响，那是麦浪在迎候。',
          '泥土深处是有灵性的，只要你真心对它，它千百年都不会亏待耕耘的人。',
          '闭上眼睛闻闻，这晚风里有阳光、麦芒与黑土地合奏的生命交响乐。',
        ],
      },
    },
    heartMilestones: [
      '初见：小掌柜面生得很，不过肯往泥土麦田里走，就是老汉的实诚朋友！',
      '相知：你帮老汉扶过被风吹歪的草垛，好后生，有副好心肠！',
      '熟络：走！今天风车磨坊出新面，去老汉家里吃刚出锅的柴火大麦饼！',
      '信任：老汉这把用了大半辈子的雕花木草叉，你拿去挥两把试试手感！',
      '默契：老汉只要抬眼瞅瞅天，你就知道把晾晒的麦子往仓里收了，真搭档！',
      '知心：城里人嫌泥土脏，老汉觉得这黑泥土比啥香水都贴心，你懂老汉的心。',
      '挚友：老汉没啥能耐，但只要你小屋开口，老汉田里的麦仓随便你拉！',
      '托付：这本记了四十年节气物候的黄皮本本，老汉正式交到你手里了。',
      '如子：看着你把小屋打理得红红火火，老汉心里比多收三千斤麦子还宽慰！',
      '丰碑：这片黄金田野，有老汉的半生汗水，如今也有了你留下的灿烂金光！',
    ],
    gifts: {
      loved: ['golden_wheat', '金黄丰收麦穗', 'sugar_pumpkin', '蜜糖大南瓜', 'bread', '烤麦面包'],
      liked: ['seed', '种子', 'wood', '木头', 'egg', '土鸡蛋', 'pie', '馅饼'],
      disliked: ['stardust', '星尘', 'magic_scroll', '魔法卷轴', 'flower_perfume', '香水'],
      thankLines: {
        loved: '哎呀呀！这麦穗粒粒饱满圆滚！这南瓜沉实！小掌柜是真懂老汉的心头好啊！痛快！',
        liked: '好东西！老汉正愁家里缺这口粮呢，多谢小掌柜挂念！',
        neutral: '哈哈，小掌柜送啥老汉都高兴，收下啦！',
        disliked: '哎哟喂……这花里胡哨的玩意儿，不能吃不能种的，老汉真用不惯哪！',
      },
    },
    pixelFrames: [FARMER_FRAME_1, FARMER_FRAME_2],
    pixelPalette: FARMER_PALETTE,
  },

  /* ================================================================ */
  /* 4. 【溪水边 stream】：碧水钓叟 · 渔隐客                           */
  /* ================================================================ */
  stream_fisherman_hermit: {
    id: 'stream_fisherman_hermit',
    name: '渔隐客',
    title: '碧水钓叟',
    role: '江海钓客 · 沧浪隐者',
    themeId: 'stream',
    themeLabel: '溪水边',
    avatarIcon: '🎣🌊',
    appearanceDescription:
      '端坐在码头老杉木墩上悠然持竿，身着青灰蓑笠与水草编织的竹芒鞋，白须飘然垂胸，神情闲适淡远，身旁立着青瓷茶壶与一只盛满清冽溪水的活水鱼篓。',
    loreAndKnowledge:
      '隐居清溪数十载，擅察水流湍缓、鱼讯起伏与水草荣枯。传闻其甩竿入水，不为获鳞，只为借微波浮标体悟浮沉进退的人生至理。',
    personalityTags: ['闲适禅意', '敏锐机智', '豁达超脱', '深藏不露'],
    defaultSpawn: { worldX: 2900, depth: 0.62 },
    timeGreetings: {
      dawn: '晨烟笼碧水，晓日照苍苔。小友，清晨水皮凉，正值巨鳞觅食之时。',
      day: '日影透溪底，卵石耀金光。心若不动，便如那水底老龟，任流水滔滔。',
      dusk: '斜阳照孤舟，水波泛橘红。晚霞垂钓，钓的不是鱼，是满江流光。',
      night: '月印寒潭千尺清，夜间抛竿，唯闻浮标点水与夜蛙长鸣。',
    },
    microExpressions: {
      greet: {
        expression: 'greet',
        label: '轻抬鱼竿微颔致意',
        emoji: '🎋',
        lines: [
          '客从何处来？何妨在这栈桥木墩上小坐片刻，听水波洗砚。',
          '哈哈，步履轻捷无声，倒是个不惊游鱼的好钓友。请坐。',
          '一竿一笠一溪月，不知世间岁月长。小友今日可有闲心垂钓？',
        ],
      },
      smile: {
        expression: 'smile',
        label: '淡然拈须微笑',
        emoji: '🐟',
        lines: [
          '钓鱼之道，重在守候。水流急时莫下钩，水势平缓方从容。',
          '你看那尾碧鳞青鱼，在睡莲花瓣下打了个水旋又游走了，颇有灵性。',
          '得鱼亦喜，无鱼亦喜。满目清泉睡莲，早已满载而归。',
        ],
      },
      ponder: {
        expression: 'ponder',
        label: '凝视水面浮标',
        emoji: '🌊',
        lines: [
          '浮标微颤三寸……动中有静，静中有动，这正是天地运转之妙。',
          '流水不争先，争的是滔滔不绝。人世浮沉，亦复如是。',
          '方才一尾赤鲤撞开涟漪，老夫在想，它究竟是想跃龙门，还是眷恋浅滩？',
        ],
      },
      joy: {
        expression: 'joy',
        label: '提竿破水欢笑',
        emoji: '🎣',
        lines: [
          '中！哈哈！尺余长的雪鳞鳟鱼！鳞甲银白如缎，真乃溪中尤物！',
          '好水出好鱼！今晚老夫这青瓷锅里，当有一锅鲜美透骨的鱼羹！',
          '遇着知心客，又逢咬钩鱼，老夫今日之乐，千金不换！',
        ],
      },
      teach: {
        expression: 'teach',
        label: '传授垂钓秘诀',
        emoji: '🐡',
        lines: [
          '水流湍急处下重铅，缓流浅滩用羽漂。知水之性，鱼莫能逃。',
          '嫩绿水芹配上鱼骨清汤，既去腥膻，又添一抹高山流水的脆甜。',
          '幽香睡莲采摘需用巧劲轻旋花柄，切莫蛮力拉扯坏了水下莲藕玉根。',
        ],
      },
      care: {
        expression: 'care',
        label: '清凉安神劝慰',
        emoji: '🍵',
        lines: [
          '溪畔湿寒，老夫这壶里煮着高山老岩茶与生姜，趁热饮下半盏。',
          '心烦意乱时，就来盯着水面上漂动的睡莲。看上小半个时辰，心便定了。',
          '莫为昨日逝去的流水懊恼，前路清溪万重，更有无限风光。',
        ],
      },
      mystic: {
        expression: 'mystic',
        label: '沧浪哲理',
        emoji: '📜',
        lines: [
          '沧浪之水清兮，可以濯我缨；沧浪之水浊兮，可以濯我足。',
          '老夫这一钩直下千尺水，钓的不是口腹之欲，是天地一刹那的契机。',
          '浅滩卵石历经万载磨砺方得温润，人若经得起岁月冲刷，亦当光洁如玉。',
        ],
      },
    },
    heartMilestones: [
      '初见：客舟偶遇，老夫垂纶已久，难得有脚步如此轻静的知音。',
      '相知：你上次静坐了两个时辰未发一言，老夫便知你是个耐得住寂寞的人。',
      '熟络：这柄随老夫南征北战三十载的紫竹钓竿，小友拿去试甩两竿。',
      '信任：当年老夫辞官归隐，投江自溺亦未能洗净浊气，直到来到这条清溪。',
      '默契：浮标只轻点半目，你我便同时知晓大鱼已经咬钩，心意相通。',
      '知心：老夫收网烹茶，世人谓我孤傲，唯有小友懂我这片清波自适。',
      '挚友：老夫鱼篓里的极品七彩锦鳞，向来只为挚友下锅！',
      '托付：整条溪流十七处暗礁回流宝穴，老夫已录成《渔经》倾囊相赠。',
      '大成：执竿如执剑，涉水如凌虚。小友如今的气韵，已有沧浪之风！',
      '知音：孤舟蓑笠，清江浩渺。得友如此，此生何求！',
    ],
    gifts: {
      loved: ['water_lily', '幽香睡莲', 'water_cress', '嫩绿水芹', 'fish', '彩虹鲑鱼', 'spring_water', '清冽泉水'],
      liked: ['reed', '芦苇', 'pebble', '鹅卵石', 'tea_leaf', '茶叶', 'wood', '木材'],
      disliked: ['quantum_chip', '机械零件', 'slimy_goo', '腥臭粘液', 'trash', '垃圾'],
      thankLines: {
        loved: '善哉！水润物灵，芳香沁脾！这等得天地清和之气的水中奇珍，老夫甚喜！',
        liked: '清雅适性，恰如清溪流水，多谢小友的美意。',
        neutral: '客礼受领，小友且坐，容老夫续上一盏热茶。',
        disliked: '唉，江水宜洁，此等浊秽机巧之物，徒增水波烦恼，还是请回吧。',
      },
    },
    pixelFrames: [FISHERMAN_FRAME_1, FISHERMAN_FRAME_2],
    pixelPalette: FISHERMAN_PALETTE,
  },

  /* ================================================================ */
  /* 5. 【观星台 observatory】：观星学者 · 塞莱斯特                   */
  /* ================================================================ */
  observatory_celeste: {
    id: 'observatory_celeste',
    name: '塞莱斯特',
    title: '观星学者',
    role: '星象学者 · 天体宗师',
    themeId: 'observatory',
    themeLabel: '观星台',
    avatarIcon: '🧙♂️🌌',
    appearanceDescription:
      '身披深蓝丝绒星纹法袍，法袍上绣着微缩银线星座与流动星轨，右眼戴着黄铜单片折射镜，手握雕有黄道十二宫刻度的便携星盘与羽毛笔，银白卷发随夜风微拂，深邃若浩瀚夜空。',
    loreAndKnowledge:
      '倾尽半生推演黄道十二宫星图与宇宙以太潮汐。能通过观测星辰位移与流星轨迹，洞悉天地规律与人们内心隐秘的情感图景。',
    personalityTags: ['深邃优雅', '严谨理性', '神秘莫测', '温和包容'],
    defaultSpawn: { worldX: 3380, depth: 0.50 },
    timeGreetings: {
      dawn: '启明星隐入晨光，而星轨的数学逻辑永远恒定。早安，求知的心灵。',
      day: '虽是白昼，浩瀚星辰依旧在深空静默运转。黄铜星象仪已校准完毕。',
      dusk: '第一颗星辰在晚霞深处点亮了。星云正在苏醒，准备开启今夜的观测吧。',
      night: '银河如瀑贯穿天幕。站上星座地垫，此刻的你正与整个宇宙产生共振。',
    },
    microExpressions: {
      greet: {
        expression: 'greet',
        label: '轻转便携星盘致意',
        emoji: '🧭',
        lines: [
          '星辰向你致意，远道而来的旅行者。今夜的天空为你准备了罕见的星象。',
          '你踏上观星台的瞬间，你本命星位的光芒明显增强了零点三等星。',
          '在无垠的宇宙面前，我们的相遇绝非偶然，而是引力法则必然的交点。',
        ],
      },
      smile: {
        expression: 'smile',
        label: '扶镜优雅微笑',
        emoji: '✨',
        lines: [
          '微小的快乐如同夜空跳跃的脉冲星，虽不刺眼，却恒定闪烁在你的眼眸中。',
          '我喜欢你仰望星空时的神情，纯净得如同未经大气折射的恒星原始光谱。',
          '黄铜望远镜的目镜已经为你擦净，今晚木星的四颗卫星正向你招手呢。',
        ],
      },
      ponder: {
        expression: 'ponder',
        label: '凝视星图沉思',
        emoji: '🔭',
        lines: [
          '光在宇宙中旅行了亿万年才抵达我们的视网膜……我们所见，皆是壮丽的往昔。',
          '心境星盘显示，你内心深处有一片未曾被命名的温暖星云。',
          '引力与电磁力维持着星体的平衡，如同理智与情感维系着人类的心灵宇宙。',
        ],
      },
      joy: {
        expression: 'joy',
        label: '记下流星轨迹欢颜',
        emoji: '🌠',
        lines: [
          '观测到了！刚刚掠过的英仙座流星雨，光芒照亮了整个星座地垫！完美！',
          '星象仪共鸣了！这种频率的共鸣在过去三十年间只发生过三次！太震撼了！',
          '真奇妙，与你并肩观测，似乎连星图记录中的误差扰动都神奇地归零了！',
        ],
      },
      teach: {
        expression: 'teach',
        label: '解析天体玄奥',
        emoji: '📜',
        lines: [
          '抚触星象仪时需放慢呼吸，将自身以太与浑仪的黄道刻度同频律动。',
          '星芒碎片是流星与以太大气摩擦凝结的产物，蕴含着最纯粹的空间能量。',
          '黄道十二宫并非命运的枷锁，而是映照你灵魂潜能与性格多面体的镜子。',
        ],
      },
      care: {
        expression: 'care',
        label: '星光守护叮嘱',
        emoji: '🛡️',
        lines: [
          '高台夜冷，深空之风虽美却寒。我的星纹法袍分你一半披上吧。',
          '迷茫时不要怕，抬起头来。无论你身在何方，北极星永远恒定指引着归途。',
          '每一个生命都是由星辰微尘汇聚而成。在宇宙眼中，你无比珍贵而独特。',
        ],
      },
      mystic: {
        expression: 'mystic',
        label: '深空星命预言',
        emoji: '🌌',
        lines: [
          '今夜双鱼座与仙女座发生黄金合相，预示着一段深邃持久的羁绊正在萌芽。',
          '时空的涟漪穿越维度屏障……我能看到，你的小屋将成为无数漂泊心灵的灯塔。',
          '闭上眼睛，倾听群星运转的古老旋律……那是宇宙从初生至今未绝的回响。',
        ],
      },
    },
    heartMilestones: [
      '初见：星轨记录者塞莱斯特，欢迎来到这处远离尘嚣的天体观测台。',
      '相知：你是我遇到的第一个能耐心地看完一整卷星盘坐标测算的人。',
      '熟络：我将你的名字记录在天鹅座未编号星云的象限旁，它将永存星图。',
      '信任：当年学院的学者们嘲笑我的以太共鸣理论，唯有你选择相信。',
      '默契：流星划过的那一秒，我们不需言语便同时看向了同一个天空坐标。',
      '知心：这枚我亲手校准的便携黄铜微缩星盘，赠予你挂在小屋窗棂前。',
      '挚友：星海茫茫，光年浩瀚。能够在同一时代、同一颗星球相遇，何其幸运。',
      '托付：我毕生编纂的《宇宙全天黄道星象集》，唯一的继承者只能是你。',
      '共鸣：星芒交织，你的心灵频率与整个宇宙的宏伟节律达到了完美的合鸣。',
      '永恒：群星终会熄灭，宇宙终入寂夜，但我们共同见证的光辉，永不磨灭。',
    ],
    gifts: {
      loved: ['star_shards', '星芒碎片', 'astral_resonance', '星轨天象共鸣', 'stardust', '星尘', 'crystal', '水晶'],
      liked: ['metal_ore', '金属矿', 'glass', '玻璃镜片', 'pebble', '陨石碎屑', 'magic_scroll', '魔法卷轴'],
      disliked: ['rotten_wood', '朽木', 'slimy_goo', '污泥', 'bone', '骨头'],
      thankLines: {
        loved: '不可思议的光谱！这枚星芒碎片中蕴含的宇宙以太纯度极其惊人！塞莱斯特致以最高敬意！',
        liked: '纯净而规整的质感，用于校准星象仪透镜再合适不过，非常感谢！',
        neutral: '承蒙赠予，我将把它收录于观测台的物资编目中。',
        disliked: '此类物质不仅阻碍光学折射，还会产生无谓的杂波干扰，请妥善收回吧。',
      },
    },
    pixelFrames: [CELESTE_FRAME_1, CELESTE_FRAME_2],
    pixelPalette: CELESTE_PALETTE,
  },
};

/* ------------------------------------------------------------------ */
/* 核心检索与好感逻辑                                                  */
/* ------------------------------------------------------------------ */

/** 按场景或主题 ID 获取对应专属 NPC */
export function getExclusiveNpcByTheme(themeId: string): ExclusiveNpcMeta {
  const canonical = (
    themeId === 'garden'
      ? 'garden'
      : themeId === 'field' || themeId === 'golden_field' || themeId === 'country'
        ? 'golden_field'
        : themeId === 'stream'
          ? 'stream'
          : themeId === 'planet' || themeId === 'observatory' || themeId === 'scifi'
            ? 'observatory'
            : 'forest'
  ) as CanonicalThemeId;

  const found = Object.values(EXCLUSIVE_NPCS).find((npc) => npc.themeId === canonical);
  return found ?? EXCLUSIVE_NPCS.forest_elder_pine;
}

/** 获取所有 5 大专属 NPC 列表 */
export function getAllExclusiveNpcs(): ExclusiveNpcMeta[] {
  return Object.values(EXCLUSIVE_NPCS);
}

/** 读取或初始化所有 NPC 好感度状态 */
export function loadAllNpcStates(): Record<string, NpcState> {
  try {
    const raw = localStorage.getItem(NPC_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed === 'object' && parsed !== null) {
        return parsed;
      }
    }
  } catch {
    // 忽略异常，回退初始化
  }
  return {};
}

/** 持久化保存所有 NPC 状态 */
export function saveAllNpcStates(states: Record<string, NpcState>): void {
  try {
    localStorage.setItem(NPC_STORAGE_KEY, JSON.stringify(states));
  } catch {
    // 存储满或受限静默处理
  }
}

/** 获取或新建特定 NPC 交互状态 */
export function getNpcState(npcId: string, currentDay = 1): NpcState {
  const states = loadAllNpcStates();
  let state = states[npcId];
  if (!state) {
    state = {
      npcId,
      points: 0,
      hearts: 0,
      giftsToday: 0,
      lastGiftDay: currentDay,
      interactCount: 0,
    };
  } else if (state.lastGiftDay !== currentDay) {
    // 跨天重置赠礼限制
    state = {
      ...state,
      giftsToday: 0,
      lastGiftDay: currentDay,
    };
  }
  return state;
}

/** 动态计算好感心数 */
export function calculateHearts(points: number): number {
  return Math.max(0, Math.min(MAX_HEARTS, Math.floor(points / POINTS_PER_HEART)));
}

/**
 * 微表情台词解析：依据 NPC、心情微表情、心数阶段与时段返回最贴合的台词气泡
 *
 * 已知缺口：`timeOfDay` / `hearts` 已进签名但尚未消费——`ExclusiveNpcMeta.timeGreetings`
 * 与 `heartMilestones`（10 段，索引 0..9）两套数据已备好，等接 UI 时按
 * 「greet 用时段问候、hearts>0 追加阶段心声」接线。当前刻意不猜，避免无消费者的
 * 行为被当成已实现。
 */
export function resolveNpcMicroDialogue(
  npcId: string,
  options: {
    expression?: MicroExpression;
    timeOfDay?: TimeOfDay;
    hearts?: number;
    currentDay?: number;
  } = {},
): {
  text: string;
  expression: MicroExpression;
  emoji: string;
  speaker: string;
  title: string;
} {
  const npc = EXCLUSIVE_NPCS[npcId] ?? EXCLUSIVE_NPCS.forest_elder_pine;
  const expr = options.expression ?? 'greet';

  const exprPool = npc.microExpressions[expr] ?? npc.microExpressions.greet;
  const lines = exprPool.lines;
  const text = lines[Math.floor(Math.random() * lines.length)] || lines[0];

  return {
    text: `【${npc.title}】${text}`,
    expression: exprPool.expression,
    emoji: exprPool.emoji,
    speaker: npc.name,
    title: npc.title,
  };
}

/**
 * NPC 日常互动（闲聊 / 请教求教 / 问候致意）
 */
export function interactWithNpc(
  npcId: string,
  type: 'chat' | 'ask_lore' | 'pat_greet',
  currentDay = 1,
): {
  state: NpcState;
  gainedPoints: number;
  dialogue: { text: string; expression: MicroExpression; emoji: string };
} {
  const state = getNpcState(npcId, currentDay);

  let gained = 15;
  let targetExpr: MicroExpression = 'smile';

  if (type === 'ask_lore') {
    gained = 20;
    targetExpr = 'teach';
  } else if (type === 'pat_greet') {
    gained = 10;
    targetExpr = 'greet';
  }

  const nextPoints = Math.min(1000, state.points + gained);
  const nextHearts = calculateHearts(nextPoints);

  const updatedState: NpcState = {
    ...state,
    points: nextPoints,
    hearts: nextHearts,
    interactCount: state.interactCount + 1,
  };

  const all = loadAllNpcStates();
  all[npcId] = updatedState;
  saveAllNpcStates(all);

  const dial = resolveNpcMicroDialogue(npcId, {
    expression: targetExpr,
    hearts: nextHearts,
    currentDay,
  });

  return {
    state: updatedState,
    gainedPoints: gained,
    dialogue: {
      text: dial.text,
      expression: dial.expression,
      emoji: dial.emoji,
    },
  };
}

/**
 * 每日赠礼核心逻辑（好感加成、限额防刷与专属答谢台词）
 */
export function giveGiftToNpc(
  npcId: string,
  itemId: string,
  itemLabel?: string,
  currentDay = 1,
): {
  success: boolean;
  state: NpcState;
  gainedPoints: number;
  reactionType: 'loved' | 'liked' | 'neutral' | 'disliked';
  responseText: string;
  reason?: string;
} {
  const npc = EXCLUSIVE_NPCS[npcId] ?? EXCLUSIVE_NPCS.forest_elder_pine;
  const state = getNpcState(npcId, currentDay);

  if (state.giftsToday >= MAX_GIFTS_PER_DAY) {
    return {
      success: false,
      state,
      gainedPoints: 0,
      reactionType: 'neutral',
      responseText: `【${npc.name}】小友心意老夫已足，今日赠礼已达上限（${MAX_GIFTS_PER_DAY}/${MAX_GIFTS_PER_DAY}），明日再来吧！`,
      reason: '今日赠礼次数已用完',
    };
  }

  const checkItem = (itemId + (itemLabel || '')).toLowerCase();
  let gained = 20;
  let reactionType: 'loved' | 'liked' | 'neutral' | 'disliked' = 'neutral';
  let thankLine = npc.gifts.thankLines.neutral;

  // 判定喜好
  if (npc.gifts.loved.some((kw) => checkItem.includes(kw.toLowerCase()))) {
    gained = 50;
    reactionType = 'loved';
    thankLine = npc.gifts.thankLines.loved;
  } else if (npc.gifts.liked.some((kw) => checkItem.includes(kw.toLowerCase()))) {
    gained = 35;
    reactionType = 'liked';
    thankLine = npc.gifts.thankLines.liked;
  } else if (npc.gifts.disliked.some((kw) => checkItem.includes(kw.toLowerCase()))) {
    gained = 5;
    reactionType = 'disliked';
    thankLine = npc.gifts.thankLines.disliked;
  }

  const nextPoints = Math.min(1000, state.points + gained);
  const nextHearts = calculateHearts(nextPoints);

  const updatedState: NpcState = {
    ...state,
    points: nextPoints,
    hearts: nextHearts,
    giftsToday: state.giftsToday + 1,
    lastGiftDay: currentDay,
  };

  const all = loadAllNpcStates();
  all[npcId] = updatedState;
  saveAllNpcStates(all);

  return {
    success: true,
    state: updatedState,
    gainedPoints: gained,
    reactionType,
    responseText: `【${npc.name}】${thankLine}（好感度 +${gained}）`,
  };
}

/**
 * 将手绘专属 NPC 转换为系统兼容的 NpcRow 规格（供 HUD 与社交面板原样渲染）
 */
export function convertNpcToNpcRow(npc: ExclusiveNpcMeta, state?: NpcState): NpcRow {
  const hearts = state ? state.hearts : 0;
  const heartsDisplay = '♥'.repeat(hearts) + '♡'.repeat(MAX_HEARTS - hearts);

  return {
    id: npc.id,
    name: `${npc.name}（${npc.title}）`,
    role: npc.role,
    place: npc.themeLabel,
    activity: npc.personalityTags.join(' · '),
    awake: true,
    hearts,
    hearts_display: heartsDisplay,
    marker: hearts >= 8 ? '!' : hearts >= 4 ? '·' : '',
  };
}
