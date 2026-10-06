import { hexToNumber, shade, lighten } from '../cabinPixelArt';
import type { PixelPalette } from '../cabinPixels';
import { FURNITURE_IDS, getFurniture } from './furnitureCatalog';

/**
 * W1 · 家具像素美术（零外部素材，全部代码内字符矩阵）
 *
 * 复用 cabinPixels 的 `spriteFromMatrix` + 调色板机制（任务书第 2 条硬要求），
 * 风格对齐 cabinPixelArt.ts 的现代唯美像素风约定：
 *  - 每件家具 2~3 档配色（colorways），家具栏可切换；
 *  - 矩阵内手工放置 1px 高光（`l`）与暗部（大写/深色字符）；
 *  - 1px 深色自动描边由 `spriteFromMatrix` 的 `autoOutline` 统一补齐。
 *
 * 尺寸约定：矩阵 1 字符 = 1 虚拟像素；占位 = `sizeCells * 16`。
 * 渲染时按占位格数贴地居中，矩阵最后一行即与地面的接触面。
 *
 * ★ 调色板字符是**每件家具独立**定义的（不跨家具复用），避免同字符在不同家具
 *   里代表不同颜色造成的串色/串戏（11 号文档 DNA-9 主题一致性）。每档配色都列全
 *   该矩阵用到的全部字符，`buildPalette` 会在开发期断言无遗漏。
 */

/* ------------------------------ 调色板工具 ------------------------------ */

type PaletteSpec = Readonly<Record<string, string>>;

export interface Colorway {
  readonly id: string;
  readonly label: string;
  readonly palette: PixelPalette;
}

/** 木色三阶：W 暗 / w 主 / V 高光（V 避开大小写 l，与轮廓高光区分）。 */
function wood(base = '#8d6748'): PaletteSpec {
  return {
    W: `#${shade(hexToNumber(base), 0.72).toString(16).padStart(6, '0')}`,
    w: base,
    V: `#${lighten(hexToNumber(base), 1.22).toString(16).padStart(6, '0')}`,
  };
}

/** 四阶色阶写入指定字符位：[暗, 主, 亮, 高光]。 */
function ramp(base: string, chars: readonly [string, string, string, string]): PaletteSpec {
  const c = hexToNumber(base);
  return {
    [chars[0]]: `#${shade(c, 0.6).toString(16).padStart(6, '0')}`,
    [chars[1]]: `#${c.toString(16).padStart(6, '0')}`,
    [chars[2]]: `#${lighten(c, 1.2).toString(16).padStart(6, '0')}`,
    [chars[3]]: `#${lighten(c, 1.5).toString(16).padStart(6, '0')}`,
  };
}

/** 单色（平涂，如书本脊背 / 花瓣）。 */
function flat(base: string, ch: string): PaletteSpec {
  return { [ch]: base };
}

function merge(...specs: PaletteSpec[]): PaletteSpec {
  return Object.assign({}, ...specs) as PaletteSpec;
}

function buildPalette(spec: PaletteSpec, furnitureId: string): PixelPalette {
  const out: Record<string, number> = {};
  for (const [ch, hex] of Object.entries(spec)) {
    if (!/^[A-Za-z.]$/.test(ch)) {
      throw new Error(`furnitureArt: ${furnitureId} 调色板字符非法 '${ch}'（只允许单个字母）`);
    }
    out[ch] = hexToNumber(hex);
  }
  return out as PixelPalette;
}

function ways(furnitureId: string, list: readonly { id: string; label: string; spec: PaletteSpec }[]): readonly Colorway[] {
  return list.map((c) => ({
    id: c.id,
    label: c.label,
    palette: buildPalette(c.spec, furnitureId),
  }));
}

/* ------------------------------ 家具矩阵 ------------------------------ */
/* 统一约定：最后一行是家具与地面的接触面（无透明行），渲染按底边贴地。 */
/* 字符语义见每个 colorway 的 spec（逐件独立定义）。 */

