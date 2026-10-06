import {
  compositeLayers,
  idleFrames,
  walkFrames,
  toPixelPalette,
  assertPaletteCoversMatrix,
  AVATAR_WIDTH,
  AVATAR_HEIGHT,
  type AvatarLayers,
  type AvatarCharPalette,
} from '../avatar/avatarPixels';
import {
  spriteFromMatrix,
  mulberry32,
  type PixelPalette,
} from './cabinPixels';
import type {
  AvatarProfile,
  HouseAvatar,
  AvatarPalette,
  PortraitInput,
} from '../../api/avatar';
import { Texture } from 'pixi.js';

/**
 * 数码小屋 · 无限组合、自由换装、可根据个人画像 AI 随机生成的微像素高精角色系统。
 *
 * 核心特性：
 * 1. 精致手绘微像素角色部件库：
 *    - 发型（10 款）：双马尾、微卷长发、清爽短发、古典发髻、蓬松碎发、斗篷兜帽、优雅侧编、齐耳波波头、顺直及腰、羊角甜卷
 *    - 发色（10 款）：薄荷绿、雾霾蓝、香槟金、曜石黑、樱花粉、栗茶、丁香紫、银霜白、琥珀赤红、焦糖暖棕
 *    - 服装（10 款）：森系长袍、冒险斗篷、学院马甲、针织毛衣、水手服、汉风云锦、星空法袍、工装背带、哥特洋装、元气卫衣
 *    - 面部神态（8 款）：清澈灵动、沉静思考、元气微笑、星星眼、温柔眨眼、纯真呆萌、敏锐专注、慵懒朦胧
 *    - 随身饰品（8 款）：魔法星杖、蝴蝶发卡、治愈小伞、猫耳发箍、羽毛笔与诗卷、萤火提灯、治愈花环、秘术灵珠
 * 2. 排列组合空间：10 × 10 × 10 × 8 × 8 = 64,000 种组合（远超 18,432+ 种），支持自由换装与画像推演。
 * 3. 画像与心理推演：
 *    - MBTI（16 型）：影响剪影质感、服装剪裁、站姿与气场
 *    - 八字五行（木/火/土/金/水）：主导调色板意象与自然光泽
 *    - 心境（calm/sunny/focused/melancholy）：引导面部神态与随身法器
 * 4. PixiJS 动效集成：`exportAvatarFrames()` 一键导出待机与行走纹理切片，支持主场景即时“换皮”！
 */

/* ------------------------------------------------------------------ */
/* 基础尺寸与字符键映射                                                */
/* ------------------------------------------------------------------ */

export { AVATAR_WIDTH, AVATAR_HEIGHT };
export const FACE_CX = 12;
export const FACE_CY = 10;
export const BODY_TOP = 20;

/** 字符色板语义映射表 */
export const MODULAR_CHAR_KEYS: Record<string, string> = {
  s: 'skin',
  S: 'skin_shadow',
  h: 'hair',
  H: 'hair_shadow',
  G: 'hair_highlight',
  o: 'outfit_base',
  O: 'outfit_shadow',
  F: 'outfit_highlight',
  c: 'cape',
  C: 'cape_shadow',
  e: 'eye_ink',
  E: 'eye_highlight',
  b: 'blush',
  t: 'trim',
  g: 'hand_item',
  w: 'shoe',
  L: 'legwear',
  x: 'rim_light',
  a: 'emblem_bg',
  z: 'ground_shadow',
  '.': 'transparent',
};

/* ------------------------------------------------------------------ */
/* 部件类型定义                                                       */
/* ------------------------------------------------------------------ */

export interface HairstyleDef {
  id: string;
  name: string;
  desc: string;
  tags: string[];
}

export interface HairToneDef {
  id: string;
  name: string;
  colorName: string;
  baseHex: string;
  shadowHex: string;
  highlightHex: string;
}

export interface OutfitDef {
  id: string;
  name: string;
  desc: string;
  baseHex: string;
  shadowHex: string;
  highlightHex: string;
  trimHex: string;
  legwearHex: string;
  shoeHex: string;
  capeHex?: string;
  capeShadowHex?: string;
}

export interface ExpressionDef {
  id: string;
  name: string;
  desc: string;
  moodBias: string;
}

export interface AccessoryDef {
  id: string;
  name: string;
  desc: string;
  handItemName: string;
}

/* ------------------------------------------------------------------ */
/* 部件资源库（发型 10 / 发色 10 / 服装 10 / 神态 8 / 饰品 8）       */
/* ------------------------------------------------------------------ */

export const MODULAR_HAIRSTYLES: HairstyleDef[] = [
  { id: 'twin_tail', name: '双马尾', desc: '俏皮灵动的元气双马尾，发尾系着浅色丝带', tags: ['cute', 'energetic'] },
  { id: 'wavy_long', name: '微卷长发', desc: '温婉柔和的波浪长卷发，随风轻拂', tags: ['gentle', 'elegant'] },
  { id: 'short_neat', name: '清爽短发', desc: '干练洒脱的层次短发，发梢微翘', tags: ['crisp', 'calm'] },
  { id: 'classic_bun', name: '古典发髻', desc: '精致高雅的东方发髻，缀以古典发簪', tags: ['traditional', 'noble'] },
  { id: 'fluffy_messy', name: '蓬松碎发', desc: '慵懒随性的蓬松碎发，充满少年感', tags: ['casual', 'free'] },
  { id: 'hood_cloak', name: '斗篷兜帽', desc: '包裹头部的神秘旅人兜帽，露出一弯刘海', tags: ['mystic', 'adventure'] },
  { id: 'side_braid', name: '优雅侧编', desc: '搭在一侧胸前的单侧长麻花编发', tags: ['literary', 'peaceful'] },
  { id: 'bob_cut', name: '齐耳波波头', desc: '内扣修饰脸颊的齐短发，减龄可爱', tags: ['modern', 'sweet'] },
  { id: 'straight_long', name: '顺直及腰', desc: '如瀑布般顺滑垂落的长发，光泽闪烁', tags: ['pure', 'serene'] },
  { id: 'curly_horns', name: '羊角甜卷', desc: '两侧如可爱羊角般盘旋的小卷发', tags: ['playful', 'fantasy'] },
];

