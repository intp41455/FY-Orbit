import type { CabinBackgroundId, CabinHouseId } from './cabinConfig';
import type { GradientStop, PixelPalette } from './cabinPixels';

/**
 * 数码小屋像素美术数据（零外部素材）：全部视觉元素是代码内的字符矩阵
 * （每字符一色）+ 程序化调色板。矩阵经 cabinPixels.spriteFromMatrix 生成纹理。
 *
 * 唯美像素风约定（参考 Eastward / Summerhouse 的现代像素风，非 8-bit 复古）：
 *  - 每个场景 16~32 色丰富调色板，用 3~4 阶同色系色阶表达体积与光影；
 *  - 矩阵内手工放置 1px 轮廓光（r/l 等高亮字符）+ 暗部（大写字符）；
 *  - 1px 深色自动描边由 cabinPixels 的 autoOutline 统一补齐；
 *  - 山洞/雪洞共用同一矩阵（不同调色板），与宠物的换色机制同理。
 */

/* ------------------------------ 色彩工具 ------------------------------ */

export function hexToNumber(hex: string): number {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex);
  return m ? parseInt(m[1], 16) : 0x4fc3f7;
}

export function shade(color: number, factor: number): number {
  const r = Math.min(255, Math.round(((color >> 16) & 0xff) * factor));
  const g = Math.min(255, Math.round(((color >> 8) & 0xff) * factor));
  const b = Math.min(255, Math.round((color & 0xff) * factor));
  return (r << 16) | (g << 8) | b;
}

export function lighten(color: number, factor = 1.3): number {
  return shade(color, factor);
}

/* ------------------------------ 色彩 / 比例工具 ------------------------------ */

