/**
 * 数码小屋（P2）2.5D 纵深向内探索与蜿蜒小路/山道地貌系统。
 *
 * 彻底解决“天空和地面就用一条生硬直线划分开、无法向纵深延伸”的视觉痛点。
 *
 * 核心特性：
 * 1. 5 大主题向深处延伸的特色小道（Winding Depth Trails）：
 *    - 老林子（forest）：蜿蜒深入密林的青苔泥土小路与晨雾深径；
 *    - 后花园（garden）：通往后方紫藤与喷泉的白玉鹅卵石园径；
 *    - 黄金田野（golden_field）：穿行于金黄麦浪与远方风车之间的田埂小道；
 *    - 溪水边（stream）：延伸入水景深处的木质栈桥与河畔汀步石；
 *    - 观星台（observatory）：蜿蜒盘旋向深空星海的星纹汉白玉台阶；
 * 2. 2.5D 透视宽度递减（Near-Far Tapering）：小道在屏幕底部（近景）宽大（~68px），深入远方（地平线）逐渐收敛（~18px）；
 * 3. 有机起伏山脊轮廓（Rolling Horizon Crest）：消除生硬的单一直线切割，地平线拥有自然山丘起伏，小道穿过垭口深入远山；
 * 4. 纯数据 + Pixi 渲染双接口，保障 Vitest 单测 100% 可测性与渲染高性能。
 */

import { Container, Graphics } from 'pixi.js';
import type { CabinBackgroundId } from './cabinConfig';
import { normalizeThemeId, type CanonicalThemeId } from './cabinThemedWorlds';

export interface TrailPoint {
  worldX: number;
  depth: number; // 0=地平线远端, 1=屏幕最下方近景
}

export interface TrailDecoration {
  normT: number; // 0..1 沿小径比例
  lateral: number; // 相对小径中线的侧向偏移（虚拟像素）
  kind: 'pebble' | 'moss' | 'petal' | 'plank' | 'rune' | 'water_ripple' | 'mist';
  size: number;
  color: number;
}

export interface WindingPathThemeDef {
  theme: CanonicalThemeId;
  name: string;
  summary: string;
  /** 控制点序列（从 depth=1.0 近景到 depth=0.0 远景） */
  controlPoints: TrailPoint[];
  nearWidth: number;
  farWidth: number;
  colors: {
    groundFill: number;
    pathSurface: number;
    pathBorder: number;
    accent: number;
    accent2: number;
    mistGlow?: number;
  };
  decorations: TrailDecoration[];
}