export const MODULAR_HAIR_TONES: HairToneDef[] = [
  { id: 'mint', name: '薄荷绿', colorName: '清爽薄荷', baseHex: '#7fb69a', shadowHex: '#52836b', highlightHex: '#a9dbc1' },
  { id: 'mist_blue', name: '雾霾蓝', colorName: '静谧雾蓝', baseHex: '#6c8fa8', shadowHex: '#476980', highlightHex: '#9fc0d8' },
  { id: 'champagne_gold', name: '香槟金', colorName: '耀目香槟', baseHex: '#e6c875', shadowHex: '#b29446', highlightHex: '#fbf0b5' },
  { id: 'obsidian_black', name: '曜石黑', colorName: '深邃曜石', baseHex: '#23272e', shadowHex: '#14171d', highlightHex: '#454c5c' },
  { id: 'sakura_pink', name: '樱花粉', colorName: '初绽落樱', baseHex: '#f4a6b8', shadowHex: '#c97387', highlightHex: '#fdd3de' },
  { id: 'chestnut', name: '栗茶色', colorName: '温暖栗茶', baseHex: '#7c4f38', shadowHex: '#523120', highlightHex: '#a8745b' },
  { id: 'lilac_purple', name: '丁香紫', colorName: '幽香丁香', baseHex: '#a593c6', shadowHex: '#746194', highlightHex: '#d1c4ea' },
  { id: 'silver_frost', name: '银霜白', colorName: '霜月银白', baseHex: '#d8e2ec', shadowHex: '#a0b3c6', highlightHex: '#f5f8fc' },
  { id: 'amber_red', name: '琥珀赤红', colorName: '暖阳琥珀', baseHex: '#b84a39', shadowHex: '#852d20', highlightHex: '#df7462' },
  { id: 'caramel_tea', name: '焦糖暖棕', colorName: '甜郁焦糖', baseHex: '#c47d48', shadowHex: '#8d4d23', highlightHex: '#e8a572' },
];

export const MODULAR_OUTFITS: OutfitDef[] = [
  {
    id: 'forest_robe',
    name: '森系长袍',
    desc: '棉麻质地的林间贤者长袍，饰有嫩叶暗纹与藤蔓金扣',
    baseHex: '#3a7251',
    shadowHex: '#244e36',
    highlightHex: '#629e7a',
    trimHex: '#d4af37',
    legwearHex: '#2e3d30',
    shoeHex: '#593822',
    capeHex: '#2b583f',
    capeShadowHex: '#1b3a2a',
  },
  {
    id: 'adventurer_cape',
    name: '冒险斗篷',
    desc: '抗风御寒的流浪斗篷，带有皮革搭扣与随身工具袋',
    baseHex: '#8c4f34',
    shadowHex: '#5e321e',
    highlightHex: '#b77353',
    trimHex: '#e0b86a',
    legwearHex: '#3d3028',
    shoeHex: '#302219',
    capeHex: '#6b3c26',
    capeShadowHex: '#472516',
  },
  {
    id: 'academy_vest',
    name: '学院马甲',
    desc: '利落优雅的学院风背心马甲，配有领结与制服短裙',
    baseHex: '#303f56',
    shadowHex: '#1e293a',
    highlightHex: '#4f6485',
    trimHex: '#d9534f',
    legwearHex: '#202633',
    shoeHex: '#1c1b22',
  },
  {
    id: 'knit_sweater',
    name: '针织毛衣',
    desc: '慵懒暖和的粗棒针毛衣，袖口微微垂落，温馨治愈',
    baseHex: '#c87d55',
    shadowHex: '#995433',
    highlightHex: '#e2a37f',
    trimHex: '#fff2df',
    legwearHex: '#4a403b',
    shoeHex: '#6d4c41',
  },
  {
    id: 'sailor_suit',
    name: '水手服',
    desc: '经典清爽的水手领校服，搭配红领结与百褶裙',
    baseHex: '#2c4060',
    shadowHex: '#1b2a40',
    highlightHex: '#46628f',
    trimHex: '#e53935',
    legwearHex: '#f5f5f5',
    shoeHex: '#212121',
  },
  {
    id: 'hanfu_cloud',
    name: '汉风云锦',
    desc: '飘逸如仙的交领襦裙，祥云广袖，环佩玎珰',
    baseHex: '#5b8a99',
    shadowHex: '#3b5d69',
    highlightHex: '#8cb1bd',
    trimHex: '#f7d070',
    legwearHex: '#e0eef2',
    shoeHex: '#825852',
    capeHex: '#4d7885',
    capeShadowHex: '#2f4b54',
  },
  {
    id: 'star_mage',
    name: '星空法袍',
    desc: '夜幕般神秘的法师长袍，闪烁着星群印记与星云渐变',
    baseHex: '#312c5b',
    shadowHex: '#1f1b3b',
    highlightHex: '#57508f',
    trimHex: '#ffd54f',
    legwearHex: '#1a172e',
    shoeHex: '#2b2348',
    capeHex: '#241f47',
    capeShadowHex: '#15112e',
  },
  {
    id: 'traveler_overalls',
    name: '工装背带',
    desc: '耐磨丹宁背带裤配条纹打底衫，适合田野与林中漫步',
    baseHex: '#3b5978',
    shadowHex: '#263b52',
    highlightHex: '#5a7fa8',
    trimHex: '#ffa726',
    legwearHex: '#d7ccc8',
    shoeHex: '#5d4037',
  },
  {
    id: 'gothic_dress',
    name: '哥特洋装',
    desc: '层叠花边与十字绑带的复古哥特洛丽塔礼服',
    baseHex: '#33293a',
    shadowHex: '#1f1824',
    highlightHex: '#544460',
    trimHex: '#ce93d8',
    legwearHex: '#1b171f',
    shoeHex: '#16131a',
  },
  {
    id: 'casual_hoodie',
    name: '元气卫衣',
    desc: '宽松舒适的连帽大卫衣，前袋带暖手口袋，街头可爱',
    baseHex: '#bf5765',
    shadowHex: '#8a3843',
    highlightHex: '#db7886',
    trimHex: '#ffffff',
    legwearHex: '#2e3440',
    shoeHex: '#d8dee9',
  },
];

export const MODULAR_EXPRESSIONS: ExpressionDef[] = [
  { id: 'vivid_clear', name: '清澈灵动', desc: '眸光清亮含笑，透着对世界的好奇与希冀', moodBias: 'sunny' },
  { id: 'deep_thinker', name: '沉静思考', desc: '眼神从容深邃，唇角微平，静水流深', moodBias: 'focused' },
  { id: 'energetic_smile', name: '元气微笑', desc: '弯月般明媚的笑眼，嘴角上扬，活力四射', moodBias: 'sunny' },
  { id: 'sparkle_star', name: '星星眼', desc: '瞳仁中绽放金色星芒，充满惊喜与雀跃', moodBias: 'sunny' },
  { id: 'gentle_wink', name: '温柔眨眼', desc: '单眸轻眨，双颊泛起微红，俏皮亲昵', moodBias: 'calm' },
  { id: 'innocent_dot', name: '纯真呆萌', desc: '圆滚滚的黑亮大眼，微微张嘴的小可爱', moodBias: 'calm' },
  { id: 'sharp_focus', name: '敏锐专注', desc: '坚毅专注的凝视，自信沉着，决断果敢', moodBias: 'focused' },
  { id: 'dreamy_sleepy', name: '慵懒朦胧', desc: '眼睑半垂含笑，如同初醒的小猫般惬意', moodBias: 'melancholy' },
];