export const BED_ROWS: readonly string[] = [
  '..llllllllllllll..',
  '.ldddddddddddddd..',
  'lddWWWWWWWWWWWWddl',
  'ldWwwwwwwwwwwwwWdl',
  'ldWwwwwwwwwwwwwWdl',
  'ldWWWWWWWWWWWWWWdl',
  'ldWffffffffffffWdl',
  'ldWffLLLLLLLLffWdl',
  'ldWffLLLLLLLLffWdl',
  'ldWWWWWWWWWWWWWWdl',
  '.lddddddddddddddl.',
  '..lDDDDDDDDDDDDl..',
  '...dddddddddddd...',
];

export const TABLE_ROWS: readonly string[] = [
  '..lLLLLLLLLLLLLl..',
  '.ldWWWWWWWWWWWWdl.',
  'ldWWWWWWWWWWWWWWdl',
  'ldWWWWWWWWWWWWWWdl',
  '.lddddddddddddddl.',
  '....lW......Wl....',
  '....lW......Wl....',
  '....lW......Wl....',
  '....lW......Wl....',
  '...ldW......Wdl...',
  '...ldd......ddl...',
  '....dd......dd....',
];

export const CHAIR_ROWS: readonly string[] = [
  '.lLLLLLLl.',
  '.ldWWWWdl.',
  'ldWWWWWWdl',
  'ldWWWWWWdl',
  'ldWWWWWWdl',
  'ldWWWWWWdl',
  '.lddddddl.',
  '.lW....Wl.',
  '.lW....Wl.',
  '.ld....dl.',
  '.ld....dl.',
  '..dd..dd..',
];

export const BOOKSHELF_ROWS: readonly string[] = [
  'lLLLLLLLLLLl.',
  'ldddddddddddl',
  'ldWWWWWWWWWWl',
  'ldWaaaaaaaaWl',
  'ldWadddddddWl',
  'ldWWWWWWWWWWl',
  'ldWbbbbbbbbWl',
  'ldWbddddddbWl',
  'ldWWWWWWWWWWl',
  'ldWcccccccaWl',
  'ldWcdddddcaWl',
  'ldWWWWWWWWWWl',
  '.ldddddddddl.',
];

export const FLOOR_LAMP_ROWS: readonly string[] = [
  '....llll.....',
  '...ldaaaal...',
  '..ldaaaaaal..',
  '..ldaaaaaal..',
  '..ldddddddl..',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '...ldmmmdl...',
  '..ldddddddl..',
  '.ldddddddddl.',
  'ldddddddddddl',
];

export const RUG_ROWS: readonly string[] = [
  '..lddddddddddddddddl...',
  '.ldLLLLLLLLLLLLLLLLdl..',
  'ldLWWWWWWWWWWWWWWWWWLdl',
  'ldLWWaaaaaaaaaaWWWWWLdl',
  'ldLWWaLLLLLLLLLaWWWWLdl',
  'ldLWWaLdddddddLaWWWWLdl',
  'ldLWWaLdfffdLdLaWWWLdl.',
  'ldLWWaLdfffdLdLaWWWLdl.',
  'ldLWWaLdddddddLaWWWWLdl',
  'ldLWWaLLLLLLLLLaWWWWLdl',
  'ldLWWaaaaaaaaaaWWWWLdl.',
  'ldLWWWWWWWWWWWWWWWWLdl.',
  'ldLWWWWWWWWWWWWWWWWLdl.',
  '.ldLLLLLLLLLLLLLLLLdl..',
  '..lddddddddddddddddl...',
];

export const PLANT_ROWS: readonly string[] = [
  '...lhhhh....',
  '..lhggghl...',
  '..lhgllghl..',
  '..lhglglghl.',
  'lhhglglghhl.',
  'lhggllggghl.',
  '.ldggggggdl.',
  '..ldaaaal...',
  '..ldaaaal...',
  '..ldaaaal...',
  '..ldaaaal...',
  '..ldaaaal...',
  '.ldddddddl..',
  '.lWWWWWWWl..',
  'ldddddddddl.',
];

export const CABINET_ROWS: readonly string[] = [
  'lLLLLLLLLLLl.',
  'ldWWWWWWWWWl.',
  'ldWaaaaaaWWl.',
  'ldWaddddaaWWl',
  'ldWWWWWWWWWl.',
  'ldWbbbbbbWWl.',
  'ldWbdddbbWWl.',
  'ldWWWWWWWWWl.',
  '.ldddddddddl.',
  '..ld....dl...',
  '..ld....dl...',
];