export const THEME_WINDING_PATHS: Record<CanonicalThemeId, WindingPathThemeDef> = {
  forest: {
    theme: 'forest',
    name: '青苔泥土小路与晨雾深径',
    summary: '蜿蜒穿行于古树巨松之间的青苔泥土小径，晨雾在林道间幽幽弥漫',
    controlPoints: [
      { worldX: 2160, depth: 1.0 },
      { worldX: 2210, depth: 0.72 },
      { worldX: 2170, depth: 0.42 },
      { worldX: 2260, depth: 0.18 },
      { worldX: 2290, depth: 0.0 },
    ],
    nearWidth: 70,
    farWidth: 20,
    colors: {
      groundFill: 0x223820,
      pathSurface: 0x543e2b,
      pathBorder: 0x3d6635,
      accent: 0x5a8a4b, // 翠绿苔藓
      accent2: 0x788694, // 泥中石子
      mistGlow: 0xdcfce7,
    },
    decorations: [
      { normT: 0.15, lateral: -24, kind: 'moss', size: 6, color: 0x4d7c0f },
      { normT: 0.28, lateral: 20, kind: 'pebble', size: 5, color: 0x94a3b8 },
      { normT: 0.45, lateral: -14, kind: 'moss', size: 8, color: 0x65a30d },
      { normT: 0.62, lateral: 12, kind: 'pebble', size: 4, color: 0x64748b },
      { normT: 0.78, lateral: -8, kind: 'mist', size: 16, color: 0xf0fdf4 },
      { normT: 0.92, lateral: 6, kind: 'moss', size: 5, color: 0x3f6212 },
    ],
  },

  garden: {
    theme: 'garden',
    name: '白玉鹅卵石园径',
    summary: '通往后方紫藤花廊与白石喷泉的鹅卵石园径，两旁落满粉白花瓣',
    controlPoints: [
      { worldX: 2140, depth: 1.0 },
      { worldX: 2190, depth: 0.75 },
      { worldX: 2165, depth: 0.45 },
      { worldX: 2235, depth: 0.2 },
      { worldX: 2270, depth: 0.0 },
    ],
    nearWidth: 68,
    farWidth: 22,
    colors: {
      groundFill: 0x2c4a24,
      pathSurface: 0xf1f5f9, // 白玉鹅卵石
      pathBorder: 0xcbd5e1,
      accent: 0xc084fc, // 紫藤花瓣
      accent2: 0xf472b6, // 玫瑰花瓣
      mistGlow: 0xfdf2f8,
    },
    decorations: [
      { normT: 0.12, lateral: 22, kind: 'petal', size: 4, color: 0xf472b6 },
      { normT: 0.3, lateral: -20, kind: 'pebble', size: 6, color: 0xe2e8f0 },
      { normT: 0.5, lateral: 15, kind: 'petal', size: 5, color: 0xc084fc },
      { normT: 0.7, lateral: -12, kind: 'pebble', size: 5, color: 0xf8fafc },
      { normT: 0.85, lateral: 8, kind: 'petal', size: 4, color: 0xfb7185 },
    ],
  },

  golden_field: {
    theme: 'golden_field',
    name: '麦浪风车田埂小道',
    summary: '穿行于金黄麦浪与远方旋转大风车之间的田埂小道，车辙平整温润',
    controlPoints: [
      { worldX: 2130, depth: 1.0 },
      { worldX: 2210, depth: 0.7 },
      { worldX: 2160, depth: 0.4 },
      { worldX: 2250, depth: 0.16 },
      { worldX: 2280, depth: 0.0 },
    ],
    nearWidth: 66,
    farWidth: 18,
    colors: {
      groundFill: 0x78350f,
      pathSurface: 0x92400e, // 暖褐色田埂
      pathBorder: 0xb45309,
      accent: 0xf59e0b, // 金黄麦穗
      accent2: 0xfde68a, // 碎麦草
      mistGlow: 0xfef3c7,
    },
    decorations: [
      { normT: 0.18, lateral: -26, kind: 'moss', size: 6, color: 0xd97706 },
      { normT: 0.35, lateral: 22, kind: 'pebble', size: 5, color: 0xb45309 },
      { normT: 0.55, lateral: -16, kind: 'moss', size: 8, color: 0xf59e0b },
      { normT: 0.75, lateral: 14, kind: 'pebble', size: 4, color: 0x78350f },
      { normT: 0.9, lateral: -8, kind: 'moss', size: 5, color: 0xfbbf24 },
    ],
  },

  stream: {
    theme: 'stream',
    name: '木质栈桥与河畔汀步石',
    summary: '延伸入溪流深处的木质栈桥与错落的河畔汀步石，水波微澜',
    controlPoints: [
      { worldX: 2150, depth: 1.0 },
      { worldX: 2200, depth: 0.74 },
      { worldX: 2180, depth: 0.44 },
      { worldX: 2240, depth: 0.2 },
      { worldX: 2265, depth: 0.0 },
    ],
    nearWidth: 64,
    farWidth: 20,
    colors: {
      groundFill: 0x1e3a5f,
      pathSurface: 0x78350f, // 木质栈桥原木
      pathBorder: 0x451a03,
      accent: 0x64748b, // 汀步石
      accent2: 0x38bdf8, // 水花波光
      mistGlow: 0xe0f2fe,
    },
    decorations: [
      { normT: 0.15, lateral: 20, kind: 'pebble', size: 7, color: 0x64748b },
      { normT: 0.32, lateral: -18, kind: 'water_ripple', size: 10, color: 0x38bdf8 },
      { normT: 0.52, lateral: 14, kind: 'plank', size: 8, color: 0x92400e },
      { normT: 0.72, lateral: -12, kind: 'pebble', size: 6, color: 0x94a3b8 },
      { normT: 0.88, lateral: 10, kind: 'water_ripple', size: 8, color: 0x7dd3fc },
    ],
  },

  observatory: {
    theme: 'observatory',
    name: '星纹汉白玉台阶',
    summary: '蜿蜒盘旋向深空星海的星纹汉白玉台阶，阶梯镶嵌星辉符文',
    controlPoints: [
      { worldX: 2160, depth: 1.0 },
      { worldX: 2220, depth: 0.76 },
      { worldX: 2175, depth: 0.46 },
      { worldX: 2255, depth: 0.22 },
      { worldX: 2295, depth: 0.0 },
    ],
    nearWidth: 68,
    farWidth: 24,
    colors: {
      groundFill: 0x0f172a,
      pathSurface: 0xe0e7ff, // 汉白玉星阶
      pathBorder: 0x312e81,
      accent: 0x38bdf8, // 星辉符文蓝
      accent2: 0xfacc15, // 星芒金光
      mistGlow: 0xede9fe,
    },
    decorations: [
      { normT: 0.16, lateral: -20, kind: 'rune', size: 6, color: 0x38bdf8 },
      { normT: 0.36, lateral: 18, kind: 'pebble', size: 5, color: 0xc7d2fe },
      { normT: 0.56, lateral: -14, kind: 'rune', size: 7, color: 0xfacc15 },
      { normT: 0.76, lateral: 12, kind: 'pebble', size: 5, color: 0xa5b4fc },
      { normT: 0.92, lateral: -8, kind: 'rune', size: 6, color: 0x818cf8 },
    ],
  },
};

