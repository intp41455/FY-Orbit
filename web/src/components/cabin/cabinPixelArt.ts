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
  '........................................',
  '...................ll...................',
  '..................llRR..................',
  '.................llRRRr.................',
  '...............llRRRRRRRr...............',
  '..............llRRRRRRRrrr..............',
  '............llRRRRRRRRRRRrrr............',
  '...........llRRRRRRRRRRRRrrrr...........',
  '.........llRRRRRRRRRRRRRRRrrrrr.........',
  '........llRRRRRRRRRRRRRRRRrrrrrr........',
  '......llRRRRRRRRRRRRRRRRRRRrrrrrrr......',
  '.....llRRRRRRRRRRRRRRRRRRRRrrrrrrrr.....',
  '...llRRRRRRRRRRRRRRRRRRRRRRRrrrrrrrrr...',
  '..eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee..',
  '.......SSSSSSSSSSSSSSSSSSSSSSSSSS.......',
  '.......ssssssssssssssssssssssssss.......',
  '.......sssfBBBfsssssssssssfBBBfsss......',
  '.......sssfbbbfsssssssssssfbbbfsss......',
  '.......sssfffffsssssssssssfffffsss......',
  '.......sssfbbbfsssssssssssfbbbfsss......',
  '.......sssfffffsssssssssssfffffsss......',
  '.......ssssssssssssssssssssssssss.......',
  '.......sssfBBBfsssDDDDsssfBBBfsss.......',
  '.......sssfbbbfsssmDDDsssfbbbfsss.......',
  '.......sssfffffsssmDDDsssfffffsss.......',
  '.......sssfbbbfsssmDDDsssfbbbfsss.......',
  '.......sssfffffsssmDyDsssfffffsss.......',
  '.......sssssssssssmDDDsssssssssss.......',
  '.......sssssssssssmDDDsssssssssss.......',
  '.......sssssssssssmDDDsssssssssss.......',
  '.......tttttttttttttttttttttttttt.......',
  '.......ttttttttttggggggtttttttttt.......',
];

export const VILLA_PALETTE: PixelPalette = {
  l: 0xff9e6e, R: 0xe0663f, r: 0xbf4f34, e: 0x9c3d2b,
  w: 0xfdf3e0, s: 0xf2e3c4, S: 0xd9c49c,
  f: 0x8fb8d8, b: 0xbfe3ff, B: 0xeaf8ff,
  D: 0x6b422a, m: 0x8d5a3a, y: 0xffe082,
  t: 0xb8a888, g: 0xcabfa0,
};