export const CAT_BED_ROWS: readonly string[] = [
  '..ldddddddl..',
  '.ldfffffffdl.',
  'ldffLLLLffdl.',
  'ldfLLLLLLfdl.',
  'ldfffffffffdl',
  'ldfLLLLLLfdl.',
  'ldffLLLLffdl.',
  'ldffffffffddl',
  '.ldddddddddl.',
  '..lddddddddl.',
];

export const FISH_TANK_ROWS: readonly string[] = [
  '.lLLLLLLLLLl.',
  'lldddddddddl.',
  'ldWWaWWWWaWdl',
  'ldWaaWWaaWWdl',
  'ldWWaWWWWaWdl',
  'ldWaaWWaaWWdl',
  'ldWWaWWWWaWdl',
  'ldWaaWWaaWWdl',
  'ldWWaWWWWaWdl',
  'ldaaaaaaaaaWl',
  'ldWWWWWWWWWWl',
  '.ldddddddddl.',
  '..ldddddddl..',
];

export const CRATE_ROWS: readonly string[] = [
  'lLLLLLLLLl',
  'ldWWWWWWWl',
  'ldWWWWWWWl',
  'ldWddddWWl',
  'ldWWWWWWWl',
  'ldWWWWWWWl',
  'ldWWWWWWWl',
  'ldWddddWWl',
  'ldWWWWWWWl',
  '.lddddddl.',
  '..lddddl..',
];

export const PICTURE_FRAME_ROWS: readonly string[] = [
  '.lLLLLLLLl..',
  'ldWWWWWWWWdl',
  'ldWllllllWdl',
  'ldWlaaaalWdl',
  'ldWlaaLalWdl',
  'ldWlaaaalWdl',
  'ldWllllllWdl',
  'ldWWWWWWWWdl',
  '.lddddddddl.',
  '..lddddldl..',
];

/**
 * 更衣镜（W11 角色工坊入口）。
 * l/L = 镜框，W = 镜面底色，a = 镜面反光，A = 高光十字星（一眼看出是「镜子」）。
 */
export const MIRROR_ROWS: readonly string[] = [
  '..lddddddl..',
  '.ldLLLLLdl..',
  'ldLlllllLdl.',
  'ldLlWWWlLdl.',
  'ldLlWaaWlLdl',
  'ldLlWaAWlLdl',
  'ldLlWaaWlLdl',
  'ldLlWWWlLdl.',
  'ldLlllllLdl.',
  'ldLLLLLLLdl.',
  '.lddddddldl.',
  '..lddddldl..',
];

export const CEILING_LAMP_ROWS: readonly string[] = [
  '..lddddddddl..',
  '.ldMMMMMMMMdl.',
  'ldMmmmmmmmmMdl',
  '.ldlllllllldl.',
  '..ldaaaaaadl..',
  '...ldaaaal....',
  '...ldaaaal....',
  '....ldddl.....',
];

export const WALL_SHELF_ROWS: readonly string[] = [
  'lLLLLLLLl.',
  'ldWWWWWWl.',
  'ldWaaabbWl',
  'ldWWWWWWl.',
  'ldWccddWl.',
  'ldWWWWWWl.',
  '.ldddddl..',
];

export const RUG_LARGE_ROWS: readonly string[] = [
  '...ldddddddddddddddddddl....',
  '..ldLLLLLLLLLLLLLLLLLLLLdl..',
  'ldLWWWWWWWWWWWWWWWWWWWWWLdl.',
  'ldLWWaaaaaaaaaaaaaaaaaWWWLdl',
  'ldLWWaLLLLLLLLLLLLLLLaWWWLdl',
  'ldLWWaLdddddddddddddLaWWWLdl',
  'ldLWWaLdffffffffffdLaWWWLdl.',
  'ldLWWaLdfLLLLLLLLfLdLaWWWLdl',
  'ldLWWaLdffffffffffdLaWWWLdl.',
  'ldLWWaLdfLLLLLLLLfLdLaWWWLdl',
  'ldLWWaLdfggggggggfdLaWWWLdl.',
  'ldLWWaLdffffffffffdLaWWWLdl.',
  'ldLWWaLdffffffffffdLaWWWLdl.',
  'ldLWWaLdddddddddddddLaWWWLdl',
  'ldLWWaLLLLLLLLLLLLLLLaWWWLdl',
  'ldLWWaaaaaaaaaaaaaaaaaWWWLdl',
  'ldLWWWWWWWWWWWWWWWWWWWWWLdl.',
  'ldLWWWWWWWWWWWWWWWWWWWWWLdl.',
  '..ldLLLLLLLLLLLLLLLLLLLLdl..',
  '...ldddddddddddddddddddl....',
];