/** 获取指定背景对应的小路地貌配置（自动归一化 5 大核心主题） */
export function getWindingPathDef(bg: CabinBackgroundId | CanonicalThemeId | string): WindingPathThemeDef {
  const canonical = normalizeThemeId(bg);
  return THEME_WINDING_PATHS[canonical] ?? THEME_WINDING_PATHS.forest;
}

/**
 * 沿小道样条曲线计算给定 depth（0..1）下的世界 X 坐标与透视宽度。
 */
export function evaluateTrailAtDepth(
  pathDef: WindingPathThemeDef,
  depth: number,
): { worldX: number; width: number } {
  const clampedDepth = Math.min(1, Math.max(0, depth));
  const pts = pathDef.controlPoints;

  // 沿控制点折线插值
  // controlPoints 顺序：depth 1.0 -> 0.0
  let worldX = pts[0].worldX;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i];
    const p1 = pts[i + 1];
    // p0.depth >= clampedDepth >= p1.depth
    if (clampedDepth <= p0.depth && clampedDepth >= p1.depth) {
      const span = p0.depth - p1.depth;
      const t = span > 0.0001 ? (p0.depth - clampedDepth) / span : 0;
      // 3 次平滑插值 (smoothstep)
      const smoothT = t * t * (3 - 2 * t);
      worldX = p0.worldX + (p1.worldX - p0.worldX) * smoothT;
      break;
    }
  }

  // 近大远小透视宽度
  const width = pathDef.farWidth + (pathDef.nearWidth - pathDef.farWidth) * clampedDepth;
  return { worldX, width };
}

/**
 * 沿地平线计算起伏山脊的高度偏移（消除硬直线地平线）。
 * 小道交界处（约为 pathDef 的远景端）形成自然平缓的垭口通道。
 */
export function evaluateHorizonRidgeOffset(
  worldX: number,
  pathDef: WindingPathThemeDef,
): number {
  const passX = pathDef.controlPoints[pathDef.controlPoints.length - 1].worldX;
  const distToPass = Math.abs(worldX - passX);

  // 基础波浪起伏 (±10px 虚拟像素)
  const baseWave =
    Math.sin(worldX * 0.006) * 7 +
    Math.cos(worldX * 0.015) * 4 +
    Math.sin(worldX * 0.035) * 2;

  // 垭口区域衰减（使小道顺畅通向远方，不被高山阻断）
  const passDampen = Math.min(1, distToPass / 64);
  return baseWave * passDampen;
}

/**
 * 将向深处延伸的蜿蜒小径与地貌装饰绘制进 Pixi Container。
 * 坐标系：内部以【虚拟世界坐标】绘制（x∈[0, WORLD.width], y∈[0, GROUND_VH]），
 * 外部 Container 通过 scale.set(worldScale) 和 position 自动与相机平滑同步。
 */