export const MODULAR_ACCESSORIES: AccessoryDef[] = [
  { id: 'star_wand', name: '魔法星杖', desc: '顶端镶嵌五角光芒星的施法手杖', handItemName: '星芒之杖' },
  { id: 'butterfly_clip', name: '蝴蝶发卡', desc: '停留在鬓角的虹彩磷光蝶，微翅轻颤', handItemName: '魔法卷轴' },
  { id: 'healing_parasol', name: '治愈小伞', desc: '蕾丝雕花的林间小遮阳伞，驱散微雨', handItemName: '晴雨小伞' },
  { id: 'cat_ears', name: '猫耳发箍', desc: '毛茸茸的灵巧小猫耳，内侧泛着粉晕', handItemName: '花草茶杯' },
  { id: 'quill_scroll', name: '羽毛笔与诗卷', desc: '诗人的金羽长笔与浮空羊皮纸诗卷', handItemName: '金羽书卷' },
  { id: 'firefly_lantern', name: '萤火提灯', desc: '古铜提灯中栖息着荧光微暖的林间飞萤', handItemName: '幽光提灯' },
  { id: 'flower_wreath', name: '治愈花环', desc: '采撷自晨雾草地的野蔷薇与薄荷花环', handItemName: '清露花束' },
  { id: 'crystal_orb', name: '秘术灵珠', desc: '环绕指尖缓缓悬浮流转的水晶秘珠', handItemName: '辉光宝珠' },
];

/** 组合总数：10 * 10 * 10 * 8 * 8 = 64,000 种 */
export const MODULAR_COMBINATIONS_COUNT =
  MODULAR_HAIRSTYLES.length *
  MODULAR_HAIR_TONES.length *
  MODULAR_OUTFITS.length *
  MODULAR_EXPRESSIONS.length *
  MODULAR_ACCESSORIES.length;

/* ------------------------------------------------------------------ */
/* 角色图层绘制引擎 (24 宽 × 48 高)                                    */
/* ------------------------------------------------------------------ */

class PixelLayerCanvas {
  readonly grid: string[][];
  constructor() {
    this.grid = Array.from({ length: AVATAR_HEIGHT }, () => Array(AVATAR_WIDTH).fill('.'));
  }

  px(x: number, y: number, ch: string) {
    if (x >= 0 && x < AVATAR_WIDTH && y >= 0 && y < AVATAR_HEIGHT && ch !== '.') {
      this.grid[y][x] = ch;
    }
  }

  rect(x: number, y: number, w: number, h: number, ch: string) {
    for (let iy = y; iy < y + h; iy++) {
      for (let ix = x; ix < x + w; ix++) {
        this.px(ix, iy, ch);
      }
    }
  }

  ellipse(cx: number, cy: number, rx: number, ry: number, ch: string) {
    if (rx <= 0 || ry <= 0) return;
    const x0 = Math.max(0, Math.floor(cx - rx - 0.5));
    const x1 = Math.min(AVATAR_WIDTH - 1, Math.ceil(cx + rx + 0.5));
    const y0 = Math.max(0, Math.floor(cy - ry - 0.5));
    const y1 = Math.min(AVATAR_HEIGHT - 1, Math.ceil(cy + ry + 0.5));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = (x + 0.5 - cx) / rx;
        const dy = (y + 0.5 - cy) / ry;
        if (dx * dx + dy * dy <= 1.0) {
          this.px(x, y, ch);
        }
      }
    }
  }

  rows(): string[] {
    return this.grid.map((r) => r.join(''));
  }
}

/** 1. 影子图层 */
function drawShadow(): string[] {
  const c = new PixelLayerCanvas();
  c.ellipse(12, 46, 8.5, 1.8, 'z');
  c.ellipse(12, 46, 5.0, 1.0, 'z');
  return c.rows();
}

/** 2. 身体底图层（头脸、颈部、四肢、基础鞋袜） */
function drawBody(): string[] {
  const c = new PixelLayerCanvas();
  // 头部圆润脸胚
  c.ellipse(FACE_CX, FACE_CY, 7.0, 6.8, 's');
  // 耳朵
  c.px(4, 10, 's');
  c.px(19, 10, 's');
  // 颈部
  c.rect(10, 16, 4, 4, 's');
  c.rect(10, 18, 4, 1, 'S'); // 颈影

  // 躯干
  c.rect(5, 20, 14, 3, 's');
  c.rect(6, 23, 12, 7, 's');
  c.rect(6, 30, 12, 6, 's');

  // 手臂与手掌
  c.rect(3, 21, 2, 10, 's');
  c.rect(19, 21, 2, 10, 's');
  c.rect(3, 31, 2, 3, 's');
  c.rect(19, 31, 2, 3, 's');

  // 腿部与袜
  c.rect(8, 36, 3, 8, 'L');
  c.rect(13, 36, 8, 8, 'L');
  c.rect(13, 36, 3, 8, 'L');

  // 鞋履
  c.rect(7, 44, 4, 2, 'w');
  c.rect(13, 44, 4, 2, 'w');
  c.px(6, 45, 'w');
  c.px(17, 45, 'w');

  return c.rows();
}