export const STOVE_ROWS: readonly string[] = [
  '.......ll......ll.........',
  '......ldOmmmmmmOdl........',
  '......ldMmmmmmmMdl........',
  '......lddMMMMMMddl........',
  '....llLLLLLLLLLLLLll......',
  '...lLLLLLLLLLLLLLLLLl.....',
  '...lVVVVVVVVVVVVVVVVl.....',
  '...lwwwwwwwwwwwwwwwwl.....',
  '...lWWWWWWWWWWWWWWWWl.....',
  '...lMMMMMMMMMMMMMMMMl.....',
  '..lMOOOOmmmmmmmmOOOOMl....',
  '..lMmmDDDDDDDDDDDDmmMl....',
  '..lMmDDDDDDDDDDDDDDmMl....',
  '..lMmDDDDffffffDDDDmMl....',
  '..lMmDDDffBBBBffDDDmMl....',
  '..lMmDDffbBBBBbffdDmMl....',
  '..lMmDDfbbBBBBbbffDmMl....',
  '..lMmDDffbbffbbffdDmMl....',
  '..lMmDDDffffffffDDDmMl....',
  '..lMmDWWWddddddWWWDmMl....',
  '..lMmDwwwwddddwwwwDmMl....',
  '..lMmDMMMMMMMMMMMMDmMl....',
  '..lMMMMMMMMMMMMMMMMMMl....',
  '..lMOOOmmmmmmmmmmOOOMl....',
  '..lMmmmmmmmmmmmmmmmmMl....',
  '..lMMMMMMMMMMMMMMMMMMl....',
];

export const CRYSTAL_TREE_ROWS: readonly string[] = [
  '....lLLl....',
  '...lLLLLl...',
  '..lLccLLl...',
  '.lLccccLLl..',
  'lLLccLLLLl..',
  'lLLLLLLLLl..',
  '.ldccccccdl.',
  '.ldccccccdl.',
  '..ldcccldl..',
  '..ldcccldl..',
  '...ldccdl...',
  '...ldccdl...',
  '..lddddddl..',
  '.lddddddddl.',
  'lddddddddddl',
];

export const SNOW_LAMP_ROWS: readonly string[] = [
  '....lLLl.....',
  '...lLccLl....',
  '..lLccccLl...',
  '..ldccccdl...',
  '...ldccdl....',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '....lmmml....',
  '...ldmmmdl...',
  '..ldddddddl..',
  '.ldddddddddl.',
  'ldddddddddddl',
];

export const HERB_SHELF_ROWS: readonly string[] = [
  'lLLLLLLLLl',
  'ldWWWWWWWl',
  'ldWggaaWbl',
  'ldWWWWWWWl',
  'ldWbggaaWl',
  'ldWWWWWWWl',
  'ldWccggWdl',
  'ldWWWWWWWl',
  '.lddddddl.',
];

/** id → 矩阵。key 与 furnitureCatalog 的 id 严格一致。 */
export const FURNITURE_ROWS: Readonly<Record<string, readonly string[]>> = {
  bed: BED_ROWS,
  table: TABLE_ROWS,
  chair: CHAIR_ROWS,
  bookshelf: BOOKSHELF_ROWS,
  floor_lamp: FLOOR_LAMP_ROWS,
  rug: RUG_ROWS,
  plant: PLANT_ROWS,
  cabinet: CABINET_ROWS,
  cat_bed: CAT_BED_ROWS,
  fish_tank: FISH_TANK_ROWS,
  crate: CRATE_ROWS,
  picture_frame: PICTURE_FRAME_ROWS,
  mirror: MIRROR_ROWS,
  ceiling_lamp: CEILING_LAMP_ROWS,
  wall_shelf: WALL_SHELF_ROWS,
  rug_large: RUG_LARGE_ROWS,
  stove: STOVE_ROWS,
  crystal_tree: CRYSTAL_TREE_ROWS,
  snow_lamp: SNOW_LAMP_ROWS,
  herb_shelf: HERB_SHELF_ROWS,
};