export function buildWindingPathGraphics(
  container: Container,
  pathDef: WindingPathThemeDef,
  groundVh: number,
  worldWidth: number,
): void {
  container.removeChildren();

  const g = new Graphics();
  container.addChild(g);

  const STEPS = 36;
  const ribbonLeft: Array<{ x: number; y: number }> = [];
  const ribbonRight: Array<{ x: number; y: number }> = [];

  for (let i = 0; i <= STEPS; i++) {
    const depth = i / STEPS; // 0 = 远端地平线, 1 = 近景底部
    const { worldX, width } = evaluateTrailAtDepth(pathDef, depth);
    const y = depth * groundVh;
    const half = width / 2;
    ribbonLeft.push({ x: worldX - half, y });
    ribbonRight.push({ x: worldX + half, y });
  }

  // 1. 绘制小径边缘柔和青苔/泥土/石质镶边（稍宽 4px）
  g.beginPath();
  g.moveTo(ribbonLeft[0].x - 3, ribbonLeft[0].y);
  for (let i = 1; i <= STEPS; i++) {
    g.lineTo(ribbonLeft[i].x - 3, ribbonLeft[i].y);
  }
  for (let i = STEPS; i >= 0; i--) {
    g.lineTo(ribbonRight[i].x + 3, ribbonRight[i].y);
  }
  g.closePath();
  g.fill({ color: pathDef.colors.pathBorder, alpha: 0.75 });

  // 2. 绘制小道主体路面
  g.beginPath();
  g.moveTo(ribbonLeft[0].x, ribbonLeft[0].y);
  for (let i = 1; i <= STEPS; i++) {
    g.lineTo(ribbonLeft[i].x, ribbonLeft[i].y);
  }
  for (let i = STEPS; i >= 0; i--) {
    g.lineTo(ribbonRight[i].x, ribbonRight[i].y);
  }
  g.closePath();
  g.fill({ color: pathDef.colors.pathSurface, alpha: 0.95 });

  // 3. 沿路面绘制细部质感（木板纹理/鹅卵石块/星纹台阶横线）
  const STRIP_COUNT = 24;
  for (let s = 1; s < STRIP_COUNT; s++) {
    const depth = s / STRIP_COUNT;
    const { worldX, width } = evaluateTrailAtDepth(pathDef, depth);
    const y = depth * groundVh;
    const half = width * 0.42;

    if (pathDef.theme === 'stream') {
      // 木质栈桥横向枕木
      g.rect(worldX - half, y - 1, half * 2, 2).fill({ color: pathDef.colors.pathBorder, alpha: 0.8 });
      g.rect(worldX - half + 2, y, half * 2 - 4, 1).fill({ color: pathDef.colors.accent2, alpha: 0.35 });
    } else if (pathDef.theme === 'observatory') {
      // 汉白玉星阶台阶阴影与星纹亮线
      g.rect(worldX - half, y, half * 2, 2).fill({ color: pathDef.colors.pathBorder, alpha: 0.7 });
      g.rect(worldX - half + 3, y - 1, half * 2 - 6, 1).fill({ color: pathDef.colors.accent, alpha: 0.85 });
    } else {
      // 泥土/鹅卵石斑块微质感
      const offset = (s % 3 - 1) * (half * 0.4);
      g.circle(worldX + offset, y, Math.max(1, depth * 2.5)).fill({
        color: s % 2 === 0 ? pathDef.colors.accent : pathDef.colors.accent2,
        alpha: 0.6,
      });
    }
  }

  // 4. 放置特色地貌装饰（落花、晨雾、汀步石、星辉符文）
  for (const deco of pathDef.decorations) {
    const depth = deco.normT;
    const { worldX } = evaluateTrailAtDepth(pathDef, depth);
    const x = worldX + deco.lateral;
    const y = depth * groundVh;
    const scaledSize = Math.max(2, deco.size * (0.65 + depth * 0.5));

    if (deco.kind === 'mist') {
      // 晨雾光晕椭圆
      g.ellipse(x, y, scaledSize * 2, scaledSize * 0.8).fill({
        color: deco.color,
        alpha: 0.28,
      });
    } else if (deco.kind === 'rune') {
      // 菱形星辉符文
      g.poly([
        x, y - scaledSize,
        x + scaledSize * 0.8, y,
        x, y + scaledSize,
        x - scaledSize * 0.8, y,
      ]).fill({ color: deco.color, alpha: 0.88 });
    } else if (deco.kind === 'plank') {
      // 栈桥木桩
      g.rect(x - scaledSize / 2, y - scaledSize / 2, scaledSize, scaledSize * 1.4).fill({
        color: deco.color,
        alpha: 0.9,
      });
    } else {
      // 花瓣、石子、水波
      g.circle(x, y, scaledSize).fill({ color: deco.color, alpha: 0.85 });
    }
  }

  // 5. 地平线有机山丘轮廓（打破生硬横向水平线）
  const RIDGE_SAMPLES = 80;
  const ridgeWidth = Math.min(worldWidth, 5120);
  const stepX = ridgeWidth / RIDGE_SAMPLES;
  g.beginPath();
  g.moveTo(0, 0);
  for (let i = 0; i <= RIDGE_SAMPLES; i++) {
    const rx = i * stepX;
    const ry = evaluateHorizonRidgeOffset(rx, pathDef);
    g.lineTo(rx, ry);
  }
  g.lineTo(ridgeWidth, -4);
  g.lineTo(0, -4);
  g.closePath();
  // 顶部微透阴影衔接，使草甸和天空产生自然层叠
  g.fill({ color: pathDef.colors.groundFill, alpha: 0.85 });
}