export const CABIN_ROWS: readonly string[] = [
  '...................ll...................',
  '..................llRR..................',
  '.................llRRRr....NNNNNN.......',
  '................llRRRRRr....nnnn........',
  '..............llRRRRRRRRrr..nnnn........',
  '.............llRRRRRRRRRRrr.nnnn........',
  '............llRRRRRRRRRRRRrrnnnn........',
  '..........llRRRRRRRRRRRRRRrrrrnn........',
  '.........llRRRRRRRRRRRRRRRRrrn..........',
  '........llRRRRRRRRRRRRRRRRRRRRrr........',
  '......llRRRRRRRRRRRRRRRRRRRRRRRRrr......',
  '....rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr....',
  '........SSSSSSSSSSSSSSSSSSSSSSSS........',
  '........wssssssssssssssssssssssw........',
  '........wssssssssssssssssssssssw........',
  '........wfYYfsssssssssfyyyfssssw........',
  '........wfyyfsssssssssfyyyfssssw........',
  '........wffffgggggggggfffffggggw........',
  '........wggggSSSSSSggggggggggggw........',
  '........wggggDmDmDmggggggggggggw........',
  '........wssssDmDmDmssssssssssssw........',
  '........wggggDmDmDmggggggggggggw........',
  '........wssssDmDmDmssssssssssssw........',
  '........wggggDmDmDmggggggggggggw........',
  '........wssssDmDmDmssssssssssssw........',
  '........wSSSSDmDmDmSSSSSSSSSSSSw........',
  '........SSSSSSSSSSSSSSSSSSSSSSSS........',
  '............gggggggg....................',
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
  '...................ll...................',
  '..................llnn..................',
  '.................llnnnS.................',
  '................llnnnnnS................',
  '...............llnnnnnnSS...............',
  '..............llnnnnnnnnSS..............',
  '.............llnnnnnnnnnnSS.............',
  '............llnnnnnnnnnnnnSS............',
  '...........llnnnnnnnnnnnnnnSS...........',
  '..........llnnnnnnnnnnnnnnnnSS..........',
  '.........llnnnnnnnnSSSSSSnnnnnSS........',
  '........llnnnnnnndmmmmmmdnnnnnnSS.......',
  '........llnnnnnnndmmmmmmmmdnnnnnnSS.....',
  '.......lvVnnnnnndmmmmmmmmmmdnnnnnSS.....',
  '.......llnnnnnnnndmmmmmmmmmmdnnnnnnSS...',
  '.......llnnnnnnnndmmmmmmmmmmdnnnnnnSS...',
  '......lnnnnnnnnnndmmmmmmmmmmdnnnnnnnSS..',
  '......lnnnnnnnnnndmmmmmmmmmmdnnnnnnnVnSS',
  '......lnnnnnnnnnndkkkkkkkkkkdnnnnnnnSS..',
  '.....lvnnnnnnnnnndkkkkkkkkkkdnnnnnnnnSS.',
  '.....llnnnnnnnnnnnnnnnnnnnnnnnnnnnSS....',
  '.....llnnnnnnnnnnnnnnnnnnnnnnnnnnnSS....',
  '.....dddddddddddddddddddddddddddddd.....',
  '........................................',
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
  '........y...............................',
  '........a...............................',
  '........a...............................',
  '........a...............................',
  '........a...............................',
  '........a.....llllllllllll..............',
  '........a...lnnnnnnnnnnnnnnS............',
  '........a..lnnnnnnnnnnnnnnnnS...........',
  '........alnnnnnnnnnnnnnnnnnnS...........',
  '........lnnnnnnnnnnnnnnnnnnnnnS.........',
  '......lnnnnnnnnnnnnnnnnnnnnnnnnnnS......',
  '.....lnnnnnnnnnnnnnnnnnnnnnnnnnnnnnS....',
  '....lnnnnnMMMMnnnnnnnnnnMMMMnnnnSS......',
  '...lnnnnnnnnnnnnnnnnnnnnnnnMMMMMMnSS....',
  '..lnnnnnnnnnnnnnnnnnnnnnnnMmmmmMnSSS....',
  '..lnnssttssttsnnnnnnnnnnnnMmmmYMnSSS....',
  '..lnntssttssttsnnnnnnnnnnnMmmmmMnSSS....',
  '..SSssttssttsSSSSSSSSSSSSSMmmmmMnSSS....',
  '..dddddddddddddddddddddddddddddddddd....',
  '.EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE.',
  'EEvEEEEEEEEEvEEEEEEEEEEEEEEvEEEEEEEEvEEE',
  'EEEEvEEEEEEEEEEEEEEEvEEEEEEEEEEEEvEEEEEE',
];

export const BUNKER_PALETTE: PixelPalette = {
  l: 0xc6cdc3, n: 0xa8b0a6, S: 0x878f85, d: 0x626a60,
  M: 0x4a524c, m: 0x5c6660, y: 0x9be7c4, a: 0x5c6660,
  s: 0xb3a077, t: 0x96835d, E: 0x7a6a50, v: 0x6fa860,
};

export const CASTLE_ROWS: readonly string[] = [
  '..................................aFF..',
  '..................................a....',
  '.....RR..........................RR....',
  '....cRRr........................cRRr...',
  '...cRRRRr......................cRRRRr..',
  '..cRRRRRRr....................cRRRRRRr.',
  '.cRRRRRRRRr..................cRRRRRRRRr',
  '.cRRRRRRRRr..................cRRRRRRRRr',
  '.rrrrrrrrrr..................rrrrrrrrrr',
  '..lnnnnnnS....................lnnnnnnS.',
  '..lnnnnnnS....................lnnnnnnS.',
  '..lnnnnnnS....................lnnnnnnS.',
  '..lnnknnnS....................lnnnknnS.',
  '..lnnknnnS....................lnnnknnS.',
  '..lnnknnnS....................lnnnknnS.',
  '..lnnknnnSMMM...MMM...MMM...MMlnnnknnS.',
  '..lnnnnnnSMMM...MMM...MMM...MMlnnnnnnS.',
  '..lnnnnnnSSSSSSSSSSSSSSSSSSSSSlnnnnnnS.',
  '..lnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnS.',
  '..lnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnS.',
  '..lnnnnnSndnnnndnnnndnnnndnnnnnnnnnnnS.',
  '..lnnnnnSnnknnnnnnnnnnnnnknnnnnnnnnnnS.',
  '..lnnnnnSnnknnnnnnnnnnnnnknnnnnnnnnnnS.',
  '..lnnnnnSnnknnnnnnnnnnnnnknnnnnnnnnnnS.',
  '..lnnnnnSnnnnnnnnddddnnnnnnnnnnnnnnnnS.',
  '..lnnnnnSnnnnnnndkkkkkdnnnnnnnnnnnnnnS.',
  '..lnnnnnSnnnnnndkkkkkkkdnnnnnnnnnnnnnS.',
  '..lnnnnnSnnnnnndknknknkdnnnnnnnnnnnnnS.',
  '..lnnnnnSndnnnndknknknkdnnnnnnnnnnnnnS.',
  '..lnnnnnSnnnnnndknknknkdnnnnnnnnnnnnnS.',
  '..lnnnnnSnnnnnndknknknkdnnnnnnnnnnnnnS.',
  '..dddddddddddddddddddddddddddddddddddd.',
];