/* ------------------------------ 配色档位（每件 2~3 档） ------------------------------ */

const OUTLINE = '#2b2440';

export const FURNITURE_COLORWAYS: Readonly<Record<string, readonly Colorway[]>> = {
  bed: ways('bed', [
    { id: 'oak', label: '橡木暖橘', spec: merge(wood(), ramp('#e0894f', ['d', 'a', 'l', 'L']), flat('#f4e3d0', 'f'), ramp('#c9a37e', ['D', 'F', 'b', 'B']), flat(OUTLINE, 'x')) },
    { id: 'mint', label: '薄荷奶绿', spec: merge(wood(), ramp('#7fc8a9', ['d', 'a', 'l', 'L']), flat('#eef7f2', 'f'), ramp('#b6d8c8', ['D', 'F', 'b', 'B']), flat(OUTLINE, 'x')) },
    { id: 'berry', label: '莓果粉', spec: merge(wood(), ramp('#e08fa8', ['d', 'a', 'l', 'L']), flat('#fbe9ef', 'f'), ramp('#d3a8b6', ['D', 'F', 'b', 'B']), flat(OUTLINE, 'x')) },
  ]),
  table: ways('table', [
    { id: 'oak', label: '原木', spec: merge(wood(), flat('#6b4a33', 'd'), flat('#d8c0a0', 'L'), flat('#f0e4d0', 'l'), flat(OUTLINE, 'x')) },
    { id: 'walnut', label: '胡桃', spec: merge(wood('#6b4a33'), flat('#4a3222', 'd'), flat('#a98a68', 'L'), flat('#cbb194', 'l'), flat(OUTLINE, 'x')) },
  ]),
  chair: ways('chair', [
    { id: 'oak', label: '原木', spec: merge(wood(), flat('#6b4a33', 'd'), flat('#d8c0a0', 'L'), flat('#f0e4d0', 'l'), flat(OUTLINE, 'x')) },
    { id: 'sage', label: '灰绿', spec: merge(wood(), ramp('#9cb894', ['d', 'a', 'l', 'L']), flat(OUTLINE, 'x')) },
  ]),
  bookshelf: ways('bookshelf', [
    {
      id: 'oak',
      label: '橡木书架',
      spec: merge(
        wood(),
        flat('#4a3222', 'd'),
        flat('#c9a37e', 'L'),
        flat('#f0e4d0', 'l'),
        flat('#c2543f', 'a'),
        flat('#4a7fb5', 'b'),
        flat('#5aa469', 'c'),
        flat('#d9a441', 'e'),
        flat(OUTLINE, 'x'),
      ),
    },
    {
      id: 'ink',
      label: '墨蓝书架',
      spec: merge(
        wood('#4a4038'),
        flat('#2e2820', 'd'),
        flat('#847365', 'L'),
        flat('#a9b3bf', 'l'),
        flat('#c2543f', 'a'),
        flat('#5aa469', 'b'),
        flat('#d9a441', 'c'),
        flat(OUTLINE, 'x'),
      ),
    },
  ]),
  floor_lamp: ways('floor_lamp', [
    { id: 'warm', label: '暖光', spec: merge(ramp('#f5d9a0', ['d', 'a', 'l', 'L']), ramp('#e0b878', ['M', 'm', 'N', 'O']), flat('#a8834a', 'D'), flat(OUTLINE, 'x')) },
    { id: 'ice', label: '冷光', spec: merge(ramp('#d6ecff', ['d', 'a', 'l', 'L']), ramp('#a8c8e8', ['M', 'm', 'N', 'O']), flat('#6f8fb0', 'D'), flat(OUTLINE, 'x')) },
  ]),
  rug: ways('rug', [
    { id: 'crimson', label: '绯红', spec: merge(flat('#c2543f', 'a'), flat('#8a3527', 'd'), flat('#e07a68', 'l'), flat('#f0b3a8', 'L'), flat('#e8b06a', 'f'), flat('#f7d9a8', 'b'), flat('#5a3a2c', 'W'), flat('#7d5442', 'V'), flat(OUTLINE, 'x')) },
    { id: 'teal', label: '青碧', spec: merge(flat('#3f8f9c', 'a'), flat('#2a646e', 'd'), flat('#68b6c0', 'l'), flat('#a0d8de', 'L'), flat('#8fd0d8', 'f'), flat('#c4ecef', 'b'), flat('#2f4a50', 'W'), flat('#4a6b72', 'V'), flat(OUTLINE, 'x')) },
  ]),
  plant: ways('plant', [
    { id: 'leaf', label: '常青', spec: merge(ramp('#5aa469', ['g', 'h', 'i', 'j']), ramp('#c2703f', ['d', 'a', 'l', 'L']), flat('#8a5a34', 'W'), flat('#8d6748', 'd'), flat(OUTLINE, 'x')) },
    { id: 'bloom', label: '开花', spec: merge(ramp('#5aa469', ['g', 'h', 'i', 'j']), ramp('#e88fa8', ['k', 'm', 'n', 'o']), ramp('#c2703f', ['d', 'a', 'l', 'L']), flat('#8a5a34', 'W'), flat('#8d6748', 'D'), flat(OUTLINE, 'x')) },
  ]),
  cabinet: ways('cabinet', [
    { id: 'oak', label: '原木', spec: merge(wood(), ramp('#c2703f', ['d', 'a', 'l', 'L']), flat('#4a7fb5', 'b'), flat(OUTLINE, 'x')) },
    { id: 'ink', label: '墨蓝', spec: merge(wood('#4a4038'), ramp('#4a7fb5', ['d', 'a', 'l', 'L']), flat('#c2543f', 'b'), flat(OUTLINE, 'x')) },
  ]),
  cat_bed: ways('cat_bed', [
    { id: 'cream', label: '奶油', spec: merge(ramp('#f0dcbe', ['d', 'f', 'l', 'L']), ramp('#c9a37e', ['D', 'F', 'b', 'B']), flat(OUTLINE, 'x')) },
    { id: 'slate', label: '石板', spec: merge(ramp('#7a8a99', ['d', 'f', 'l', 'L']), ramp('#56636e', ['D', 'F', 'b', 'B']), flat(OUTLINE, 'x')) },
  ]),
  fish_tank: ways('fish_tank', [
    { id: 'water', label: '水蓝', spec: merge(wood(), ramp('#7fc8e8', ['d', 'W', 'l', 'L']), flat('#e88fa8', 'a'), flat('#f7d9e0', 'b'), flat('#3f8f9c', 'D'), flat(OUTLINE, 'x')) },
    { id: 'reef', label: '珊瑚', spec: merge(wood(), ramp('#4fc3a1', ['d', 'W', 'l', 'L']), flat('#e88fa8', 'a'), flat('#f7d9e0', 'b'), flat('#2f7a63', 'D'), flat(OUTLINE, 'x')) },
  ]),
  crate: ways('crate', [
    { id: 'oak', label: '原木', spec: merge(wood('#a97c50'), flat('#6b4a33', 'd'), flat('#d8c0a0', 'L'), flat('#f0e4d0', 'l'), flat(OUTLINE, 'x')) },
    { id: 'aged', label: '做旧', spec: merge(wood('#6b5136'), flat('#463322', 'd'), flat('#a5834f', 'L'), flat('#c9a37e', 'l'), flat(OUTLINE, 'x')) },
  ]),
  picture_frame: ways('picture_frame', [
    { id: 'gold', label: '描金', spec: merge(ramp('#b08a3e', ['d', 'W', 'l', 'L']), ramp('#7fb0d8', ['a', 'A', 'b', 'B']), flat('#e8dcb0', 'f'), flat(OUTLINE, 'x')) },
    { id: 'silver', label: '素银', spec: merge(ramp('#7d8895', ['d', 'W', 'l', 'L']), ramp('#9ec4d8', ['a', 'A', 'b', 'B']), flat('#dce8ee', 'f'), flat(OUTLINE, 'x')) },
  ]),
  mirror: ways('mirror', [
    { id: 'silver', label: '素银镜', spec: merge(flat('#7d8895', 'd'), flat('#c3ccd6', 'l'), flat('#9aa7b4', 'L'), flat('#cfe4f2', 'W'), flat('#e8f4fb', 'a'), flat('#ffffff', 'A'), flat(OUTLINE, 'x')) },
    { id: 'rose', label: '玫瑰金镜', spec: merge(flat('#8a5f49', 'd'), flat('#e0b49c', 'l'), flat('#c08a6a', 'L'), flat('#e8c8d4', 'W'), flat('#f6e4ea', 'a'), flat('#fff6f8', 'A'), flat(OUTLINE, 'x')) },
  ]),
  ceiling_lamp: ways('ceiling_lamp', [
    { id: 'warm', label: '暖光', spec: merge(ramp('#e0b878', ['M', 'm', 'N', 'O']), ramp('#f7dca6', ['d', 'a', 'l', 'L']), flat('#a8834a', 'D'), flat(OUTLINE, 'x')) },
    { id: 'rose', label: '玫瑰', spec: merge(ramp('#e8a8b8', ['M', 'm', 'N', 'O']), ramp('#fbe0e6', ['d', 'a', 'l', 'L']), flat('#a8707f', 'D'), flat(OUTLINE, 'x')) },
  ]),
  wall_shelf: ways('wall_shelf', [
    { id: 'oak', label: '原木', spec: merge(wood(), flat('#5aa469', 'a'), flat('#c2543f', 'b'), flat('#4a7fb5', 'c'), flat('#4a3222', 'd'), flat('#c9a37e', 'L'), flat('#f0e4d0', 'l'), flat(OUTLINE, 'x')) },
    { id: 'ink', label: '墨蓝', spec: merge(wood('#4a4038'), flat('#5aa469', 'a'), flat('#c2543f', 'b'), flat('#4a7fb5', 'c'), flat('#2e2820', 'd'), flat('#847365', 'L'), flat('#a9b3bf', 'l'), flat(OUTLINE, 'x')) },
  ]),
  rug_large: ways('rug_large', [
    { id: 'crimson', label: '绯红', spec: merge(flat('#c2543f', 'a'), flat('#8a3527', 'd'), flat('#e07a68', 'l'), flat('#f0b3a8', 'L'), flat('#e8b06a', 'f'), flat('#f7d9a8', 'b'), flat('#5aa469', 'g'), flat('#3a6d44', 'G'), flat('#84c28c', 'h'), flat('#5a3a2c', 'W'), flat('#7d5442', 'V'), flat(OUTLINE, 'x')) },
    { id: 'indigo', label: '靛蓝', spec: merge(flat('#4a5b9c', 'a'), flat('#333f75', 'd'), flat('#7d8ec9', 'l'), flat('#b3c0e8', 'L'), flat('#9ab0e8', 'f'), flat('#cfdcf7', 'b'), flat('#c2543f', 'g'), flat('#8a3527', 'G'), flat('#dd8071', 'h'), flat('#2f3358', 'W'), flat('#4a507a', 'V'), flat(OUTLINE, 'x')) },
  ]),
  stove: ways('stove', [
    { id: 'iron', label: '铸铁', spec: merge(wood(), flat('#d8c0a0', 'L'), flat('#f0e4d0', 'l'), ramp('#5b6068', ['M', 'm', 'N', 'O']), ramp('#e08a4a', ['d', 'f', 'b', 'B']), flat('#33373d', 'D'), flat(OUTLINE, 'x')) },
    { id: 'copper', label: '铜锈', spec: merge(wood(), flat('#d8c0a0', 'L'), flat('#f0e4d0', 'l'), ramp('#a8703f', ['M', 'm', 'N', 'O']), ramp('#e0a04a', ['d', 'f', 'b', 'B']), flat('#6b4526', 'D'), flat(OUTLINE, 'x')) },
  ]),
  crystal_tree: ways('crystal_tree', [
    { id: 'crystal', label: '晶体', spec: merge(wood('#6a5a7a'), ramp('#8fd0f0', ['c', 'd', 'l', 'L']), flat('#5a4a6a', 'D'), flat(OUTLINE, 'x')) },
    { id: 'amethyst', label: '紫晶', spec: merge(wood('#5a4a6a'), ramp('#b07ab8', ['c', 'd', 'l', 'L']), flat('#4a3a5a', 'D'), flat(OUTLINE, 'x')) },
  ]),
  snow_lamp: ways('snow_lamp', [
    { id: 'ice', label: '冰晶', spec: merge(ramp('#a8c8e8', ['M', 'm', 'N', 'O']), ramp('#d6ecff', ['c', 'd', 'l', 'L']), flat('#6f8fb0', 'D'), flat(OUTLINE, 'x')) },
    { id: 'aurora', label: '极光', spec: merge(ramp('#8fe0c0', ['M', 'm', 'N', 'O']), ramp('#d6fbec', ['c', 'd', 'l', 'L']), flat('#5fa88c', 'D'), flat(OUTLINE, 'x')) },
  ]),
  herb_shelf: ways('herb_shelf', [
    { id: 'garden', label: '田园', spec: merge(wood(), ramp('#5aa469', ['g', 'a', 'b', 'c']), ramp('#e8d06a', ['A', 'B', 'd', 'l']), flat('#4a3222', 'D'), flat('#c9a37e', 'L'), flat('#f0e4d0', 'V'), flat(OUTLINE, 'x')) },
    { id: 'forest', label: '深林', spec: merge(wood('#5a4632'), ramp('#3a7d4a', ['g', 'a', 'b', 'c']), ramp('#c2543f', ['A', 'B', 'd', 'l']), flat('#2e2418', 'D'), flat('#a5834f', 'L'), flat('#c9a37e', 'V'), flat(OUTLINE, 'x')) },
  ]),
};