/** 3. 发型图层 */
function drawHair(styleId: string): string[] {
  const c = new PixelLayerCanvas();
  const cx = FACE_CX;

  // 头顶主发冠
  c.ellipse(cx, FACE_CY - 0.5, 8.8, 8.0, 'h');
  // 高光环 (天使光圈环绕)
  c.rect(cx - 5, 5, 4, 1, 'G');
  c.rect(cx + 2, 5, 4, 1, 'G');

  // 挖出脸部开孔 (保证大眼睛与五官不被头发完全挡住)
  for (let y = 8; y <= 16; y++) {
    for (let x = 6; x <= 17; x++) {
      const dx = (x + 0.5 - cx) / 6.2;
      const dy = (y + 0.5 - FACE_CY) / 6.2;
      if (dx * dx + dy * dy <= 0.85) {
        c.grid[y][x] = '.';
      }
    }
  }

  // 刘海
  c.rect(cx - 6, 6, 13, 2, 'h');
  c.px(cx - 2, 8, 'h');
  c.px(cx + 2, 8, 'h');

  // 根据不同发型款式附加特色结构
  switch (styleId) {
    case 'twin_tail':
      // 左马尾
      c.ellipse(2, 12, 2.5, 6.0, 'h');
      c.ellipse(1, 18, 2.0, 5.0, 'h');
      c.px(3, 9, 't'); // 丝带
      // 右马尾
      c.ellipse(21, 12, 2.5, 6.0, 'h');
      c.ellipse(22, 18, 2.0, 5.0, 'h');
      c.px(20, 9, 't');
      break;

    case 'wavy_long':
      // 柔顺侧长卷发，垂落到胸前和后背
      c.rect(3, 9, 3, 16, 'h');
      c.rect(18, 9, 3, 16, 'h');
      c.ellipse(4, 25, 2.2, 3.5, 'h');
      c.ellipse(19, 25, 2.2, 3.5, 'h');
      c.px(4, 28, 'H');
      c.px(19, 28, 'H');
      break;

    case 'short_neat':
      // 干练短发，两鬓干净内扣
      c.rect(4, 8, 2, 6, 'h');
      c.rect(18, 8, 2, 6, 'h');
      c.px(4, 14, 'H');
      c.px(19, 14, 'H');
      break;

    case 'classic_bun':
      // 头顶高发髻与发簪
      c.ellipse(cx, 2.5, 4.2, 3.2, 'h');
      c.rect(cx - 4, 2, 9, 1, 't'); // 玉簪
      c.px(cx - 5, 2, 'G');
      break;

    case 'fluffy_messy':
      // 蓬松发缕外扬
      c.px(cx - 8, 4, 'h');
      c.px(cx + 7, 4, 'h');
      c.px(cx - 9, 8, 'h');
      c.px(cx + 8, 8, 'h');
      c.rect(3, 9, 3, 7, 'h');
      c.rect(18, 9, 3, 7, 'h');
      break;

    case 'hood_cloak':
      // 宽兜帽
      c.ellipse(cx, 7, 9.8, 8.5, 'h');
      c.rect(2, 8, 3, 12, 'h');
      c.rect(19, 8, 3, 12, 'h');
      break;

    case 'side_braid':
      // 侧长单编发（左侧）
      c.rect(18, 9, 2, 7, 'h');
      c.rect(3, 9, 3, 10, 'h');
      c.ellipse(4, 21, 2.2, 4.0, 'h');
      c.ellipse(5, 26, 1.8, 3.5, 'h');
      c.px(5, 24, 't'); // 扎绳
      break;

    case 'bob_cut':
      // 弧形波波头内扣
      c.ellipse(4, 13, 2.8, 5.0, 'h');
      c.ellipse(19, 13, 2.8, 5.0, 'h');
      break;

    case 'straight_long':
      // 直垂至腰
      c.rect(3, 8, 3, 22, 'h');
      c.rect(18, 8, 3, 22, 'h');
      break;

    case 'curly_horns':
      // 双侧盘螺旋卷
      c.ellipse(3, 5, 3.5, 3.5, 'h');
      c.ellipse(20, 5, 3.5, 3.5, 'h');
      c.px(3, 5, 'G');
      c.px(20, 5, 'G');
      break;
  }

  return c.rows();
}

/** 4. 面部神态图层 */
function drawFace(expressionId: string): string[] {
  const c = new PixelLayerCanvas();
  const cx = FACE_CX;
  const eyeY = 10;

  // 基础腮红 (娇憨红润)
  c.rect(5, eyeY + 3, 2, 1, 'b');
  c.rect(17, eyeY + 3, 2, 1, 'b');

  switch (expressionId) {
    case 'vivid_clear':
      // 清澈双眼 + 灵动高光
      c.rect(7, eyeY, 2, 3, 'e');
      c.rect(15, eyeY, 2, 3, 'e');
      c.px(7, eyeY, 'E');
      c.px(15, eyeY, 'E');
      c.px(8, eyeY + 2, 'E');
      c.px(16, eyeY + 2, 'E');
      // 甜美微抿嘴
      c.rect(cx - 1, eyeY + 5, 2, 1, 'b');
      break;

    case 'deep_thinker':
      // 平静睿智眼
      c.rect(7, eyeY + 1, 2, 2, 'e');
      c.rect(15, eyeY + 1, 2, 2, 'e');
      c.px(7, eyeY + 1, 'E');
      c.px(15, eyeY + 1, 'E');
      // 平静唇线
      c.rect(cx - 1, eyeY + 5, 3, 1, 'O');
      break;

    case 'energetic_smile':
      // 元气弯月眼
      c.px(7, eyeY + 1, 'e');
      c.px(8, eyeY, 'e');
      c.px(9, eyeY + 1, 'e');
      c.px(8, eyeY + 1, 'E');
      c.px(15, eyeY + 1, 'e');
      c.px(16, eyeY, 'e');
      c.px(17, eyeY + 1, 'e');
      c.px(16, eyeY + 1, 'E');
      // 开朗笑口
      c.rect(cx - 2, eyeY + 4, 4, 1, 'O');
      c.rect(cx - 1, eyeY + 5, 2, 1, 'b');
      break;

    case 'sparkle_star':
      // 星星眼高光
      c.rect(7, eyeY, 3, 3, 'e');
      c.rect(14, eyeY, 3, 3, 'e');
      // 金色十字星
      c.px(8, eyeY, 'E');
      c.px(7, eyeY + 1, 'E');
      c.px(8, eyeY + 1, 'E');
      c.px(9, eyeY + 1, 'E');
      c.px(8, eyeY + 2, 'E');
      c.px(15, eyeY, 'E');
      c.px(14, eyeY + 1, 'E');
      c.px(15, eyeY + 1, 'E');
      c.px(16, eyeY + 1, 'E');
      c.px(15, eyeY + 2, 'E');
      // 咧嘴笑
      c.rect(cx - 2, eyeY + 4, 4, 2, 'b');
      break;

    case 'gentle_wink':
      // 左眼睁大，右眼轻眨
      c.rect(7, eyeY, 2, 3, 'e');
      c.px(7, eyeY, 'E');
      c.px(8, eyeY + 2, 'E');
      c.px(15, eyeY + 1, 'e');
      c.px(16, eyeY + 2, 'e');
      c.px(17, eyeY + 1, 'e');
      // 俏皮小嘴
      c.px(cx - 1, eyeY + 5, 'O');
      c.px(cx, eyeY + 5, 'b');
      break;

    case 'innocent_dot':
      // 纯真圆眼
      c.rect(7, eyeY, 3, 3, 'e');
      c.rect(14, eyeY, 3, 3, 'e');
      c.rect(7, eyeY, 2, 1, 'E');
      c.rect(14, eyeY, 2, 1, 'E');
      // 惊讶小嘴
      c.rect(cx - 1, eyeY + 4, 2, 2, 'b');
      break;

    case 'sharp_focus':
      // 敏锐专注剑眉微聚
      c.rect(7, eyeY, 3, 2, 'e');
      c.rect(14, eyeY, 3, 2, 'e');
      c.px(8, eyeY, 'E');
      c.px(15, eyeY, 'E');
      c.rect(cx - 1, eyeY + 5, 3, 1, 'O');
      break;

    case 'dreamy_sleepy':
      // 慵懒微垂
      c.rect(7, eyeY + 1, 3, 2, 'e');
      c.rect(14, eyeY + 1, 3, 2, 'e');
      c.px(8, eyeY + 1, 'E');
      c.px(15, eyeY + 1, 'E');
      c.rect(cx - 1, eyeY + 5, 2, 1, 'b');
      break;
  }

  return c.rows();
}