export const CASTLE_PALETTE: PixelPalette = {
  l: 0xd6dce6, n: 0xb6bdc9, S: 0x97a0b0, d: 0x757e90,
  R: 0x5c6bc0, r: 0x4553a5, c: 0x7d8cf0,
  F: 0xef5350, a: 0x5c6660, k: 0x2c2f3a,
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
    { mx: 12, my: 18, tint: 0xffd9a0, scale: 2.2, alpha: 0.5 },
    { mx: 27, my: 18, tint: 0xffd9a0, scale: 2.2, alpha: 0.5 },
    { mx: 12, my: 24, tint: 0xffd9a0, scale: 2.2, alpha: 0.5 },
    { mx: 27, my: 24, tint: 0xffd9a0, scale: 2.2, alpha: 0.5 },
    { mx: 20, my: 26, tint: 0xffc98a, scale: 2.0, alpha: 0.45, flicker: true },
  ],
  cabin: [
    { mx: 11, my: 17, tint: 0xffd27a, scale: 2.4, alpha: 0.6, flicker: true },
    { mx: 24, my: 17, tint: 0xffd27a, scale: 2.4, alpha: 0.6, flicker: true },
  ],
  cave: [{ mx: 20, my: 20, tint: 0xffb066, scale: 3.0, alpha: 0.55, flicker: true }],
  snowcave: [{ mx: 20, my: 20, tint: 0x9fd9ff, scale: 2.6, alpha: 0.5 }],
  bunker: [
    { mx: 31, my: 15, tint: 0x9be7c4, scale: 1.6, alpha: 0.4 },
    { mx: 8, my: 0, tint: 0x9be7c4, scale: 0.9, alpha: 0.8, flicker: true },
  ],
  castle: [
    { mx: 20, my: 28, tint: 0xffc98a, scale: 2.2, alpha: 0.5, flicker: true },
    { mx: 5, my: 13, tint: 0x7d8cf0, scale: 1.1, alpha: 0.5 },
    { mx: 34, my: 13, tint: 0x7d8cf0, scale: 1.1, alpha: 0.5 },
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

/* ------------------------------ 宠物（20×14，两帧，调色板换色） ------------------------------ */
/* 猫/犬通用形：b 本色 / d 暗部 / l 亮部 / i 无 / e 眼 / n 鼻。换 palette 即换宠物颜色。 */

const PET_TOP: readonly string[] = [
  '..d.................',
  '.dd..........d....d.',
  '..d..........dbbbbd.',
  '..d.........lbbbbbbb',
  '..dbbbbbbbbbbbbbbbbb',
  '...bbbbbbbbbbbbbebbb',
  '...bbbbbbbbbbbbbbbbn',
  '...bbbbbbbbbbbbbbbb.',
  '....bbbbbbbbbbbbbb..',
  '....bblllllllbbbb...',
  '.....bbbbbbbbbbb....',
];

const PET_LEGS_A: readonly string[] = [
  '....dd...dd...dd....',
  '....dd...dd...dd....',
  '...ddd...ddd..ddd...',
];

const PET_LEGS_B: readonly string[] = [
  '....dd...dd....dd...',
  '....dd...dd....dd...',
  '...ddd...ddd...ddd..',
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
  '......aaaaaa..........',
  '....aaaaaaaaaa........',
  '..aaaaaaaaaaaaaa......',
  '.aaaaaaaaaaaaaaaaa....',
  'abbbbbbbbbbbbbbbbbc...',
  'abbbbbbbbbbbbbbbbbcc..',
  '.abbbbbbbbbbbbbbbbcc..',
  '..ccccccccccccccc.....',
];

export const PINE_ROWS: readonly string[] = [
  '......ll......',
  '.....llmm.....',
  '....lmmmm.....',
  '...lmmmmmm....',
  '...lmmmmmmm...',
  '..lmmmmmmmm...',
  '..lmmmmmmmdd..',
  '.lmmmmmmmmmdd.',
  '.lmmmmmmmmmdd.',
  'lmmmmmmmmmmmdd',
  'lmmmmmmmmmmmdd',
  'lmmmmmmmmmmmdd',
  '.dmmmmmmmmmmdd',
  '..dmmmmmmmmdd.',
  '...ddddddddd..',
  '....TttttT....',
  '....TttttT....',
  '....TttttT....',
  '....TttttT....',
  '....TttttT....',
  '....TttttT....',
  '....TttttT....',
];

export const TREE_ROUND_ROWS: readonly string[] = [
  '......mmmm......',
  '....mmmmmmmm....',
  '...mllmmmmmmm...',
  '..mllmmmmmmmmd..',
  '.mllmmmmmmmmmmd.',
  '.mlmmmmmmmmmmmd.',
  'mllmmmmmmmmmmmmd',
  'mmlmmmmmmmmmmmmd',
  'mmmmmmmmmmmmmmdd',
  'mmmmmmmmmmmmmmdd',
  '.mmmmmmmmmmmmdd.',
  '.dmmmmmmmmmmmdd.',
  '..dmmmmmmmmmdd..',
  '...ddmmmmmmddd..',
  '.....ddmmmdd....',
  '......TttT......',
  '......TttT......',
  '......TttT......',
];

export const BUSH_ROWS: readonly string[] = [
  '...mmmmmm...',
  '.mmllmmmmmm.',
  'mllmmmmmmmmd',
  'mlmmmmmmmmmd',
  'mmmmmmmmmmdd',
  '.dmmmmmmmmdd',
  '..dddddddddd',
];

export const TUFT_ROWS: readonly string[] = [
  '.l....l.',
  '.l..l.l.',
  'l.l.l.l.',
  'l.lll.ll',
  'd.dddd.d',
];

export const ROCK_ROWS: readonly string[] = [
  '...llll...',
  '.llmmmmmd.',
  'llmmmmmmdd',
  'lmmmmmmmdd',
  'dmmmmmmmdd',
  '.dddddddd.',
];

export const PEBBLE_ROWS: readonly string[] = [
  '.lll.',
  'lmmmd',
  '.ddd.',
];

export const FLOWER_ROWS: readonly string[] = [
  '.PPP.',
  'PPYPP',
  '.PPP.',
  '..t..',
  '..t..',
  '..t..',
  'ltt..',
  '..t..',
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

export const THEME_ART: Record<CabinBackgroundId, ThemeArt> = {
  forest: {
    skyStops: [
      { t: 0, color: 0x6fb8e8 },
      { t: 0.45, color: 0x9ed4f2 },
      { t: 0.8, color: 0xd8f0f4 },
      { t: 1, color: 0xf2f7e2 },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xf0f7fb, c: 0xc9dfea }, count: 5, scale: 2 },
    far: { kind: 'hills', back: 0xa9cfc0, front: 0x86b7a4, treeColor: 0x6f9f8d, trees: 10 },
    mid: [
      { rows: PINE_ROWS, palette: { l: 0x7fc79a, m: 0x4f9e6c, d: 0x367a50, t: 0x7a563c, T: 0x5d3f2b }, count: 4, scale: 2, baseYMin: 34, baseYMax: 48 },
      { rows: TREE_ROUND_ROWS, palette: { l: 0x9fd98f, m: 0x63b46f, d: 0x3f8f55, t: 0x7a563c, T: 0x5d3f2b }, count: 3, scale: 2, baseYMin: 36, baseYMax: 49 },
      { rows: BUSH_ROWS, palette: { l: 0x8fcc84, m: 0x58a464, d: 0x3c7f4e }, count: 3, scale: 2, baseYMin: 40, baseYMax: 49 },
    ],
    ground: { top: 0x93d494, base: 0x74bd78, bottom: 0x549a5e, speckles: [0xa9e2a2, 0x5da566] },
    near: [
      { rows: TUFT_ROWS, palette: { l: 0xa8e29a, d: 0x4f9459 }, count: 14, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: { l: 0xd9dfd2, m: 0xb0b8a8, d: 0x8b9484 }, count: 6, scale: 1, baseYMin: 14, baseYMax: 25 },
      { rows: FLOWER_ROWS, palette: forestFlower, count: 3, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'firefly', count: 36, color: 0xfff3a8, centerColor: 0xfffbd9, additive: true },
        { kind: 'seed', count: 24, color: 0xfff3c4 },
      ],
    },
    ambientGlow: { fx: 0.22, fy: 0.14, rx: 0.38, ry: 0.32, color: 0xfff7d9, alpha: 0.35 },
  },
  garden: {
    skyStops: [
      { t: 0, color: 0x9edcfa },
      { t: 0.5, color: 0xcdeffc },
      { t: 1, color: 0xfff3d0 },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xfdf4ea, c: 0xf3dccb }, count: 5, scale: 2 },
    far: { kind: 'hills', back: 0xb9d9a2, front: 0x94c483, treeColor: 0x74a868, trees: 8 },
    mid: [
      { rows: BUSH_ROWS, palette: { l: 0x9fd98f, m: 0x6bb873, d: 0x4b9459 }, count: 4, scale: 2, baseYMin: 38, baseYMax: 48 },
      { rows: TREE_ROUND_ROWS, palette: { l: 0xa8e096, m: 0x6fbf77, d: 0x4c9c5c, t: 0x7a563c, T: 0x5d3f2b }, count: 2, scale: 2, baseYMin: 30, baseYMax: 44 },
      { rows: FLOWER_ROWS, palette: gardenFlowerA, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: FLOWER_ROWS, palette: gardenFlowerB, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: FLOWER_ROWS, palette: gardenFlowerC, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: FLOWER_ROWS, palette: gardenFlowerD, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
    ],
    ground: { top: 0xa2dd93, base: 0x84c97f, bottom: 0x62a864, speckles: [0xc2ecab, 0x6fb371] },
    near: [
      { rows: TUFT_ROWS, palette: { l: 0xb2e6a0, d: 0x559a5d }, count: 12, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerA, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerB, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerC, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: FLOWER_ROWS, palette: gardenFlowerD, count: 2, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: { l: 0xe3e0d2, m: 0xbdb8a4, d: 0x948f7c }, count: 4, scale: 1, baseYMin: 14, baseYMax: 25 },
    ],
    particles: {
      kinds: [
        { kind: 'petal', count: 36, color: 0xf8b9cd },
        { kind: 'sparkle', count: 24, color: 0xffffff, centerColor: 0xe8f9ff, additive: true },
      ],
    },
    ambientGlow: { fx: 0.5, fy: 0.1, rx: 0.52, ry: 0.34, color: 0xfff0c9, alpha: 0.4 },
  },
  stream: {
    skyStops: [
      { t: 0, color: 0x8fd0f5 },
      { t: 0.55, color: 0xc4ecfb },
      { t: 1, color: 0xeafaff },
    ],
    clouds: { palette: { a: 0xffffff, b: 0xeef8fd, c: 0xcfe6f2 }, count: 4, scale: 2 },
    far: { kind: 'hills', back: 0x9cc7ae, front: 0x7db191, treeColor: 0x63987d, trees: 9 },
    mid: [
      { rows: BUSH_ROWS, palette: { l: 0x8fcf8a, m: 0x5daa67, d: 0x417f4f }, count: 3, scale: 2, baseYMin: 36, baseYMax: 47 },
      { rows: REED_ROWS, palette: streamReed, count: 5, scale: 2, baseYMin: 42, baseYMax: 49 },
      { rows: ROCK_ROWS, palette: streamRock, count: 3, scale: 2, baseYMin: 42, baseYMax: 49 },
    ],
    ground: {
      top: 0x8ccf88,
      base: 0x6fb96f,
      bottom: 0x529a58,
      speckles: [0xa9df9e, 0x5fa563],
      water: { from: 34, to: 66, color: 0x5db3e8, deep: 0x3f92c9, ripple: 0x9fd9f2 },
    },
    near: [
      { rows: PEBBLE_ROWS, palette: streamRock, count: 8, scale: 1, baseYMin: 12, baseYMax: 25 },
      { rows: TUFT_ROWS, palette: { l: 0x9fd793, d: 0x4f9459 }, count: 8, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: REED_ROWS, palette: streamReed, count: 3, scale: 1, baseYMin: 8, baseYMax: 20 },
    ],
    particles: {
      kinds: [
        { kind: 'sparkle', count: 36, color: 0xffffff, centerColor: 0xe8f9ff, additive: true },
        { kind: 'seed', count: 24, color: 0xfff3c4 },
      ],
    },
    ambientGlow: { fx: 0.3, fy: 0.1, rx: 0.42, ry: 0.3, color: 0xe8f9ff, alpha: 0.3 },
  },
  field: {
    skyStops: [
      { t: 0, color: 0xffc98a },
      { t: 0.4, color: 0xffe0a8 },
      { t: 0.75, color: 0xfff0c8 },
      { t: 1, color: 0xfff6da },
    ],
    clouds: { palette: { a: 0xfff2d9, b: 0xf9dfb4, c: 0xe8bd8e }, count: 3, scale: 2 },
    far: { kind: 'dunes', back: 0xd9ad6e, front: 0xc09154 },
    mid: [
      { rows: WHEAT_ROWS, palette: fieldWheat, count: 6, scale: 2, baseYMin: 38, baseYMax: 49 },
      { rows: FENCE_ROWS, palette: { s: 0xa3765a }, count: 2, scale: 2, baseYMin: 40, baseYMax: 49 },
      { rows: TREE_ROUND_ROWS, palette: { l: 0xd8b060, m: 0xb28a45, d: 0x8f6c33, t: 0x6e4f33, T: 0x543c26 }, count: 1, scale: 2, baseYMin: 30, baseYMax: 42 },
    ],
    ground: { top: 0xe3cb85, base: 0xd0b268, bottom: 0xb1914c, speckles: [0xf0dfa0, 0xbfa355], mow: true },
    near: [
      { rows: TUFT_ROWS, palette: { l: 0xe8d492, d: 0xa98e4a }, count: 10, scale: 1, baseYMin: 12, baseYMax: 24 },
      { rows: WHEAT_ROWS, palette: fieldWheat, count: 6, scale: 1, baseYMin: 10, baseYMax: 22 },
      { rows: PEBBLE_ROWS, palette: { l: 0xe8dcc0, m: 0xc4b490, d: 0x9a8c6a }, count: 5, scale: 1, baseYMin: 14, baseYMax: 25 },
    ],
    particles: {
      kinds: [
        { kind: 'seed', count: 36, color: 0xfff3c4 },
        { kind: 'firefly', count: 24, color: 0xfff3a8, centerColor: 0xfffbd9, additive: true },
      ],
    },
    ambientGlow: { fx: 0.8, fy: 0.3, rx: 0.42, ry: 0.44, color: 0xffdf9e, alpha: 0.5 },
  },
  planet: {
    skyStops: [
      { t: 0, color: 0x070b24 },
      { t: 0.35, color: 0x1b1440 },
      { t: 0.7, color: 0x3a2468 },
      { t: 1, color: 0x5a3a8c },
    ],
    stars: 42,
    nebula: {
      color: 0x8b5fd6,
      alpha: 0.35,
      blobs: [
        { x: 120, y: 40, r: 60 },
        { x: 330, y: 90, r: 48 },
        { x: 240, y: 30, r: 40 },
      ],
    },
    far: { kind: 'ridge', back: 0x4a3a78, front: 0x372a5c, spires: 8, treeColor: 0x2c2148 },
    mid: [
      { rows: CRYSTAL_ROWS, palette: planetCrystal, count: 4, scale: 2, baseYMin: 34, baseYMax: 48 },
      { rows: ROCK_ROWS, palette: planetRock, count: 3, scale: 2, baseYMin: 40, baseYMax: 49 },
    ],
    ground: { top: 0x9b8ade, base: 0x816fd0, bottom: 0x5f4da8, speckles: [0xc4b2f2, 0x6a58b0] },
    near: [
      { rows: CRYSTAL_ROWS, palette: planetCrystal, count: 4, scale: 1, baseYMin: 10, baseYMax: 24 },
      { rows: PEBBLE_ROWS, palette: planetRock, count: 6, scale: 1, baseYMin: 14, baseYMax: 25 },
      { rows: TUFT_ROWS, palette: { l: 0xb9a8e8, d: 0x6a58b0 }, count: 8, scale: 1, baseYMin: 12, baseYMax: 24 },
    ],
    particles: {
      kinds: [
        { kind: 'stardust', count: 36, color: 0xcfe3ff, centerColor: 0xffffff, additive: true },
        { kind: 'sparkle', count: 24, color: 0xffffff, centerColor: 0xe8f9ff, additive: true },
      ],
    },
    ambientGlow: { fx: 0.35, fy: 0.25, rx: 0.46, ry: 0.36, color: 0x7d5fd6, alpha: 0.3 },
  },
};