/** 默认配色档位索引。 */
export const DEFAULT_COLORWAY_INDEX = 0;

/** 取某家具的配色档位；未知 id 抛错（注册表与美术必须严格一致，不静默降级）。 */
export function getColorway(furnitureId: string, index: number): Colorway {
  const list = FURNITURE_COLORWAYS[furnitureId];
  if (!list || list.length === 0) {
    throw new Error(`furnitureArt: 未知家具 id '${furnitureId}'（注册表与美术不一致）`);
  }
  const i = Math.min(list.length - 1, Math.max(0, Math.floor(index)));
  return list[i];
}

export function getColorwayCount(furnitureId: string): number {
  return FURNITURE_COLORWAYS[furnitureId]?.length ?? 0;
}

/** 取家具矩阵；未知 id 返回 undefined（调用方负责降级）。 */
export function getFurnitureRows(furnitureId: string): readonly string[] | undefined {
  if (!getFurniture(furnitureId)) return undefined;
  return FURNITURE_ROWS[furnitureId];
}

/**
 * 开发期自检：注册表每个 id 都必须有矩阵 + ≥2 档配色 + 调色板覆盖矩阵全部字符。
 * 渲染层启动时调用一次；任何缺失都**抛错**而不是画个空白家具糊弄过去
 * （诚实原则：画不出来就要明确报错）。
 */
export function assertFurnitureArtComplete(): void {
  const problems: string[] = [];
  for (const id of FURNITURE_IDS) {
    const rows = FURNITURE_ROWS[id];
    if (!rows || rows.length === 0) {
      problems.push(`${id}: 缺矩阵`);
      continue;
    }
    const list = FURNITURE_COLORWAYS[id];
    if (!list || list.length < 2) {
      problems.push(`${id}: 配色档位 < 2`);
      continue;
    }
    for (const cw of list) {
      const chars = new Set<string>();
      for (const row of rows) {
        for (const ch of row) {
          if (ch === '.' || ch === ' ') continue;
          chars.add(ch);
        }
      }
      for (const ch of chars) {
        if (cw.palette[ch] === undefined) {
          problems.push(`${id}/${cw.id}: 调色板缺字符 '${ch}'`);
        }
      }
    }
  }
  if (problems.length > 0) {
    throw new Error(`furnitureArt 完整性自检失败 -> ${problems.join('; ')}`);
  }
}