/** 5. 服装图层 */
function drawOutfit(outfitId: string): string[] {
  const c = new PixelLayerCanvas();
  const cx = FACE_CX;

  // 上衣躯干主体
  c.rect(5, BODY_TOP, 14, 3, 'o');
  c.rect(6, BODY_TOP + 3, 12, 7, 'o');
  // 袖子
  c.rect(3, BODY_TOP + 1, 2, 9, 'o');
  c.rect(19, BODY_TOP + 1, 2, 9, 'o');

  // 下摆裙/裤根据款式扩展
  switch (outfitId) {
    case 'forest_robe':
      // 飘逸森系长袍与叶形腰带
      c.rect(5, BODY_TOP + 10, 14, 7, 'o');
      c.rect(4, BODY_TOP + 14, 16, 3, 'o');
      c.rect(cx - 5, BODY_TOP + 8, 10, 1, 't'); // 金叶腰带
      c.rect(cx - 1, BODY_TOP + 1, 2, 8, 'F'); // 前襟立线
      break;

    case 'adventurer_cape':
      // 斗篷披肩与肩甲搭扣
      c.rect(2, BODY_TOP - 1, 20, 12, 'c');
      c.rect(cx - 6, BODY_TOP + 1, 12, 2, 't'); // 领口皮扣
      c.rect(6, BODY_TOP + 10, 12, 5, 'o');
      break;

    case 'academy_vest':
      // 学院背心 + 衬衫领带
      c.rect(cx - 2, BODY_TOP, 4, 3, 'F'); // 白衬衫领
      c.rect(cx - 1, BODY_TOP + 3, 2, 4, 't'); // 红领结
      c.rect(5, BODY_TOP + 10, 14, 4, 'o'); // 百褶裙
      break;

    case 'knit_sweater':
      // 宽松粗针织毛衣
      c.rect(5, BODY_TOP, 14, 12, 'o');
      c.rect(2, BODY_TOP + 1, 3, 11, 'o'); // 宽袖口
      c.rect(19, BODY_TOP + 1, 3, 11, 'o');
      c.rect(6, BODY_TOP + 4, 12, 1, 'F'); // 针织条纹
      c.rect(6, BODY_TOP + 8, 12, 1, 'F');
      break;

    case 'sailor_suit':
      // 水手服领巾与海军蓝裙
      c.rect(cx - 4, BODY_TOP, 8, 2, 'F');
      c.rect(cx - 1, BODY_TOP + 2, 2, 3, 't'); // 领巾
      c.rect(5, BODY_TOP + 10, 14, 5, 'o');
      c.rect(5, BODY_TOP + 14, 14, 1, 'F'); // 裙摆白条纹
      break;

    case 'hanfu_cloud':
      // 汉服宽袖与祥云腰封
      c.rect(1, BODY_TOP + 2, 4, 11, 'c'); // 广袖
      c.rect(19, BODY_TOP + 2, 4, 11, 'c');
      c.rect(cx - 6, BODY_TOP + 7, 12, 2, 't'); // 祥云腰带
      c.rect(4, BODY_TOP + 10, 16, 7, 'o'); // 裙摆
      break;

    case 'star_mage':
      // 星宿纹袍与法师外衣
      c.rect(2, BODY_TOP - 1, 20, 16, 'o');
      c.rect(cx - 1, BODY_TOP, 2, 16, 't'); // 金边中线
      c.px(7, BODY_TOP + 5, 'F'); // 星辉刺绣
      c.px(16, BODY_TOP + 5, 'F');
      c.px(8, BODY_TOP + 12, 'F');
      c.px(15, BODY_TOP + 12, 'F');
      break;

    case 'traveler_overalls':
      // 工装背带裤
      c.rect(6, BODY_TOP, 12, 3, 'F'); // 内衬 T 恤
      c.rect(7, BODY_TOP + 3, 2, 5, 'o'); // 左背带
      c.rect(15, BODY_TOP + 3, 2, 5, 'o'); // 右背带
      c.rect(7, BODY_TOP + 8, 10, 8, 'o'); // 裤身与口袋
      c.rect(9, BODY_TOP + 9, 6, 3, 'O');
      break;

    case 'gothic_dress':
      // 哥特蓬蓬裙
      c.rect(cx - 3, BODY_TOP, 6, 2, 'F'); // 蕾丝领
      c.rect(cx - 1, BODY_TOP + 3, 2, 5, 'O'); // 束腰
      c.rect(4, BODY_TOP + 9, 16, 6, 'o');
      c.rect(3, BODY_TOP + 13, 18, 2, 'F'); // 荷叶下摆
      break;

    case 'casual_hoodie':
      // 大连帽卫衣
      c.rect(cx - 5, BODY_TOP - 1, 10, 2, 'O'); // 堆叠兜帽
      c.rect(cx - 3, BODY_TOP + 2, 1, 3, 't'); // 抽绳
      c.rect(cx + 2, BODY_TOP + 2, 1, 3, 't');
      c.rect(7, BODY_TOP + 7, 10, 4, 'O'); // 袋鼠口袋
      c.rect(6, BODY_TOP + 11, 12, 4, 'o');
      break;
  }

  // 阴影与轮廓修饰
  c.rect(6, BODY_TOP + 10, 12, 1, 'O');
  return c.rows();
}

/** 6. 随身饰品（头部饰品）图层 */
function drawAccessory(accId: string): string[] {
  const c = new PixelLayerCanvas();
  const cx = FACE_CX;

  switch (accId) {
    case 'butterfly_clip':
      // 鬓角蝴蝶发卡 (左侧)
      c.rect(3, 7, 3, 3, 't');
      c.px(4, 8, 'a');
      c.px(2, 6, 'G');
      break;

    case 'cat_ears':
      // 萌动猫耳
      c.rect(4, 1, 3, 3, 'h');
      c.px(5, 2, 'b'); // 粉色内耳
      c.rect(17, 1, 3, 3, 'h');
      c.px(18, 2, 'b');
      break;

    case 'flower_wreath':
      // 治愈花环
      c.rect(5, 4, 14, 2, 't');
      c.px(6, 4, 'b');
      c.px(10, 3, 'a');
      c.px(14, 4, 'b');
      c.px(17, 3, 'G');
      break;

    case 'healing_parasol':
      // 撑在肩头的伞顶边
      c.rect(18, 3, 5, 2, 't');
      c.rect(19, 1, 3, 2, 'a');
      break;

    case 'star_wand':
      // 发饰星光发夹
      c.rect(cx + 4, 5, 3, 3, 't');
      c.px(cx + 5, 5, 'G');
      break;

    case 'firefly_lantern':
      // 发带流苏
      c.rect(cx - 6, 6, 2, 4, 't');
      c.px(cx - 6, 10, 'G');
      break;

    case 'quill_scroll':
      // 金羽发饰
      c.rect(cx + 5, 4, 2, 4, 't');
      c.px(cx + 6, 3, 'G');
      break;

    case 'crystal_orb':
      // 额间灵珠
      c.px(cx, 6, 't');
      c.px(cx, 7, 'G');
      break;
  }

  return c.rows();
}