/** 相对亮度（Rec.709），用于色阶 / 明度顺序断言（G2-2）。 */
export function luma(color: number): number {
  const r = (color >> 16) & 0xff;
  const g = (color >> 8) & 0xff;
  const b = color & 0xff;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 小人屏显缩放系数（G2-3 基准）。与 cabinScene.layoutActors 共用，保证比例锁定一致。 */
export const PERSON_SCALE = 1.6;

/** 房屋屏显高度 : 小人屏显高度的目标比值（G2-3：解决「房屋矮胖」，目标 1.8-2.2）。 */
export const HOUSE_PERSON_RATIO = 2.0;

/**
 * G2-3：按房屋矩阵高度归一化缩放，使任意房屋的屏显高度恰好 = 小人屏显高度 × HOUSE_PERSON_RATIO。
 * 各房屋矩阵高度不同（22~32 行），统一固定缩放会让矮房屋比小人还小；改为按高度反推缩放系数，
 * 保证比例恒定达标，且不受分辨率 / 窗口高度影响。
 */
export function houseScaleFactor(houseId: CabinHouseId): number {
  const personWorldH = PERSON_WALK_FRAMES[0].length * PERSON_SCALE;
  return (personWorldH * HOUSE_PERSON_RATIO) / CABIN_HOUSE_ART[houseId].rows.length;
}

/* ------------------------------ 房屋（6 种） ------------------------------ */
/* 约定：矩阵底部即房屋落地面，场景里以 (0.5, 1) 锚点贴地。 */

export const VILLA_ROWS: readonly string[] = [
  '......................................lllrr.....................................',
  '...................................lllRRRRrrrr..................................',
  '.................................lllRRRRRRRRrrrr................................',
  '..............................lllRRRRRRRRRRRRRRrrrr.............................',
  '............................lllRRRRRRRRRRRRRRRRRRrrrr...........................',
  '.........................lllRRRRRRRRRRRRRRRRRRRRRRRRrrrr........................',
  '......................lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr.....................',
  '....................lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr...................',
  '.................lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr................',
  '...............lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr..............',
  '............lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr...........',
  '.........lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr........',
  '.......lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr......',
  '....lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr...',
  '..lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr.',
  'llRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrr',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  '...eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee...',
  '..............SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSS..............',
  '..............SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssfffffffffffsssssssssssssssssfffffffffffsssssS..............',
  '..............SssssssfBBBBBBBBBfsssssssssssssssssfBBBBBBBBBfsssssS..............',
  '..............SssssssfBBBBBBBBBfsssssssssssssssssfBBBBBBBBBfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfffffffffffsssssssssssssssssfffffffffffsssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssfffffffffffsssssssssssssssssfffffffffffsssssS..............',
  '..............SssssssfBBBBBBBBBfsssssssssssssssssfBBBBBBBBBfsssssS..............',
  '..............SssssssfBBBBBBBBBfsssssssssssssssssfBBBBBBBBBfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfssssssysssyssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfbbbbbbbbbfsssssssssssssssssfbbbbbbbbbfsssssS..............',
  '..............SssssssfffffffffffsssssssssssssssssfffffffffffsssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............SssssssssssssssssssssssssssssssssssssssssssssssssssS..............',
  '..............tttttttttttttttttttttttttttttttttttttttttttttttttttt..............',
  '..............tttttttttttttttttttttttttttttttttttttttttttttttttttt..............',
  '..............tttttttttttttttttttttttttttttttttttttttttttttttttttt..............',
  '..............tttttttttttttttttttttttttttttttttttttttttttttttttttt..............',
  '..............ttttttttttttttttgggggggggggggggggggggttttttttttttttt..............',
  '..............ttttttttttttttttgggggggggggggggggggggttttttttttttttt..............',

];

export const VILLA_PALETTE: PixelPalette = {
  l: 0xff9e6e, R: 0xe0663f, r: 0xbf4f34, e: 0x9c3d2b,
  w: 0xfdf3e0, s: 0xf2e3c4, S: 0xd9c49c,
  f: 0x8fb8d8, b: 0xbfe3ff, B: 0xeaf8ff,
  D: 0x6b422a, m: 0x8d5a3a, y: 0xffe082,
  t: 0xb8a888, g: 0xcabfa0,
};

export const CABIN_ROWS: readonly string[] = [
  '......................................lllrr.....................................',
  '...................................lllRRRRrrrr..................................',
  '................................lllRRRRRRRRRRrrrr...............................',
  '..............................lllRRRRRRRRRRRRRRrrrr.............................',
  '...........................lllRRRRRRRRRRRRRRRRRRRRrrrr..NNNNNNNNNN..............',
  '........................lllRRRRRRRRRRRRRRRRRRRRRRRRRRrrrrnnnnnnnnN..............',
  '.....................lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrrnnnnnN..............',
  '..................lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrrnnN..............',
  '................lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrrN..............',
  '.............lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr............',
  '..........lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr.........',
  '.......lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr......',
  '....lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr...',
  '..lllRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrrrr.',
  'llRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRrr',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  'RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR',
  '.....rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr.....',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwssssfffffffffffsssssssssssssssssfffffffffffssssww..............',
  '...............wwssssfYYYYYYYYYfsssssssssssssssssfYYYYYYYYYfssssww..............',
  '...............wwSSSSfYYYYYYYYYfSSSSSSSSSSSSSSSSSfYYYYYYYYYfSSSSww..............',
  '...............wwssssfyyyyyyyyyfsssssssssssssssssfyyyyyyyyyfssssww..............',
  '...............wwssssfyyyyyyyyyfsssssssssssssssssfyyyyyyyyyfssssww..............',
  '...............wwssssfyyyyyyyyyfsssssssssssssssssfyyyyyyyyyfssssww..............',
  '...............wwSSSSfyyyyyyyyyfSSSsssssssssssSSSfyyyyyyyyyfSSSSww..............',
  '...............wwssssfyyyyyyyyyfsssssssssssssssssfyyyyyyyyyfssssww..............',
  '...............wwssssfyyyyyyyyyfsssssssssssssssssfyyyyyyyyyfssssww..............',
  '...............wwssssfffffffffffsssssssssssssssssfffffffffffssssww..............',
  '...............wwSSSSSSSSSSSSSSSSSSsssssssssssSSSSSSSSSSSSSSSSSSww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwSSSSSSSSSSSSSSSSSSsssssssssssSSSSSSSSSSSSSSSSSSww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwSSSSSSSSSSSSSSSSSSsssssssssssSSSSSSSSSSSSSSSSSSww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwSSSSSSSSSSSSSSSSSSsssssssssssSSSSSSSSSSSSSSSSSSww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............wwsssssssssssssssssssssssssssssssssssssssssssssssww..............',
  '...............SSSSSSSSSSgggggggggggggggggggggggggggggggSSSSSSSSSS..............',
  '...............SSSSSSSSSSgggggggggggggggggggggggggggggggSSSSSSSSSS..............',
  '...............SSSSSSSSSSgggggggggggggggggggggggggggggggSSSSSSSSSS..............',

];

export const CABIN_PALETTE: PixelPalette = {
  l: 0x7a5340, R: 0x5b3a29, r: 0x462c1e,
  N: 0xaab4b0, n: 0x8f9995,
  w: 0x8d5a3a, s: 0x7c4e32, S: 0x6b422a, g: 0x54351f,
  D: 0x3f2a1c, m: 0x66452e,
  f: 0x4a2f1f, y: 0xffe9a8, Y: 0xfff7cf,
};

/** 山洞与雪洞共用同一穹顶矩阵，靠调色板换色区分（冷蓝雪洞 / 暖灰岩石）。 */
export const CAVE_ROWS: readonly string[] = [
  '..............................llllnnnnnnnnSSSSSSSSS.............................',
  '...........................llllnnnnnnnnnnnSSSSSSSSSSSS..........................',
  '..........................llllnnnnnnnnnnnnSSSSSSSSSSSSS.........................',
  '.........................llllnnnnnnnnnnnnnSSSSSSSSSSSSSS........................',
  '........................llllnnnnnnnnnnnnnnSSSSSSSSSSSSSSS.......................',
  '.......................llllnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSS......................',
  '......................llllnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSS.....................',
  '.....................llllnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSS....................',
  '.....................llllnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSS....................',
  '....................llllnnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSS...................',
  '...................llllnnnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSS..................',
  '...................llllnnnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSS..................',
  '..................llllnnnnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSSS.................',
  '.................llllnnnnnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSSSS................',
  '.................lVVVVVVVVVVVVnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSSSS................',
  '................llvvvvvvvvvvvvnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSSSSS...............',
  '................llvvvvvvvvvvvvnnnnnnnnnnnnSSSSSSSSvvvvvvvvvvvvSSS...............',
  '...............lllVVVVVVVVVVVVnnnnnnnnnnnnSSSSSSSSVVVVVVVVVVVVSSSS..............',
  '...............lllvvvvnvvvvvvvnnnnnnnnnnnnSSSSSSSSvvvvvvvvvvvvSSSS..............',
  '..............llllvnvvnvvvvvvvnnnnnnnnnnnnSSSSSSSSVVVVSVVVVVVVSSSSS.............',
  '..............llllVnVVnVVVVnVVnnnnnndddddddddSSSSSSvvvSvvvvSvvSSSSS.............',
  '.............llllnvnvvnvnvvnvvnnnnndmmmmmmmmmdSSSSSVVVSSVVVSVVSSSSSS............',
  '.............llllnvnvnnvnvvnvvnnnnndmmmmmmmmmdSSSSSSvvSSvvvSSvSSSSSS............',
  '............llllnnnnVnnVnVVnVnnnnndmmmmmmmmmmmdSSSSSVVSSSVVSSVSSSSSSS...........',
  '............llllnnnnvnnvnvnnvnnnnndmmmmmmmmmmmdSSSSSSvSSSvvSSSSSSSSSS...........',
  '...........llllnnnnnvnnnnvnnvnnnndmmmmmmmmmmmmmdSSSSSVSSSSVSSSSSSSSSSS..........',
  '...........llllnnnnnnnnnnVnnVnnnndmmmmmmmmmmmmmdSSSSSSSSSSvSSSSSSSSSSS..........',
  '..........llllnnnnnnnnnnnvnnnnnndmmmmmmmmmmmmmmmdSSSSSSSSSSSSSSSSSSSSSS.........',
  '..........llllnnnnnnnnnnnnnnnnnndmmmmmmmmmmmmmmmdSSSSSSSSSSSSSSSSSSSSSS.........',
  '..........llllnnnnnnnnnnnnnnnnndmmmmmmmmmmmmmmmmmdSSSSSSSSSSSSSSSSSSSSS.........',
  '.........llllnnnnnnnnnnnnnnnnndmmmmmmmmmmmmmmmmmmmdSSSSSSSSSSSSSSSSSSSSS........',
  '.........llllnnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSSS........',
  '........llllnnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSSS.......',
  '........llllnnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSSS.......',
  '.......llllnnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSSS......',
  '.......llllnnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSSS......',
  '.......llllnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSS......',
  '......llllnnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSSS.....',
  '......llllnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSS.....',
  '......llllnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSS.....',
  '.....llllnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSS....',
  '.....llllnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSS....',
  '....llllnnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSSS...',
  '....llllnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSS...',
  '....llllnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSS...',
  '...llllnnnnnnnnnnnnnnndmmkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkmmdSSSSSSSSSSSSSSSSSSS..',
  '......................dmmkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkmmd.....................',
  '..........ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.........',
  '..........ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.........',
  '..........ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.........',
];

export const CAVE_PALETTE: PixelPalette = {
  l: 0xbab3a6, n: 0x9a938a, S: 0x7c746a, d: 0x57504a,
  m: 0x38322e, k: 0x232020, v: 0x7da565, V: 0x98c07e,
};

export const SNOWCAVE_PALETTE: PixelPalette = {
  l: 0xffffff, n: 0xeaf6ff, S: 0xc6e2f5, d: 0x9cc4e4,
  m: 0x4a7ca8, k: 0x2c5578, v: 0xbfe9ff, V: 0xe3f7ff,
};

export const BUNKER_ROWS: readonly string[] = [
  '...............aya..............................................................',
  '................y...............................................................',
  '................a...............................................................',
  '...............aaa..............................................................',
  '................a...............................................................',
  '................a...............................................................',
  '...............aaa..............................................................',
  '................a...............................................................',
  '................a...............................................................',
  '...............aaa..............................................................',
  '................lllnnnnnnnnnnnnnnnnnnnnnnnnSSSS.................................',
  '................lllnnnnnnnnnnnnnnnnnnnnnnnnnSSSS................................',
  '...............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS...............................',
  '...............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS..............................',
  '...............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS.............................',
  '..............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS............................',
  '..............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS...........................',
  '..............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS..........................',
  '.............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS.........................',
  '.............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS........................',
  '............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS.......................',
  '............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS......................',
  '............lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS.....................',
  '...........lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnSSSS....................',
  '...........lllnnnnnnnnMMmMMmMMmMMmMnnnnnnnnnnnnnnnnnnnnnnSSSS...................',
  '...........lllnnnnnnnnMMmMMmMMmMMmMnnnnnnnnnnnnnnnnnnnnnnnSSSS..................',
  '..........lllnnnnnnnnnMMmMMmMMmMMmMnnnnnnnnnnnnnMMMMMMMMMMMMMMMMMMM.............',
  '..........lllnnnnnnnnnMMmMMmMMmMMmMnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmM.............',
  '..........lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmM.............',
  '.........lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmM.............',
  '.........lllnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmM.............',
  '........lllnnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmMS............',
  '........lllnnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmymMMmmymmmmmMSS...........',
  '........lllnnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmMSSS..........',
  '.......lllnnnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmMSSSS.........',
  '.......lllnnnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmMnSSSS........',
  '.......lllnnnnstststststststsnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmMnnSSSS.......',
  '......lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnMmmmmmmmMMmmmmmmmmMnnnSSSS......',
  '......lllnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnMMMMMMMMMMMMMMMMMMMnnnnSSSS.....',
  '.....ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd....',
  '.....ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd....',
  '.....ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd....',
  '.....ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd....',
  '...EEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEv..',
  '...EvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvE..',
  '...vEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEEEEEEEvEE..',
];

export const BUNKER_PALETTE: PixelPalette = {
  l: 0xc6cdc3, n: 0xa8b0a6, S: 0x878f85, d: 0x626a60,
  M: 0x4a524c, m: 0x5c6660, y: 0x9be7c4, a: 0x5c6660,
  s: 0xb3a077, t: 0x96835d, E: 0x7a6a50, v: 0x6fa860,
};

export const CASTLE_ROWS: readonly string[] = [
  '....................................................................a...........',
  '....................................................................aFFFFFF.....',
  '....................................................................aFFFFF......',
  '....................................................................aFFFF.......',
  '....................................................................aFFF........',
  '....................................................................aFF.........',
  '.................ccr.........................................ccr....a...........',
  '................ccrrr.......................................ccrrr...a...........',
  '................ccrrr.......................................ccrrr...............',
  '...............ccRrrrr.....................................ccRrrrr..............',
  '..............ccRRrrrrr...................................ccRRrrrrr.............',
  '.............ccRRRrrrrrr.................................ccRRRrrrrrr............',
  '............ccRRRRrrrrrrr...............................ccRRRRrrrrrrr...........',
  '............ccRRRRrrrrrrr...............................ccRRRRrrrrrrr...........',
  '...........ccRRRRRrrrrrrrr.............................ccRRRRRrrrrrrrr..........',
  '..........ccRRRRRRrrrrrrrrr...........................ccRRRRRRrrrrrrrrr.........',
  '..........ccRRRRRRrrrrrrrrr...........................ccRRRRRRrrrrrrrrr.........',
  '.........ccRRRRRRRrrrrrrrrrr.........................ccRRRRRRRrrrrrrrrrr........',
  '........ccRRRRRRRRrrrrrrrrrrr.......................ccRRRRRRRRrrrrrrrrrrr.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSS.......................llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSS.......................llnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSS.......................llnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSMMMM..MMMM..MMMM..MMMM.llnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSMMMM..MMMM..MMMM..MMMM.llnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSMMMM..MMMM..MMMM..MMMM.llnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSMMMM..MMMM..MMMM..MMMM.llnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSSSSSSSSSSSSSSSSSSSSSSSSllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnnnnnnnnnnnnnnnnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnknnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnknnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndndndndndndndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndddddddddddddnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '........llnnnnnnnnnnnnnnnnnSSnnnnndnknknknknkndnnnnnllnnnnnnnnnnnnnnnnnSS.......',
  '......ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.....',
  '......ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.....',

];

export const CASTLE_PALETTE: PixelPalette = {
  l: 0xd6dce6, n: 0xb6bdc9, S: 0x97a0b0, d: 0x757e90,
  R: 0x5c6bc0, r: 0x4553a5, c: 0x7d8cf0,
  F: 0xef5350, a: 0x5c6660, k: 0x2c2f3a, M: 0xe8ecf2,
};

export interface HouseArt {
  rows: readonly string[];
  palette: PixelPalette;
  outline: number;
}

export const CABIN_HOUSE_ART: Record<CabinHouseId, HouseArt> = {
  villa: { rows: VILLA_ROWS, palette: VILLA_PALETTE, outline: 0x7a4232 },
  cabin: { rows: CABIN_ROWS, palette: CABIN_PALETTE, outline: 0x3f2a1c },
  cave: { rows: CAVE_ROWS, palette: CAVE_PALETTE, outline: 0x3a332e },
  snowcave: { rows: CAVE_ROWS, palette: SNOWCAVE_PALETTE, outline: 0x7d9cb8 },
  bunker: { rows: BUNKER_ROWS, palette: BUNKER_PALETTE, outline: 0x3f4740 },
  castle: { rows: CASTLE_ROWS, palette: CASTLE_PALETTE, outline: 0x3a4152 },
};

/** 房屋氛围光：矩阵内坐标（含 1px 描边偏移）+ 颜色 + 尺寸。flicker = 火光呼吸。 */
export interface HouseGlowDef {
  mx: number;
  my: number;
  tint: number;
  scale: number;
  alpha: number;
  flicker?: boolean;
}

export const HOUSE_GLOWS: Record<CabinHouseId, readonly HouseGlowDef[]> = {
  villa: [
    { mx: 26, my: 36, tint: 0xffd9a0, scale: 3.5, alpha: 0.5 },
    { mx: 54, my: 36, tint: 0xffd9a0, scale: 3.5, alpha: 0.5 },
    { mx: 26, my: 49, tint: 0xffd9a0, scale: 3.5, alpha: 0.5 },
    { mx: 54, my: 49, tint: 0xffd9a0, scale: 3.5, alpha: 0.5 },
    { mx: 40, my: 50, tint: 0xffc98a, scale: 3.2, alpha: 0.45, flicker: true },
  ],
  cabin: [
    { mx: 26, my: 35, tint: 0xffd27a, scale: 3.8, alpha: 0.6, flicker: true },
    { mx: 54, my: 35, tint: 0xffd27a, scale: 3.8, alpha: 0.6, flicker: true },
  ],
  cave: [{ mx: 40, my: 35, tint: 0xffb066, scale: 4.8, alpha: 0.55, flicker: true }],
  snowcave: [{ mx: 40, my: 35, tint: 0x9fd9ff, scale: 4.2, alpha: 0.5 }],
  bunker: [
    { mx: 57, my: 32, tint: 0x9be7c4, scale: 2.6, alpha: 0.4 },
    { mx: 16, my: 1, tint: 0x9be7c4, scale: 1.5, alpha: 0.8, flicker: true },
  ],
  castle: [
    { mx: 40, my: 52, tint: 0xffc98a, scale: 3.5, alpha: 0.5, flicker: true },
    { mx: 18, my: 28, tint: 0x7d8cf0, scale: 1.8, alpha: 0.5 },
    { mx: 62, my: 28, tint: 0x7d8cf0, scale: 1.8, alpha: 0.5 },
  ],
};

/* ------------------------------ 小人（16×24，两帧走路） ------------------------------ */
/* 面向 +x；r = 1px 轮廓光（头顶/受光侧），大写 = 暗部。帧 A 迈步、帧 B 收步（换帧动画）。 */

const PERSON_TOP: readonly string[] = [
  '.....rrrr.......',
  '....rhhhhh......',
  '...rhhhhhhh.....',
  '...hhhhhhhhh....',
  '...hffffffff....',
  '...hfffffeff....',
  '...hffffffff....',
  '....ffffffff....',
  '.....FffffF.....',
  '....cccccccc....',
  '....ccccccccC...',
  '....ccccccccC...',
  '....cffcccccC...',
  '....ccccccccC...',
  '....ccccccccC...',
  '....CCCCCCCC....',
  '....pppppppp....',
];

const PERSON_LEGS_A: readonly string[] = [
  '....pp...pp.....',
  '....pp...pp.....',
  '....pp...pp.....',
  '....pp...pp.....',
  '....Pp...pP.....',
  '...sss...sss....',
  '...sss...sss....',
];

const PERSON_LEGS_B: readonly string[] = [
  '....pp..pp......',
  '....pp..pp......',
  '....pp..pp......',
  '....PP..pp......',
  '....ss...Pp.....',
  '.........sss....',
  '.........sss....',
];

export const PERSON_PALETTE: PixelPalette = {
  r: 0xcdefff, h: 0x475569,
  f: 0xffe0bd, F: 0xe9c49c, e: 0x1f2937,
  c: 0x38bdf8, C: 0x0e7490,
  p: 0x334155, P: 0x3b4a63, s: 0x5b6b82,
};

export const PERSON_WALK_FRAMES: readonly (readonly string[])[] = [
  [...PERSON_TOP, ...PERSON_LEGS_A],
  [...PERSON_TOP, ...PERSON_LEGS_B],
];

/* ------------------------------ 宠物（20×24，两帧，调色板换色） ------------------------------ */
/* 猫/犬通用形：b 本色 / d 暗部 / l 亮部 / e 眼 / n 鼻。换 palette 即换宠物颜色。
 *
 * G1-8a：旧版仅 20×14（上 11 行 + 腿 3 行），低于评分卡硬标准 ≥16×24。
 * 本版为**逐行重绘**（非把旧矩阵按 Y 轴拉伸——拉伸必然失真）：
 *   头（含双耳/双眼/鼻）+ 躯干 + 亮色肚腹 = 21 行，腿 3 行，合计 24 行、宽 20，近方形比例。 */

const PET_TOP: readonly string[] = [
  '...dd..........dd...',
  '..dbbd........dbbd..',
  '..dbbbbbbbbbbbbbbd..',
  '.dbbbbbbbbbbbbbbbbd.',
  '.dbbbbbbbbbbbbbbbbd.',
  '.dbbbeebbbbbbeebbd..',
  '.dbbbeebbbbbbeebbd..',
  '.dbbbbbbbnnbbbbbbd..',
  '.dbbbbbbbbbbbbbbbbd.',
  '..dbbbbbbbbbbbbbbd..',
  '..dbbbbbbbbbbbbbbd..',
  '...dbbbbbbbbbbbbd...',
  '...dbbllllllbbbdd...',
  '..dbbbllllllbbbbd...',
  '..dbbbbbbbbbbbbbbd..',
  '..dbbbbbbbbbbbbbbd..',
  '..dbbbbbbbbbbbbbbd..',
  '..dbbbbbbbbbbbbbbd..',
  '..dbbbbbbbbbbbbbbd..',
  '...dbbbbbbbbbbbbd...',
  '....dbbbbbbbbbbd....',
];

const PET_LEGS_A: readonly string[] = [
  '.....dd.....dd......',
  '.....dd.....dd......',
  '....ddd....ddd......',
];

const PET_LEGS_B: readonly string[] = [
  '.....dd.....dd......',
  '.....dd......dd.....',
  '....ddd.....ddd.....',
];

export function petPalette(base: number): PixelPalette {
  return {
    b: base,
    d: shade(base, 0.72),
    l: lighten(base, 1.28),
    e: 0x1f2937,
    n: shade(base, 0.45),
  };
}

export const PET_WALK_FRAMES: readonly (readonly string[])[] = [
  [...PET_TOP, ...PET_LEGS_A],
  [...PET_TOP, ...PET_LEGS_B],
];

/* ------------------------------ 背景元素矩阵 ------------------------------ */

export const CLOUD_ROWS: readonly string[] = [
  '.............aaaaaaaaaa.....................',
  '.........aaaaaaaaaaaaaaaaaa.................',
  '.....aaaaaaaaaaaaaaaaaaaaaaaaaa.............',
  '...aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...........',
  '..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.........',
  '.abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbba........',
  'abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbc......',
  'abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcc....',
  'abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcc...',
  'abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcc..',
  '.abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcc..',
  '..abbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbccc..',
  '...accccccccccccccccccccccccccccccccccc.....',
  '.....ccccccccccccccccccccccccccccccccc......',
  '.......ccccccccccccccccccccccccccccc........',
  '..........cccccccccccccccccccccccc..........',
];

export const PINE_ROWS: readonly string[] = [
  '.............ll.............',
  '............llmm............',
  '...........lllmmm...........',
  '..........lllmmmm...........',
  '.........llmmmmmmd..........',
  '........llmmmmmmmmd.........',
  '.......llmmmmmmmmmmd........',
  '......lllmmmmmmmmmmmdd......',
  '.....llllmmmmmmmmmmmmdd.....',
  '....lllllmmmmmmmmmmmmmdd....',
  '....lllllmmmmmmmmmmmmmdd....',
  '.....dddddddddddddddddd.....',
  '..........llmmmmdd..........',
  '.........lllmmmmmmd.........',
  '........llllmmmmmmmd........',
  '.......lllllmmmmmmmmdd......',
  '......llllllmmmmmmmmmdd.....',
  '.....lllllllmmmmmmmmmmdd....',
  '....llllllllmmmmmmmmmmmdd...',
  '...lllllllllmmmmmmmmmmmmdd..',
  '..llllllllllmmmmmmmmmmmmmdd.',
  '...ddddddddddddddddddddddd..',
  '.........lllmmmmmmmdd.......',
  '........llllmmmmmmmmmd......',
  '.......lllllmmmmmmmmmmdd....',
  '......llllllmmmmmmmmmmmdd...',
  '.....lllllllmmmmmmmmmmmmdd..',
  '....llllllllmmmmmmmmmmmmmdd.',
  '...lllllllllmmmmmmmmmmmmmmdd',
  '..llllllllllmmmmmmmmmmmmmmdd',
  '.llllllllllmmmmmmmmmmmmmmmdd',
  '..dddddddddddddddddddddddddd',
  '....dddddddddddddddddddddd..',
  '...........TttttT...........',
  '...........TttttT...........',
  '...........TttttT...........',
  '...........TttttT...........',
  '...........TttttT...........',
  '...........TttttT...........',
  '...........TttttT...........',
  '...........TttttT...........',
  '..........TTttttTT..........',
  '.........TTTttttTTT.........',
  '........TTTTttttTTTT........',
];

export const TREE_ROUND_ROWS: readonly string[] = [
  '............mmmmmmmm............',
  '.........mmmmmmmmmmmmmm.........',
  '.......mmmmmmmmmmmmmmmmmm.......',
  '.....mmmmmmmmmmmmmmmmmmmmmm.....',
  '....mmmllllmmmmmmmmmmmmmmmmd....',
  '...mmmllllllmmmmmmmmmmmmmmmmd...',
  '..mmmlllllllmmmmmmmmmmmmmmmmmd..',
  '..mmllllllllmmmmmmmmmmmmmmmmmd..',
  '.mmmllllmmmmmmmmmmmmmmmmmmmmmmd.',
  '.mmllmmmmmmmmmmmmmmmmmmmmmmmmmd.',
  '.mllmmmmmmmmmmllmmmmmmmmmmmmmmd.',
  'mllmmmmmmmmmmllllmmmmmmmmmmmmmmd',
  'mllmmmmmmmmmmllllmmmmmmmmmmmmmmd',
  'mllmmmmmmmmmmmllmmmmmmmmmmmmmmdd',
  'mmllmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  '.mmmmmmmmmmmmmmmmmmmmmmmmmmmmdd.',
  '.mmmmmmmmmmmmmmmmmmmmmmmmmmmmdd.',
  '..mmmmmmmmmmmmmmmmmmmmmmmmmmdd..',
  '..dmmmmmmmmmmmmmmmmmmmmmmmmddd..',
  '...dmmmmmmmmmmmmmmmmmmmmmmddd...',
  '....ddmmmmmmmmmmmmmmmmmmdddd....',
  '.....dddmmmmmmmmmmmmmmddddd.....',
  '.......ddddmmmmmmmmdddddd.......',
  '..........dddddddddddd..........',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '............TTttttTT............',
  '...........TTTttttTTT...........',
  '..........TTTTttttTTTT..........',
];

export const BUSH_ROWS: readonly string[] = [
  '........mmmmmmmm........',
  '.....mmmmmmmmmmmmmm.....',
  '...mmmllllmmmmmmmmmmm...',
  '..mmmllllllmmmmmmmmmmmd.',
  '.mmmlllllllmmmmmmmmmmmmd',
  '.mmllmmmmmmmmmmmmmmmmmmd',
  'mmllmmmmmmmmmmmmmmmmmmmd',
  'mllmmmmmmmmmmmmmmmmmmmmd',
  'mmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmdd',
  '.dmmmmmmmmmmmmmmmmmmmdd.',
  '..ddmmmmmmmmmmmmmmmmdd..',
  '....dddmmmmmmmmmmdddd...',
  '......dddddddddddd......',
];

export const TUFT_ROWS: readonly string[] = [
  '..l.......l.....',
  '..l...l...l...l.',
  '.ll...l..ll...l.',
  '.ll..ll..ll..ll.',
  'lll..ll.lll..ll.',
  'lll.llll.ll.llll',
  'llllllllllllllll',
  'llddddddddddddll',
  '.dddddddddddddd.',
  '..dddddddddddd..',
];

export const ROCK_ROWS: readonly string[] = [
  '.......llllll.......',
  '....llllllllllll....',
  '..llllmmmmmmmmmmmd..',
  '.llllmmmmmmmmmmmmmd.',
  'llllmmmmmmmmmmmmmmdd',
  'lllmmmmmmmmmmmmmmmdd',
  'llmmmmmmmmmmmmmmmmdd',
  'lmmmmmmmmmmmmmmmmmdd',
  'dmmmmmmmmmmmmmmmmmdd',
  '.dmmmmmmmmmmmmmmmmd.',
  '..ddmmmmmmmmmmmmdd..',
  '....dddddddddddd....',
];

export const PEBBLE_ROWS: readonly string[] = [
  '..llllll..',
  '.lllllllmd',
  'llmmmmmmdd',
  'lmmmmmmmdd',
  '.dmmmmmmd.',
  '..dddddd..',
];

export const FLOWER_ROWS: readonly string[] = [
  '...PPPP...',
  '..PPPPPP..',
  '.PPPPPPPP.',
  '.PPPYYPPP.',
  '.PPPYYPPP.',
  '.PPPPPPPP.',
  '..PPPPPP..',
  '...PPPP...',
  '....tt....',
  '....tt....',
  '....tt....',
  '..lltt....',
  '.lllttll..',
  '..llttll..',
  '....tt....',
  '....tt....',
];

export const REED_ROWS: readonly string[] = [
  '..PPP...',
  '.PPPPP..',
  '.PPPP...',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
  '..ss....',
];

export const WHEAT_ROWS: readonly string[] = [
  '..YYY..',
  '.YYYYY.',
  '..YYY..',
  '..sss..',
  '..sss..',
  '..sss..',
  's.ss.s.',
  '..sss..',
  '..sss..',
  '..sss..',
  '..sss..',
  '..sss..',
];

export const FENCE_ROWS: readonly string[] = [
  '..ss................ss..',
  '..ss................ss..',
  '..ss................ss..',
  '..ssssssssssssssssssss..',
  '..ssssssssssssssssssss..',
  '..ss................ss..',
  '..ssssssssssssssssssss..',
  '..ssssssssssssssssssss..',
  '..ss................ss..',
  '..ss................ss..',
];

export const CRYSTAL_ROWS: readonly string[] = [
  '.....ll.....',
  '....cCCc....',
  '....cCCc....',
  '...cCCCCc...',
  '..cCCCCCCc..',
  '..cCCcCCCc..',
  '.cCCCcCCCCc.',
  '.cCCccCCCcc.',
  'cCCCCcCCCccc',
  'cCCCccCCCddd',
  'cCCccCCCdddd',
  '.cCcCCCdddd.',
  '.ccCCdddddd.',
  '..ccccdddd..',
  '...ccccddd..',
  '....ddddd...',
];

export const PETAL_ROWS: readonly string[] = ['PP..', '.PP.', '..PP'];

export const SNOWFLAKE_ROWS: readonly string[] = ['..w..', '.www.', 'wwwww', '.www.', '..w..'];

export const SPARKLE_ROWS: readonly string[] = ['..w..', '..w..', 'wwWww', '..w..', '..w..'];

export const STARDUST_ROWS: readonly string[] = ['.w.', 'wWw', '.w.'];

export const SEED_ROWS: readonly string[] = ['w...w', '.w.w.', '..W..', '..t..', '..t..'];

/* ------------------------------ A4 主题专属元素矩阵（13 种，逐行重绘，严禁拉伸） ------------------------------ */
export const MAGIC_MUSHROOM_ROWS: readonly string[] = [
  '.......PPPPPP.......',
  '....PPPMMMMMPPP.....',
  '...PPMMMMMMMMMPP....',
  '..PPMMMBBBBBMMPPP...',
  '.PPPMMBBBBBBBMMPPP..',
  '.PPPMBBBBBBBBBMMPP..',
  'PPPBBBBBBBBBBBBMPPP.',
  'PPPPBBBBBBBBBBBBPPPP',
  '.MMMMMMMMMMMMMMMMMM.',
  '....BBBBBBBBBBBB....',
  '........ltt.........',
  '.......llttt........',
  '.......llttt........',
  '......llltttt.......',
  '......llltttt.......',
  '.....llllttttt......',
];
export const MAGIC_PORTAL_ROWS: readonly string[] = [
  '........ssssssssssss........',
  '......ssssssssssssssss......',
  '.....ssssssssssssssssss.....',
  '....sssllllsssssssssssss....',
  '...ssslcccccsssssssssssss...',
  '..sssllcccccCsssssssssssss..',
  '..sssllccccccCssssssssssss..',
  '.ssssllccCCCCcCssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllcCCCCCCcssssssssssss.',
  '.ssssllccCCCCcCssssssssssss.',
  '..sssllccccccCssssssssssss..',
  '..sssllcccccCsssssssssssss..',
  '...ssslcccccsssssssssssss...',
  '....sssllllsssssssssssss....',
  '....SSSSSSSSSSSSSSSSSSSS....',
  '...SSSSSSSSSSSSSSSSSSSSSS...',
];
export const MAGIC_CRYSTAL_ROWS: readonly string[] = [
  '.......ll.......',
  '......llcc......',
  '.....lllccc.....',
  '....llllcccc....',
  '....lllccccc....',
  '...llllcccccc...',
  '...lllccCCCCc...',
  '..llllccCCCCd...',
  '..lllcccCCCdd...',
  '.llllcccCCCddd..',
  '.lllccccCCdddd..',
  '.llcccccCCdddd..',
  'llccccccCCddddd.',
  'llcccccCCCddddd.',
  'lccccccCCCddddd.',
  '.ccccccCCCdddd..',
  '.ccccccCCCdddd..',
  '..cccccCCCddd...',
  '..cccccCCCddd...',
  '...ccccCCCdd....',
  '...ccccCCddd....',
  '....cccCCdd.....',
  '.....ccCdd......',
  '......cdd.......',
];
export const TECH_PYLON_ROWS: readonly string[] = [
  '.......yy.......',
  '......yyyy......',
  '......yyyy......',
  '.......yy.......',
  '......ssss......',
  '.....ssssss.....',
  '.....saaysa.....',
  '....sssayass....',
  '....ssaayass....',
  '....sssayass....',
  '....sssayass....',
  '....ssaayass....',
  '....sssayass....',
  '....sssayass....',
  '....ssaayass....',
  '....sssayass....',
  '...ssssssssss...',
  '...sssaaaaass...',
  '...sssYYYYYss...',
  '...sssaaaaass...',
  '...ssssssssss...',
  '...sssayyasss...',
  '...sssayyasss...',
  '...sssayyasss...',
  '...sssayyasss...',
  '..ssssssssssss..',
  '..sssaaaaaasss..',
  '..ssssssssssss..',
  '.aaaaaaaaaaaaaa.',
  '.aaaaaaaaaaaaaa.',
  'aaaaaaaaaaaaaaaa',
  'aaaaaaaaaaaaaaaa',
];
export const GLOW_ORE_ROWS: readonly string[] = [
  '........dddd........',
  '.....dddddddddd.....',
  '...ddddmmmmmmdddd...',
  '..dddmmYYYYYYmmddd..',
  '.dddmmYYYYYYYYmmddd.',
  '.ddmmYYYYyyyyYYmmdd.',
  'ddmmYYyyyyyyyYYmmdd.',
  'dmmYYyyyyyyyyyYYmmdd',
  'dmmYyyyyyyyyyyyYmmdd',
  'dmmYyyyyyyyyyyyYmmdd',
  '.dmmYyyyyyyyyyyYmmd.',
  '.dmmmYyyyyyyyYmmmd..',
  '..dmmmYYyyyyYYmmd...',
  '...dmmmmYYYYmmmd....',
  '....ddddddddddd.....',
  '......ddddddd.......',
];
export const MECH_PLANT_ROWS: readonly string[] = [
  '..........yyyy..........',
  '........yyYYYYyy........',
  '.......yyYYYYYYyy.......',
  '........yyYYYYyy........',
  '..........yyyy..........',
  '...........mm...........',
  '.....aa....mm....aa.....',
  '...aaaaaa..mm..aaaaaa...',
  '..aayyyaaa.mm.aaayyyaa..',
  '..ayYYyaa..mm..aayYYya..',
  '..aayyyaaa.mm.aaayyyaa..',
  '...aaaaaa..mm..aaaaaa...',
  '.....aa....mm....aa.....',
  '...........mm...........',
  '...........mm...........',
  '..........mmmm..........',
  '.........mmmmmm.........',
  '........mmmmmmmm........',
  '.......mmmmmmmmmm.......',
  '......mmmmmmmmmmmm......',
];
export const WINDMILL_ROWS: readonly string[] = [
  '.......ww..............ww.......',
  '.......ww..............ww.......',
  '........ww............ww........',
  '........ww............ww........',
  '.........ww..........ww.........',
  '.........ww..........ww.........',
  '..........ww........ww..........',
  '..........ww........ww..........',
  '...........ww......ww...........',
  '...........ww......ww...........',
  '............ww....ww............',
  '............ww....ww............',
  '.............tttt...............',
  '............tttttt..............',
  '............tttttt..............',
  '.............tttt...............',
  '............ww....ww............',
  '............ww....ww............',
  '...........ww......ww...........',
  '...........ww......ww...........',
  '..........ww........ww..........',
  '..........ww........ww..........',
  '.........ww..........ww.........',
  '.........ww..........ww.........',
  '........ww............ww........',
  '........ww............ww........',
  '.......ww..............ww.......',
  '.......ww..............ww.......',
  '..........ssssssssss............',
  '.........ssssssssssss...........',
  '........ssssssssssssss..........',
  '........ssssssssssssss..........',
  '.......ssssssSSSSssssss.........',
  '.......ssssssSSSSssssss.........',
  '......sssssssSSSSsssssss........',
  '......sssssssSSSSsssssss........',
  '.....ssssssssSSSSssssssss.......',
  '....sssssssssSSSSsssssssss......',
  '...ssssssssssSSSSssssssssss.....',
  '..sssssssssssSSSSsssssssssss....',
];
export const FRUIT_TREE_ROWS: readonly string[] = [
  '............mmmmmmmm............',
  '.........mmmmmmmmmmmmmm.........',
  '.......mmmmmmmmmmmmmmmmmm.......',
  '.....mmmmmmmmmmmmmmmmmmmmmm.....',
  '....mmmllllmmmmmmmmmmmmmmmmd....',
  '...mmmllllllmmFFmmmmmmmmmmFFd...',
  '..mmmlllllllmmFFmmmmmmmmmmFFmd..',
  '..mmllllllllmmmmmmmmmmmmmmmmmd..',
  '.mmmllllmmmmmmmmFFmmmmmmmmmmmmd.',
  '.mmllmmmmmmmmmmmFFmmmmmmmmmmmmd.',
  '.mllmmFFmmmmmmllmmmmFFmmmmmmmmd.',
  'mllmmmFFmmmmmllllmmmFFmmmmmmmmmd',
  'mllmmmmmmmmmmllllmmmmmmmmmmmmmmd',
  'mllmmmmmmmmmmmllmmmmmmmmmmmmmmdd',
  'mmllmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmFFmmmmmmmmmmFFmmmmmmmmmmdd',
  'mmmmmmFFmmmmmmmmmmFFmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmdd',
  '.mmmmmmmmmmmmmmmmmmmmmmmmmmmmdd.',
  '.mmmmmmFFmmmmmmmmmmmFFmmmmmmmdd.',
  '..mmmmmFFmmmmmmmmmmmFFmmmmmmdd..',
  '..dmmmmmmmmmmmmmmmmmmmmmmmmddd..',
  '...dmmmmmmmmmmmmmmmmmmmmmmddd...',
  '....ddmmmmmmmmmmmmmmmmmmdddd....',
  '.....dddmmmmmmmmmmmmmmddddd.....',
  '.......ddddmmmmmmmmdddddd.......',
  '..........dddddddddddd..........',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '............TTttttTT............',
  '...........TTTttttTTT...........',
  '..........TTTTttttTTTT..........',
];
export const HAYSTACK_ROWS: readonly string[] = [
  '..........yyyy..........',
  '........yyyyyyyy........',
  '......yyyyyyyyyyyy......',
  '.....yyyyyyyyyyyyyy.....',
  '....yyyyYYYYYYYYyyyy....',
  '...yyyyYYYYYYYYYYyyyy...',
  '..yyyyYYYYYYYYYYYYyyyy..',
  '.yyyyYYYYYYYYYYYYYYyyyy.',
  '.yyyYYYYYYYYYYYYYYYYyyy.',
  'yyyYYYYYYYYYYYYYYYYYYyyy',
  'yyyYYYYYYYYYYYYYYYYYYsss',
  'yyyYYYYYYYYYYYYYYYYYYsss',
  '.yyYYYYYYYYYYYYYYYYYsss.',
  '..yyYYYYYYYYYYYYYYYsss..',
  '...ssssssssssssssssss...',
  '.....ssssssssssssss.....',
];
export const PEACH_TREE_ROWS: readonly string[] = [
  '............PPPPPPPP............',
  '.........PPPPPPPPPPPPPP.........',
  '.......PPPPPPPPPPPPPPPPPP.......',
  '.....PPPPPPPPPPPPPPPPPPPPPP.....',
  '....PPPllllPPPPPPPPPPPPPPPPF....',
  '...PPPllllllPPPPPPPPPPPPPPPPF...',
  '..PPPlllllllPPPPPPPPPPPPPPPPPF..',
  '..PPllllllllPPPPPPPPPPPPPPPPPF..',
  '.PPPllllPPPPPPPPPPPPPPPPPPPPPPF.',
  '.PPllPPPPPPPPPPPPPPPPPPPPPPPPPF.',
  '.PllPPPPPPPPPPllPPPPPPPPPPPPPPF.',
  'PllPPPPPPPPPPllllPPPPPPPPPPPPPPF',
  'PllPPPPPPPPPPllllPPPPPPPPPPPPPPF',
  'PllPPPPPPPPPPPllPPPPPPPPPPPPPPFF',
  'PPllPPPPPPPPPPPPPPPPPPPPPPPPPPFF',
  'PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPFF',
  'PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPFF',
  'PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPFF',
  'PPPPPPPPPPPPPPPPPPPPPPPPPPPPPPFF',
  '.PPPPPPPPPPPPPPPPPPPPPPPPPPPPFF.',
  '.PPPPPPPPPPPPPPPPPPPPPPPPPPPPFF.',
  '..PPPPPPPPPPPPPPPPPPPPPPPPPPFF..',
  '..FPPPPPPPPPPPPPPPPPPPPPPPPFFF..',
  '...FPPPPPPPPPPPPPPPPPPPPPPFFF...',
  '....FFPPPPPPPPPPPPPPPPPPFFFF....',
  '.....FFFPPPPPPPPPPPPPPFFFFF.....',
  '.......FFFFPPPPPPPPFFFFFF.......',
  '..........FFFFFFFFFFFF..........',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '.............TttttT.............',
  '............TTttttTT............',
  '...........TTTttttTTT...........',
  '..........TTTTttttTTTT..........',
];
export const BAMBOO_ROWS: readonly string[] = [
  '......ll.........ll.....',
  '....llll.........llll...',
  '...llllll.......llllll..',
  '..ldddllll.....ldddllll.',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....CCC.........CCC....',
  '.....ccc...lll...ccc....',
  '....lccc.llllll..ccc....',
  '...lddccldddllll.ccc....',
  '.....ccc.........CCC....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....CCC.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc...ll....ccc....',
  '.....ccc.llll....CCC....',
  '.....cccldddll...ccc....',
  '.....CCC.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........CCC....',
  '.....ccc.........ccc....',
  '.....CCC.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........CCC....',
  '.....ccc.........ccc....',
  '.....CCC.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........CCC....',
  '.....ccc.........ccc....',
  '.....CCC.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
  '.....ccc.........CCC....',
  '.....ccc.........ccc....',
  '.....ccc.........ccc....',
];
export const LOTUS_ROWS: readonly string[] = [
  '........PPPP........',
  '.......PPPPPP.......',
  '......PPPYYPPP......',
  '......PPPYYPPP......',
  '.......PPPPPP.......',
  '....ggggPPPPgggg....',
  '..gggggggggggggggg..',
  '.ggggggGGGGgggggggg.',
  '.gggggGGGGGGggggggg.',
  '..gggGGGGGGGGggggg..',
  '....wwGGGGGGww......',
  '......wwwwww........',
];
export const LANTERN_POST_ROWS: readonly string[] = [
  '....TTtt....',
  '...TTTTtt...',
  '....tttt....',
  '....tt......',
  '...tFFt.....',
  '..tFFFFt....',
  '..tFyyFt....',
  '..tFyyFt....',
  '..tFFFFt....',
  '...tFFt.....',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '....tt......',
  '...TTttT....',
  '..TTTTttTT..',
];


/* ------------------------------ 主题定义（5 背景） ------------------------------ */
/* 每个场景一套 16~32 色调色板：天空 3~4 段渐变 + 远/中/近景色阶 + 地面三段色带。 */

export interface ThemeElementDef {
  rows: readonly string[];
  palette: PixelPalette;
  count: number;
  /** 盖章倍率（2 = 每格 2×2 像素块）。 */
  scale: number;
  /** 元素落地点在图层内的 y 范围（图层高 50/26）。 */
  baseYMin: number;
  baseYMax: number;
}

export type FarKind = 'hills' | 'dunes' | 'ridge';

/**
 * G2-4：粒子共享池上限。两类粒子合计实例数 ≤ PARTICLE_POOL（不是各 60）。
 * 放在纯数据模块（无 pixi 依赖）以便任意测试环境直接锁定；cabinScene 复用此常量。
 */
export const PARTICLE_POOL = 60;

/** G2-4：单类环境粒子的定义。多类共享一个定长对象池（见 PARTICLE_POOL）。 */
export interface ParticleKindDef {
  kind: 'firefly' | 'petal' | 'sparkle' | 'seed' | 'stardust' | 'snow';
  count: number;
  color: number;
  centerColor?: number;
  additive?: boolean;
}

export interface ThemeArt {
  skyStops: readonly GradientStop[];
  clouds?: { palette: PixelPalette; count: number; scale: number };
  stars?: number;
  nebula?: { color: number; alpha: number; blobs: readonly { x: number; y: number; r: number }[] };
  far: {
    kind: FarKind;
    back: number;
    front: number;
    treeColor?: number;
    trees?: number;
    spires?: number;
  };
  mid: readonly ThemeElementDef[];
  ground: {
    top: number;
    base: number;
    bottom: number;
    speckles: readonly number[];
    mow?: boolean;
    water?: { from: number; to: number; color: number; deep: number; ripple: number };
  };
  near: readonly ThemeElementDef[];
  particles: {
    /** G2-4：两类粒子从【同一共享池】取，count_a + count_b ≤ PARTICLE_POOL(60)，不是各 60。 */
    kinds: readonly ParticleKindDef[];
  };
  /** 屏幕比例坐标的氛围光（加法混合大光晕）。 */
  ambientGlow?: { fx: number; fy: number; rx: number; ry: number; color: number; alpha: number };
}

const forestFlower: PixelPalette = { P: 0xf8b9cd, Y: 0xfff3c4, t: 0x4e9d58, l: 0x63b56a };
const gardenFlowerA: PixelPalette = { P: 0xf291b1, Y: 0xfff3c4, t: 0x4e9d58, l: 0x63b56a };
const gardenFlowerB: PixelPalette = { P: 0xffd35e, Y: 0xfff9e0, t: 0x4e9d58, l: 0x63b56a };
const gardenFlowerC: PixelPalette = { P: 0xc793e6, Y: 0xfff3c4, t: 0x4e9d58, l: 0x63b56a };
const gardenFlowerD: PixelPalette = { P: 0x9ecbff, Y: 0xfff9e0, t: 0x4e9d58, l: 0x63b56a };
const fieldWheat: PixelPalette = { Y: 0xf2d06b, s: 0xb99a4e };
const streamReed: PixelPalette = { P: 0xc9a26b, s: 0x5f9459 };
const streamRock: PixelPalette = { l: 0xc9cfc4, m: 0x9aa598, d: 0x74807a };
export const planetCrystal: PixelPalette = { l: 0xdaf6ff, c: 0x9be7ff, C: 0x63c7e8, d: 0x3f92b8 };
const planetRock: PixelPalette = { l: 0xb9a8e8, m: 0x8d7bd8, d: 0x6a58b0 };

/* A4 主题元素调色板（每场景主色 ≤ 5 种，低饱和马卡龙/莫兰迪，色键全覆盖） */
export const magicMushroomPalette: PixelPalette = { P: 0x9c6bb5, M: 0x71d6b8, B: 0x563e7c, t: 0xa788d4, l: 0xd8c2f8 };
export const magicPortalPalette: PixelPalette = { s: 0x5d5378, S: 0x3d3554, c: 0x68ded0, C: 0x3d4088, l: 0xbbf7f0 };
export const magicCrystalPalette: PixelPalette = { l: 0xdaf9f4, c: 0x6fe2d6, C: 0x9370db, d: 0x573b94 };
export const techPylonPalette: PixelPalette = { a: 0x2d3748, s: 0x4a5568, y: 0x38b2ac, Y: 0xdd6b20 };
export const glowOrePalette: PixelPalette = { d: 0x2b3240, m: 0x4c5566, y: 0xed8936, Y: 0xfbd38d };
export const mechPlantPalette: PixelPalette = { m: 0x4a5568, y: 0x319795, Y: 0x81e6d9, a: 0x2d3748 };
export const windmillPalette: PixelPalette = { s: 0xa07855, S: 0x765338, w: 0xf7fafc, t: 0x4a3626 };
export const fruitTreePalette: PixelPalette = { m: 0x689f38, l: 0x8bc34a, d: 0x4b7826, t: 0x775a45, T: 0x5a4232, F: 0xe53935 };
export const haystackPalette: PixelPalette = { Y: 0xddb347, y: 0xf6d66e, s: 0xaa8327 };
export const peachTreePalette: PixelPalette = { P: 0xf8bbd0, F: 0xf06292, l: 0xfce4ec, t: 0x6d4c41, T: 0x4e342e };
export const bambooPalette: PixelPalette = { c: 0x4db6ac, C: 0x26a69a, l: 0x81c784, d: 0x43a047 };
export const lotusPalette: PixelPalette = { P: 0xf48fb1, Y: 0xffeb3b, g: 0x66bb6a, G: 0x388e3c, w: 0x80deea };
export const lanternPostPalette: PixelPalette = { t: 0x6d4c41, T: 0x4e342e, F: 0xd32f2f, y: 0xffca28 };

export const THEME_ART: Record<CabinBackgroundId, ThemeArt> = {
  forest: {
    skyStops: [
      { t: 0, color: 0x84badd },
      { t: 0.45, color: 0xb1d6eb },
      { t: 0.8, color: 0xe5f3f5 },
      { t: 1, color: 0xf6f9ef },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xfcfdfe, c: 0xd5e4eb }, count: 5, scale: 2 },
    far: { kind: 'hills', back: 0xb4cfc4, front: 0x91b5a7, treeColor: 0x799d90, trees: 10 },
    mid: [
      { rows: PINE_ROWS, palette: { l: 0x8dc2a1, m: 0x5a9a72, d: 0x3f7654, t: 0x775a45, T: 0x5a4232 }, count: 4, scale: 2, baseYMin: 34, baseYMax: 48 },
      { rows: TREE_ROUND_ROWS, palette: { l: 0xaad49f, m: 0x71ae7a, d: 0x4a8a5c, t: 0x775a45, T: 0x5a4232 }, count: 3, scale: 2, baseYMin: 36, baseYMax: 49 },
      { rows: BUSH_ROWS, palette: { l: 0x9bc793, m: 0x649f6e, d: 0x457b54 }, count: 3, scale: 2, baseYMin: 40, baseYMax: 49 },
    ],
    ground: { top: 0xa1d0a2, base: 0x82b885, bottom: 0x5e9766, speckles: [0xb6deb1, 0x6aa070] },
    near: [
      { rows: TUFT_ROWS, palette: { l: 0xb4ddaa, d: 0x599161 }, count: 14, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: { l: 0xdfe3db, m: 0xb5bbaf, d: 0x8f968a }, count: 6, scale: 1, baseYMin: 14, baseYMax: 25 },
      { rows: FLOWER_ROWS, palette: forestFlower, count: 3, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'firefly', count: 36, color: 0xf7efbd, centerColor: 0xfcfaea, additive: true },
        { kind: 'seed', count: 24, color: 0xfaf3d7 },
      ],
    },
    ambientGlow: { fx: 0.22, fy: 0.14, rx: 0.38, ry: 0.32, color: 0xfcf8ea, alpha: 0.35 },
  },
  garden: {
    skyStops: [
      { t: 0, color: 0xb3ddf2 },
      { t: 0.5, color: 0xdef1f9 },
      { t: 1, color: 0xfbf5e2 },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xfefbf8, c: 0xf2e4d9 }, count: 5, scale: 2 },
    far: { kind: 'hills', back: 0xc0d7b0, front: 0x9dc090, treeColor: 0x7da474, trees: 8 },
    mid: [
      { rows: BUSH_ROWS, palette: { l: 0xaad49f, m: 0x79b37f, d: 0x569061 }, count: 4, scale: 2, baseYMin: 38, baseYMax: 48 },
      { rows: TREE_ROUND_ROWS, palette: { l: 0xb3dba6, m: 0x7eb984, d: 0x579864, t: 0x775a45, T: 0x5a4232 }, count: 2, scale: 2, baseYMin: 30, baseYMax: 44 },
      { rows: FLOWER_ROWS, palette: gardenFlowerA, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: FLOWER_ROWS, palette: gardenFlowerB, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: FLOWER_ROWS, palette: gardenFlowerC, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: FLOWER_ROWS, palette: gardenFlowerD, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
    ],
    ground: { top: 0xaed8a3, base: 0x91c48e, bottom: 0x6fa370, speckles: [0xcbe8bb, 0x7caf7d] },
    near: [
      { rows: TUFT_ROWS, palette: { l: 0xbde1b0, d: 0x5f9766 }, count: 12, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerA, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerB, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerC, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerD, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: { l: 0xe6e5dc, m: 0xbfbbad, d: 0x959183 }, count: 4, scale: 1, baseYMin: 14, baseYMax: 25 },
    ],
    particles: {
      kinds: [
        { kind: 'petal', count: 36, color: 0xf3cbd8 },
        { kind: 'sparkle', count: 24, color: 0xffffff, centerColor: 0xf8fcfe, additive: true },
      ],
    },
    ambientGlow: { fx: 0.5, fy: 0.1, rx: 0.52, ry: 0.34, color: 0xfbf2db, alpha: 0.4 },
  },
  stream: {
    skyStops: [
      { t: 0, color: 0xa4d2ec },
      { t: 0.55, color: 0xd5eef7 },
      { t: 1, color: 0xf9fdfe },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xfcfdfe, c: 0xddebf2 }, count: 4, scale: 2 },
    far: { kind: 'hills', back: 0xa7c6b4, front: 0x88af97, treeColor: 0x6d9681, trees: 9 },
    mid: [
      { rows: BUSH_ROWS, palette: { l: 0x9ccb98, m: 0x6aa572, d: 0x4a7c55 }, count: 3, scale: 2, baseYMin: 36, baseYMax: 47 },
      { rows: REED_ROWS, palette: streamReed, count: 5, scale: 2, baseYMin: 42, baseYMax: 49 },
      { rows: ROCK_ROWS, palette: streamRock, count: 3, scale: 2, baseYMin: 42, baseYMax: 49 },
    ],
    ground: {
      top: 0x9acb97,
      base: 0x7db47d,
      bottom: 0x5d9661,
      speckles: [0xb5dbad, 0x6ba06e],
      water: { from: 34, to: 66, color: 0x74b4db, deep: 0x5493bc, ripple: 0xb2daeb },
    },
    near: [
      { rows: PEBBLE_ROWS, palette: streamRock, count: 8, scale: 1, baseYMin: 12, baseYMax: 25 },
      { rows: TUFT_ROWS, palette: { l: 0xabd3a2, d: 0x599161 }, count: 8, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: REED_ROWS, palette: streamReed, count: 3, scale: 1, baseYMin: 8, baseYMax: 20 },
    ],
    particles: {
      kinds: [
        { kind: 'sparkle', count: 36, color: 0xffffff, centerColor: 0xf8fcfe, additive: true },
        { kind: 'seed', count: 24, color: 0xfaf3d7 },
      ],
    },
    ambientGlow: { fx: 0.3, fy: 0.1, rx: 0.42, ry: 0.3, color: 0xf8fcfe, alpha: 0.3 },
  },
  field: {
    skyStops: [
      { t: 0, color: 0xf3cea1 },
      { t: 0.4, color: 0xf7e2bd },
      { t: 0.75, color: 0xfaf2da },
      { t: 1, color: 0xfcf8eb },
    ],
    clouds: { palette: { a: 0xfcf6ea, b: 0xf4e3c6, c: 0xe1c2a0 }, count: 3, scale: 2 },
    far: { kind: 'dunes', back: 0xd0af81, front: 0xb79366 },
    mid: [
      { rows: WHEAT_ROWS, palette: fieldWheat, count: 6, scale: 2, baseYMin: 38, baseYMax: 49 },
      { rows: FENCE_ROWS, palette: { s: 0x9e7c66 }, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: TREE_ROUND_ROWS, palette: { l: 0xcdb074, m: 0xab8b53, d: 0x896d3f, t: 0x6b523b, T: 0x513e2c }, count: 1, scale: 2, baseYMin: 30, baseYMax: 42 },
    ],
    ground: { top: 0xdbca97, base: 0xc7b17a, bottom: 0xa9915b, speckles: [0xeadeb2, 0xb6a166], mow: true },
    near: [
      { rows: TUFT_ROWS, palette: { l: 0xe1d3a4, d: 0xa38e57 }, count: 10, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: WHEAT_ROWS, palette: fieldWheat, count: 6, scale: 1, baseYMin: 10, baseYMax: 22 },
      { rows: PEBBLE_ROWS, palette: { l: 0xe8e0cd, m: 0xc2b69c, d: 0x988d74 }, count: 5, scale: 1, baseYMin: 14, baseYMax: 25 },
    ],
    particles: {
      kinds: [
        { kind: 'seed', count: 36, color: 0xfaf3d7 },
        { kind: 'firefly', count: 24, color: 0xf7efbd, centerColor: 0xfcfaea, additive: true },
      ],
    },
    ambientGlow: { fx: 0.8, fy: 0.3, rx: 0.42, ry: 0.44, color: 0xf6e0b4, alpha: 0.5 },
  },
  planet: {
    skyStops: [
      { t: 0, color: 0x0a0e22 },
      { t: 0.35, color: 0x1f1a3d },
      { t: 0.7, color: 0x3e2d63 },
      { t: 1, color: 0x5f4587 },
    ],
    stars: 42,
    nebula: {
      color: 0x9473cb,
      alpha: 0.35,
      blobs: [
        { x: 120, y: 40, r: 60 },
        { x: 330, y: 90, r: 48 },
        { x: 240, y: 30, r: 40 },
      ],
    },
    far: { kind: 'ridge', back: 0x504375, front: 0x3b3159, spires: 8, treeColor: 0x2f2646 },
    mid: [
      { rows: CRYSTAL_ROWS, palette: planetCrystal, count: 4, scale: 2, baseYMin: 34, baseYMax: 48 },
      { rows: CRYSTAL_ROWS, palette: planetCrystal, count: 2, scale: 3, baseYMin: 30, baseYMax: 44 },
      { rows: ROCK_ROWS, palette: planetRock, count: 3, scale: 2, baseYMin: 40, baseYMax: 49 },
      // G2-17：补第 3 类中景 —— 异星紫晶灌木，避免 planet 中景元素层只有 2 个、远景密度不足
      { rows: BUSH_ROWS, palette: { l: 0xd3c6ee, m: 0xa79bd8, d: 0x7467a9 }, count: 3, scale: 2, baseYMin: 40, baseYMax: 49 },
    ],
    ground: { top: 0xa79bd8, base: 0x8e80c8, bottom: 0x685aa3, speckles: [0xcfc3ee, 0x7467a9] },
    near: [
      { rows: CRYSTAL_ROWS, palette: planetCrystal, count: 4, scale: 1, baseYMin: 10, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: planetRock, count: 6, scale: 1, baseYMin: 14, baseYMax: 25 },
      { rows: TUFT_ROWS, palette: { l: 0xc4b8e4, d: 0x7467a9 }, count: 8, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'stardust', count: 36, color: 0xe1ecfb, centerColor: 0xffffff, additive: true },
        { kind: 'sparkle', count: 24, color: 0xffffff, centerColor: 0xf8fcfe, additive: true },
      ],
    },
    ambientGlow: { fx: 0.35, fy: 0.25, rx: 0.46, ry: 0.36, color: 0x8973cb, alpha: 0.3 },
  },
  magic: {
    skyStops: [
      { t: 0, color: 0x1f1642 },
      { t: 0.4, color: 0x3d2b6b },
      { t: 0.75, color: 0x5a488f },
      { t: 1, color: 0x82b8a7 },
    ],
    clouds: { palette: { a: 0xded2f9, b: 0xc4b2eb, c: 0x937fc2 }, count: 4, scale: 2 },
    stars: 36,
    nebula: {
      color: 0x7c5ebb,
      alpha: 0.35,
      blobs: [
        { x: 140, y: 50, r: 55 },
        { x: 360, y: 70, r: 45 },
      ],
    },
    far: { kind: 'ridge', back: 0x4f3d73, front: 0x392a54, spires: 10, treeColor: 0x2b1e42 },
    mid: [
      { rows: MAGIC_PORTAL_ROWS, palette: magicPortalPalette, count: 1, scale: 2, baseYMin: 36, baseYMax: 46 },
      { rows: MAGIC_MUSHROOM_ROWS, palette: magicMushroomPalette, count: 4, scale: 2, baseYMin: 38, baseYMax: 48 },
      { rows: MAGIC_CRYSTAL_ROWS, palette: magicCrystalPalette, count: 3, scale: 2, baseYMin: 36, baseYMax: 48 },
    ],
    ground: {
      top: 0x6e8e82,
      base: 0x4d6c62,
      bottom: 0x334b44,
      speckles: [0x8fbaab, 0x3a574e],
    },
    near: [
      { rows: MAGIC_MUSHROOM_ROWS, palette: magicMushroomPalette, count: 4, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: MAGIC_CRYSTAL_ROWS, palette: magicCrystalPalette, count: 4, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: TUFT_ROWS, palette: { l: 0x8ce3cf, d: 0x3a6e61 }, count: 10, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'stardust', count: 36, color: 0x8de6d5, centerColor: 0xd4fdf5, additive: true },
        { kind: 'firefly', count: 24, color: 0xc49dff, centerColor: 0xf5ebff, additive: true },
      ],
    },
    ambientGlow: { fx: 0.3, fy: 0.2, rx: 0.46, ry: 0.36, color: 0x72d6c9, alpha: 0.35 },
  },
  scifi: {
    skyStops: [
      { t: 0, color: 0x091526 },
      { t: 0.45, color: 0x132a45 },
      { t: 0.8, color: 0x244d6b },
      { t: 1, color: 0x5d463b },
    ],
    stars: 40,
    far: { kind: 'ridge', back: 0x2c435a, front: 0x1d3042, spires: 12, treeColor: 0x142230 },
    mid: [
      { rows: TECH_PYLON_ROWS, palette: techPylonPalette, count: 3, scale: 2, baseYMin: 36, baseYMax: 48 },
      { rows: MECH_PLANT_ROWS, palette: mechPlantPalette, count: 4, scale: 2, baseYMin: 36, baseYMax: 48 },
      { rows: GLOW_ORE_ROWS, palette: glowOrePalette, count: 3, scale: 2, baseYMin: 38, baseYMax: 48 },
    ],
    ground: {
      top: 0x4a5568,
      base: 0x394252,
      bottom: 0x262c37,
      speckles: [0x6b778c, 0x2d3440],
    },
    near: [
      { rows: GLOW_ORE_ROWS, palette: glowOrePalette, count: 4, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: MECH_PLANT_ROWS, palette: mechPlantPalette, count: 3, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: { l: 0x718096, m: 0x4a5568, d: 0x2d3748 }, count: 6, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'sparkle', count: 36, color: 0x4fe3c1, centerColor: 0xaef7e6, additive: true },
        { kind: 'stardust', count: 24, color: 0xffa052, centerColor: 0xffecd6, additive: true },
      ],
    },
    ambientGlow: { fx: 0.7, fy: 0.2, rx: 0.45, ry: 0.38, color: 0x4fc3f7, alpha: 0.35 },
  },
  country: {
    skyStops: [
      { t: 0, color: 0xb5d8ec },
      { t: 0.45, color: 0xdff0f2 },
      { t: 0.8, color: 0xfdf2ce },
      { t: 1, color: 0xfef7dc },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xfefbf2, c: 0xe6dec7 }, count: 5, scale: 2 },
    far: { kind: 'dunes', back: 0xafc997, front: 0x8faf73 },
    mid: [
      { rows: WINDMILL_ROWS, palette: windmillPalette, count: 1, scale: 2, baseYMin: 36, baseYMax: 46 },
      { rows: FRUIT_TREE_ROWS, palette: fruitTreePalette, count: 3, scale: 2, baseYMin: 34, baseYMax: 46 },
      { rows: HAYSTACK_ROWS, palette: haystackPalette, count: 4, scale: 2, baseYMin: 38, baseYMax: 48 },
    ],
    ground: {
      top: 0xa6c97b,
      base: 0x8aae5d,
      bottom: 0x698841,
      speckles: [0xc1e099, 0x76994c],
      mow: true,
    },
    near: [
      { rows: HAYSTACK_ROWS, palette: haystackPalette, count: 3, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: WHEAT_ROWS, palette: fieldWheat, count: 8, scale: 1, baseYMin: 10, baseYMax: 22 },
      { rows: TUFT_ROWS, palette: { l: 0xc1e099, d: 0x6e9145 }, count: 10, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'seed', count: 36, color: 0xf8e8b2 },
        { kind: 'firefly', count: 24, color: 0xf8f1a8, centerColor: 0xfffae0, additive: true },
      ],
    },
    ambientGlow: { fx: 0.5, fy: 0.12, rx: 0.5, ry: 0.36, color: 0xfff6cf, alpha: 0.4 },
  },
  ink: {
    skyStops: [
      { t: 0, color: 0xa6d1df },
      { t: 0.4, color: 0xcbe4ec },
      { t: 0.75, color: 0xf8e3e8 },
      { t: 1, color: 0xfbf4ee },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xfdf4f5, c: 0xe3d3d8 }, count: 4, scale: 2 },
    far: { kind: 'hills', back: 0xa8c5be, front: 0x87aba2, treeColor: 0x6d9188, trees: 10 },
    mid: [
      { rows: PEACH_TREE_ROWS, palette: peachTreePalette, count: 3, scale: 2, baseYMin: 34, baseYMax: 46 },
      { rows: BAMBOO_ROWS, palette: bambooPalette, count: 4, scale: 2, baseYMin: 36, baseYMax: 48 },
      { rows: LANTERN_POST_ROWS, palette: lanternPostPalette, count: 3, scale: 2, baseYMin: 38, baseYMax: 48 },
    ],
    ground: {
      top: 0x9bc2a4,
      base: 0x7fa689,
      bottom: 0x5e8367,
      speckles: [0xb8dabf, 0x6c9274],
      water: { from: 38, to: 62, color: 0x88c2c4, deep: 0x68a1a4, ripple: 0xcde8e7 },
    },
    near: [
      { rows: LOTUS_ROWS, palette: lotusPalette, count: 4, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: BAMBOO_ROWS, palette: bambooPalette, count: 3, scale: 1, baseYMin: 10, baseYMax: 22 },
      { rows: TUFT_ROWS, palette: { l: 0xa9d6b2, d: 0x588f63 }, count: 10, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'petal', count: 36, color: 0xf7c4cf },
        { kind: 'firefly', count: 24, color: 0xfff2b8, centerColor: 0xfffbe8, additive: true },
      ],
    },
    ambientGlow: { fx: 0.35, fy: 0.15, rx: 0.46, ry: 0.34, color: 0xfce9ea, alpha: 0.35 },
  },
};