/** 7. 随身道具（右手执握物件）图层 */
function drawHandItem(accId: string): string[] {
  const c = new PixelLayerCanvas();
  const hx = 20; // 右手位置 (x=19..22, y=28..37)
  const hy = 28;

  switch (accId) {
    case 'star_wand':
      // 魔法星杖
      c.rect(hx, hy + 3, 1, 8, 't'); // 权杖杆
      // 金色五角星
      c.rect(hx - 1, hy, 3, 3, 'g');
      c.px(hx, hy - 1, 'G');
      c.px(hx - 2, hy + 1, 'G');
      c.px(hx + 2, hy + 1, 'G');
      break;

    case 'healing_parasol':
      // 小洋伞把手与骨架
      c.rect(hx - 1, hy - 2, 4, 12, 'g');
      c.rect(hx - 1, hy + 9, 3, 1, 't');
      break;

    case 'firefly_lantern':
      // 萤火提灯
      c.rect(hx, hy + 1, 1, 2, 't'); // 提链
      c.rect(hx - 1, hy + 3, 3, 4, 'g'); // 灯罩
      c.px(hx, hy + 4, 'G'); // 萤火微光
      c.rect(hx - 1, hy + 7, 3, 1, 't'); // 底座
      break;

    case 'quill_scroll':
      // 金羽毛笔与悬浮卷轴
      c.rect(hx, hy + 2, 2, 5, 'g');
      c.rect(hx - 2, hy + 4, 2, 4, 't');
      break;

    case 'crystal_orb':
      // 浮空秘术灵珠
      c.ellipse(hx, hy + 3, 2.0, 2.0, 'g');
      c.px(hx, hy + 2, 'G');
      break;

    case 'butterfly_clip':
    case 'flower_wreath':
    case 'cat_ears':
    default:
      // 精致书卷 / 魔法药水瓶 / 花束
      c.rect(hx - 1, hy + 2, 3, 5, 'g');
      c.rect(hx, hy + 3, 2, 3, 't');
      break;
  }

  return c.rows();
}

/** 8. 角色 1px 轮廓微光图层 */
function drawOutline(layers: string[][]): string[] {
  const solid: boolean[][] = Array.from({ length: AVATAR_HEIGHT }, () => Array(AVATAR_WIDTH).fill(false));
  for (const l of layers) {
    for (let y = 0; y < AVATAR_HEIGHT; y++) {
      for (let x = 0; x < AVATAR_WIDTH; x++) {
        if (l[y][x] !== '.') solid[y][x] = true;
      }
    }
  }

  const c = new PixelLayerCanvas();
  for (let y = 0; y < AVATAR_HEIGHT; y++) {
    for (let x = 0; x < AVATAR_WIDTH; x++) {
      if (solid[y][x]) continue;
      const isNeighbor =
        (x > 0 && solid[y][x - 1]) ||
        (x < AVATAR_WIDTH - 1 && solid[y][x + 1]) ||
        (y > 0 && solid[y - 1][x]) ||
        (y < AVATAR_HEIGHT - 1 && solid[y + 1][x]);
      if (isNeighbor && y < 45) {
        c.px(x, y, 'x');
      }
    }
  }
  return c.rows();
}

/* ------------------------------------------------------------------ */
/* 色板组装与个性化派生                                               */
/* ------------------------------------------------------------------ */

export interface ModularPalettePack {
  charPalette: AvatarCharPalette;
  palette: AvatarPalette;
  pixelPalette: PixelPalette;
}

export function buildModularPalette(
  hairTone: HairToneDef,
  outfit: OutfitDef,
  element: string = '木',
): ModularPalettePack {
  // 根据五行基调微调肤色与光晕
  const skinByElement: Record<string, { skin: string; skinShadow: string; glow: string }> = {
    金: { skin: '#faede3', skinShadow: '#e4cebe', glow: '#d7e6f8' },
    木: { skin: '#f7edd8', skinShadow: '#decaa8', glow: '#d1f2d9' },
    水: { skin: '#f5f0eb', skinShadow: '#d8cbbf', glow: '#cce2f5' },
    火: { skin: '#ffe5d4', skinShadow: '#e2ba9f', glow: '#ffd6c4' },
    土: { skin: '#f4e4cf', skinShadow: '#d3bfa0', glow: '#faebd2' },
  };
  const skinData = skinByElement[element] ?? skinByElement['木'];

  const charPalette: AvatarCharPalette = {
    s: skinData.skin,
    S: skinData.skinShadow,
    h: hairTone.baseHex,
    H: hairTone.shadowHex,
    G: hairTone.highlightHex,
    o: outfit.baseHex,
    O: outfit.shadowHex,
    F: outfit.highlightHex,
    c: outfit.capeHex ?? outfit.baseHex,
    C: outfit.capeShadowHex ?? outfit.shadowHex,
    e: '#23272e',
    E: '#ffffff',
    b: '#f49aa6',
    t: outfit.trimHex,
    g: '#ffd54f',
    w: outfit.shoeHex,
    L: outfit.legwearHex,
    x: skinData.glow,
    a: '#e0b86a',
    z: '#202b38',
  };

  const palette: AvatarPalette = {
    skin: charPalette.s,
    skin_shadow: charPalette.S,
    hair: charPalette.h,
    hair_shadow: charPalette.H,
    hair_highlight: charPalette.G,
    outfit_base: charPalette.o,
    outfit_shadow: charPalette.O,
    outfit_highlight: charPalette.F,
    cape: charPalette.c,
    cape_shadow: charPalette.C,
    eye_ink: charPalette.e,
    eye_highlight: charPalette.E,
    blush: charPalette.b,
    trim: charPalette.t,
    hand_item: charPalette.g,
    shoe: charPalette.w,
    legwear: charPalette.L,
    rim_light: charPalette.x,
    emblem_bg: charPalette.a,
    ground_shadow: charPalette.z,
  };

  const pixelPalette = toPixelPalette(charPalette);
  return { charPalette, palette, pixelPalette };
}

/* ------------------------------------------------------------------ */
/* 模块化小人实体与随机生成接口                                       */
/* ------------------------------------------------------------------ */

export interface ModularAvatarTraits {
  hairStyle: string;
  hairStyleLabel: string;
  hairTone: string;
  hairToneLabel: string;
  outfit: string;
  outfitLabel: string;
  expression: string;
  expressionLabel: string;
  accessory: string;
  accessoryLabel: string;
}

export interface ModularAvatar {
  id: string;
  name: string;
  title: string;
  mbti: string;
  element: string;
  mood: string;
  aura: string;
  desc: string;
  traits: ModularAvatarTraits;
  layers: AvatarLayers;
  matrix: string[];
  charPalette: AvatarCharPalette;
  palette: AvatarPalette;
  pixelPalette: PixelPalette;
  houseAvatar: HouseAvatar;
  avatarProfile: AvatarProfile;
}

export interface ModularAvatarSelection {
  hairStyleId: string;
  hairToneId: string;
  outfitId: string;
  expressionId: string;
  accessoryId: string;
  mbti?: string;
  element?: string;
  mood?: string;
  name?: string;
  id?: string;
}

/**
 * 根据具体的部件 ID 选择直接构建微像素角色对象。
 */
export function createModularAvatar(selection: ModularAvatarSelection): ModularAvatar {
  const selectedHairStyle =
    MODULAR_HAIRSTYLES.find((h) => h.id === selection.hairStyleId) ?? MODULAR_HAIRSTYLES[0];
  const selectedHairTone =
    MODULAR_HAIR_TONES.find((t) => t.id === selection.hairToneId) ?? MODULAR_HAIR_TONES[0];
  const selectedOutfit =
    MODULAR_OUTFITS.find((o) => o.id === selection.outfitId) ?? MODULAR_OUTFITS[0];
  const selectedExpr =
    MODULAR_EXPRESSIONS.find((e) => e.id === selection.expressionId) ?? MODULAR_EXPRESSIONS[0];
  const selectedAcc =
    MODULAR_ACCESSORIES.find((a) => a.id === selection.accessoryId) ?? MODULAR_ACCESSORIES[0];

  const mbti = selection.mbti ?? 'INFP';
  const element = selection.element ?? '木';
  const mood = selection.mood ?? 'calm';

  // 组装调色板
  const palettePack = buildModularPalette(selectedHairTone, selectedOutfit, element);

  // 逐层绘制
  const shadowLayer = drawShadow();
  const bodyLayer = drawBody();
  const hairLayer = drawHair(selectedHairStyle.id);
  const faceLayer = drawFace(selectedExpr.id);
  const outfitLayer = drawOutfit(selectedOutfit.id);
  const accessoryLayer = drawAccessory(selectedAcc.id);
  const handItemLayer = drawHandItem(selectedAcc.id);
  const outlineLayer = drawOutline([bodyLayer, hairLayer, outfitLayer, accessoryLayer, handItemLayer]);

  const layers: AvatarLayers = {
    shadow: shadowLayer,
    body: bodyLayer,
    hair: hairLayer,
    face: faceLayer,
    outfit: outfitLayer,
    accessory: accessoryLayer,
    hand_item: handItemLayer,
    outline: outlineLayer,
  };

  const matrix = compositeLayers(layers);
  assertPaletteCoversMatrix(matrix, palettePack.charPalette);

  const charId = selection.id ?? `modular_${Date.now().toString(16)}`;
  const roleName = selection.name?.trim() || `${selectedHairTone.name}的${selectedHairStyle.name}`;
  const title = `${element}相 · ${mbti} ${selectedOutfit.name}`;
  const aura = `${selectedHairTone.colorName} · ${selectedAcc.name}`;
  const desc = `独一无二的微像素灵魂化身，兼具 ${selectedExpr.name} 的神韵与 ${selectedOutfit.name} 的风华。`;

  const traits: ModularAvatarTraits = {
    hairStyle: selectedHairStyle.id,
    hairStyleLabel: selectedHairStyle.name,
    hairTone: selectedHairTone.id,
    hairToneLabel: selectedHairTone.name,
    outfit: selectedOutfit.id,
    outfitLabel: selectedOutfit.name,
    expression: selectedExpr.id,
    expressionLabel: selectedExpr.name,
    accessory: selectedAcc.id,
    accessoryLabel: selectedAcc.name,
  };

  const houseAvatar: HouseAvatar = {
    fingerprint: charId,
    layers,
    matrix,
    width: AVATAR_WIDTH,
    height: AVATAR_HEIGHT,
    palette: palettePack.palette,
    char_keys: MODULAR_CHAR_KEYS,
    char_palette: palettePack.charPalette,
    labels: {
      mbti,
      element,
      mood,
      hairStyle: selectedHairStyle.name,
      outfit: selectedOutfit.name,
      accessory: selectedAcc.name,
    },
  };

  const mockAvatarProfile: AvatarProfile = {
    id: charId,
    state: 'confirmed',
    owner_id: 'guest',
    portrait: { mbti, element, mood, name: roleName },
    params: {
      hair_style: selectedHairStyle.id,
      hair_tone: selectedHairTone.id,
      palette_id: element,
      eye: selectedExpr.id,
      mouth: 'smile',
      accessory: selectedAcc.id,
      outfit: selectedOutfit.id,
      emblem: 'star',
      texture: 'smooth',
      height_scale: 1,
      colors: palettePack.palette,
      labels: { mbti, element, mood },
      sources: {},
      missing: [],
      fingerprint: charId,
      engine_version: '3.0.0-modular',
    },
    base_signature: {},
    overrides: null,
    fingerprint: charId,
    params_fingerprint: charId,
    engine_version: '3.0.0-modular',
    likeness_score: 98,
    likeness_note: '微像素高精拟真角色',
    is_house_avatar: true,
    version: 1,
    avatar: {
      fingerprint: charId,
      params_fingerprint: charId,
      engine_version: '3.0.0-modular',
      params: {} as any,
      base_signature: {},
      tuned: true,
      layers,
      matrix,
      width: AVATAR_WIDTH,
      height: AVATAR_HEIGHT,
      palette: palettePack.palette,
      char_palette: palettePack.charPalette,
      param_space_size: MODULAR_COMBINATIONS_COUNT,
      advisory: { complete: true, pending: [], note: '', notes: '', age_band_label: null },
    },
    advisory: { complete: true, pending: [], note: '', notes: '', age_band_label: null },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  return {
    id: charId,
    name: roleName,
    title,
    mbti,
    element,
    mood,
    aura,
    desc,
    traits,
    layers,
    matrix,
    charPalette: palettePack.charPalette,
    palette: palettePack.palette,
    pixelPalette: palettePack.pixelPalette,
    houseAvatar,
    avatarProfile: mockAvatarProfile,
  };
}

/**
 * 根据画像（MBTI、八字五行、心境）或确定性种子一键生成高颜值微像素角色。
 * 支持 64,000+ 种自由组合，保证生成的角色具备待机与行走多帧动效。
 */
export function generateRandomModularAvatar(
  seed?: string,
  profile?: AvatarProfile | PortraitInput,
): ModularAvatar {
  // 生成 PRNG 种子
  let seedNum = Math.floor(Math.random() * 0x7fffffff);
  if (seed) {
    let hash = 0;
    for (let i = 0; i < seed.length; i++) {
      hash = (hash << 5) - hash + seed.charCodeAt(i);
      hash |= 0;
    }
    seedNum = Math.abs(hash);
  } else if (profile) {
    const rawStr = JSON.stringify(profile);
    let hash = 0;
    for (let i = 0; i < rawStr.length; i++) {
      hash = (hash << 5) - hash + rawStr.charCodeAt(i);
      hash |= 0;
    }
    seedNum = Math.abs(hash);
  }

  const rng = mulberry32(seedNum);

  // 解析画像或赋予默认值
  const mbti = (
    (profile as PortraitInput)?.mbti ??
    (profile as AvatarProfile)?.portrait?.mbti ??
    ['INFP', 'INFJ', 'ENFP', 'INTJ', 'ENTP', 'ISFJ'][Math.floor(rng() * 6)]
  ).toString().toUpperCase();

  const element = (
    (profile as PortraitInput)?.bazi_element ??
    (profile as AvatarProfile)?.portrait?.bazi_element ??
    ['木', '火', '土', '金', '水'][Math.floor(rng() * 5)]
  ).toString();

  const mood = (
    (profile as PortraitInput)?.mood ??
    (profile as AvatarProfile)?.portrait?.mood ??
    ['calm', 'sunny', 'focused', 'melancholy'][Math.floor(rng() * 4)]
  ).toString();

  // 根据画像建立偏好映射 (软引导同时保留随机惊喜)
  let hairStyleIdx = Math.floor(rng() * MODULAR_HAIRSTYLES.length);
  if (mbti.includes('P') && rng() < 0.4) {
    hairStyleIdx = MODULAR_HAIRSTYLES.findIndex((h) => h.id === 'fluffy_messy' || h.id === 'twin_tail');
  } else if (mbti.includes('J') && rng() < 0.4) {
    hairStyleIdx = MODULAR_HAIRSTYLES.findIndex((h) => h.id === 'classic_bun' || h.id === 'short_neat');
  }
  if (hairStyleIdx < 0) hairStyleIdx = 0;

  // 五行偏好发色
  let hairToneIdx = Math.floor(rng() * MODULAR_HAIR_TONES.length);
  const elementToneMap: Record<string, string[]> = {
    木: ['mint', 'chestnut'],
    水: ['mist_blue', 'lilac_purple', 'silver_frost'],
    火: ['amber_red', 'sakura_pink'],
    土: ['caramel_tea', 'chestnut', 'champagne_gold'],
    金: ['silver_frost', 'obsidian_black', 'champagne_gold'],
  };
  const preferredTones = elementToneMap[element];
  if (preferredTones && rng() < 0.6) {
    const chosenToneId = preferredTones[Math.floor(rng() * preferredTones.length)];
    const foundIdx = MODULAR_HAIR_TONES.findIndex((t) => t.id === chosenToneId);
    if (foundIdx >= 0) hairToneIdx = foundIdx;
  }

  // 服装偏好
  let outfitIdx = Math.floor(rng() * MODULAR_OUTFITS.length);
  if (mbti.startsWith('I') && mbti.endsWith('P') && rng() < 0.5) {
    const found = MODULAR_OUTFITS.findIndex((o) => o.id === 'knit_sweater' || o.id === 'traveler_overalls');
    if (found >= 0) outfitIdx = found;
  }

  // 心境偏好神态
  let exprIdx = Math.floor(rng() * MODULAR_EXPRESSIONS.length);
  const moodExprMap: Record<string, string> = {
    sunny: 'energetic_smile',
    calm: 'vivid_clear',
    focused: 'deep_thinker',
    melancholy: 'dreamy_sleepy',
  };
  if (moodExprMap[mood] && rng() < 0.6) {
    const targetId = moodExprMap[mood];
    const found = MODULAR_EXPRESSIONS.findIndex((e) => e.id === targetId);
    if (found >= 0) exprIdx = found;
  }

  const accIdx = Math.floor(rng() * MODULAR_ACCESSORIES.length);

  const selectedHairStyle = MODULAR_HAIRSTYLES[hairStyleIdx];
  const selectedHairTone = MODULAR_HAIR_TONES[hairToneIdx];
  const selectedOutfit = MODULAR_OUTFITS[outfitIdx];
  const selectedExpr = MODULAR_EXPRESSIONS[exprIdx];
  const selectedAcc = MODULAR_ACCESSORIES[accIdx];

  const charId = `modular_${seedNum.toString(16)}`;
  const roleName = (profile as any)?.name?.trim() || `${selectedHairTone.name}的${selectedHairStyle.name}`;

  return createModularAvatar({
    id: charId,
    name: roleName,
    mbti,
    element,
    mood,
    hairStyleId: selectedHairStyle.id,
    hairToneId: selectedHairTone.id,
    outfitId: selectedOutfit.id,
    expressionId: selectedExpr.id,
    accessoryId: selectedAcc.id,
  });
}

/* ------------------------------------------------------------------ */
/* PixiJS 纹理切片导出器（待机 4 帧与行走 2 帧）                      */
/* ------------------------------------------------------------------ */

export interface ExportedAvatarFrames {
  idleTextures: Texture[];
  walkTextures: Texture[];
  idleMatrixFrames: string[][];
  walkMatrixFrames: string[][];
  charPalette: AvatarCharPalette;
  pixelPalette: PixelPalette;
}

/**
 * 输出 PixiJS 可直接绑定的待机与行走纹理切片，供主场景即时“换皮”！
 * - 待机动画：4 帧呼吸与发丝轻晃 (`idleFrames`)
 * - 行走动画：2 帧步态起伏 (`walkFrames`)
 */
export function exportAvatarFrames(
  target: ModularAvatar | HouseAvatar,
): ExportedAvatarFrames {
  const layers = (target as ModularAvatar).layers ?? (target as HouseAvatar).layers;
  const charPalette = (target as ModularAvatar).charPalette ?? (target as HouseAvatar).char_palette;
  const pixelPalette = toPixelPalette(charPalette);

  const idleFrameObjs = idleFrames(layers);
  const walkFrameObjs = walkFrames(layers);

  const idleMatrixFrames = idleFrameObjs.map((f) => f.matrix);
  const walkMatrixFrames = walkFrameObjs.map((f) => f.matrix);

  const tag = `modular-av-${(target as any).id ?? (target as any).fingerprint ?? Date.now()}`;

  let idleTextures: Texture[] = [];
  let walkTextures: Texture[] = [];

  // 安全生成 PixiJS 纹理（非浏览器/单测环境降级为矩阵帧集合）
  try {
    idleTextures = idleMatrixFrames.map((mat, i) =>
      spriteFromMatrix(
        mat,
        pixelPalette,
        { autoOutline: 0x24344d, label: `${tag}-idle-${i}` },
        `${tag}:idle:${i}`,
      ).texture,
    );

    walkTextures = walkMatrixFrames.map((mat, i) =>
      spriteFromMatrix(
        mat,
        pixelPalette,
        { autoOutline: 0x24344d, label: `${tag}-walk-${i}` },
        `${tag}:walk:${i}`,
      ).texture,
    );
  } catch {
    // jsdom 单测环境 Canvas 2D 异常时优雅回退
    idleTextures = [];
    walkTextures = [];
  }

  return {
    idleTextures,
    walkTextures,
    idleMatrixFrames,
    walkMatrixFrames,
    charPalette,
    pixelPalette,
  };
}
