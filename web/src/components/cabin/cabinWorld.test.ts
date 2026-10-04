/**
 * G3 · 开放世界摄像机：世界坐标 ↔ 屏幕坐标映射、边界钳制、平滑跟随、
 * resize 重算、大坐标不溢出。
 *
 * 纯逻辑测试（cabinConfig.ts 不依赖 Pixi / DOM，jsdom 下可直接跑）。
 * 渲染层（cabinScene.ts）另有一组**源码级回归断言**，见文件末尾：
 * 「两处屏幕 clamp」是任务书点名的漏改坑，纯函数测不到，得钉住源码。
 */
import { describe, expect, it } from 'vitest';
import cabinSceneSrc from './cabinScene.ts?raw';
import {
  WORLD,
  cameraMaxX,
  cameraTargetX,
  clampCameraToPerson,
  clampCameraX,
  clampToWorld,
  edgeFadeAlpha,
  layerVirtualWidth,
  lerpCameraX,
  screenToWorldX,
  worldScreens,
  worldToScreenX,
  type CabinWorld,
} from './cabinConfig';

/** 480×270 基准下的一屏宽度（虚拟像素），与 cabinScene 的 VIRTUAL_H=270 对应。 */
const VIEW_W = 480;

/**
 * 源码文本。用 Vite 的 `?raw` 导入（本仓已有先例：
 * src/components/workbench/diffParse.consistency.test.ts），
 * 不引入 node:fs —— 当前 tsconfig 的 `types` 只含 vite/client，node 内置模块无类型。
 */
const src = cabinSceneSrc;

describe('G3-1 · 世界配置', () => {
  it('横向可探索约 8 屏', () => {
    expect(worldScreens(WORLD, VIEW_W)).toBe(8);
  });

  it('房屋与出生点都落在世界内，且出生点在房屋右侧', () => {
    expect(WORLD.houseX).toBeGreaterThan(WORLD.edgeMargin);
    expect(WORLD.houseX).toBeLessThan(WORLD.width - WORLD.edgeMargin);
    expect(WORLD.spawnX).toBeGreaterThan(WORLD.houseX);
    expect(WORLD.spawnX).toBeLessThan(WORLD.width - WORLD.edgeMargin);
  });
});

describe('G3-2 · 世界 ↔ 屏幕坐标映射', () => {
  it('双向映射互为逆运算', () => {
    for (const [worldX, camX] of [
      [0, 0],
      [1660, 1420],
      [3840, 3360],
      [-500, 0],
      [1e7, 9_999_999],
    ] as const) {
      expect(screenToWorldX(worldToScreenX(worldX, camX), camX)).toBeCloseTo(worldX, 6);
    }
  });

  it('相机右移时同一世界点的屏幕偏移等量左移（世界不动）', () => {
    const worldX = 2000;
    const a = worldToScreenX(worldX, 1000);
    const b = worldToScreenX(worldX, 1010);
    expect(a - b).toBe(10);
  });

  it('clampToWorld 把越界值拉回 [edgeMargin, width-edgeMargin]', () => {
    expect(clampToWorld(-9999)).toBe(WORLD.edgeMargin);
    expect(clampToWorld(999999)).toBe(WORLD.width - WORLD.edgeMargin);
    expect(clampToWorld(1234)).toBe(1234); // 区间内原样返回
  });

  it('clampToWorld 对退化世界（比视口还窄）返回唯一合法点，不越界', () => {
    const tiny: CabinWorld = { ...WORLD, width: 100, edgeMargin: 24 };
    // edgeMargin=24 时 hi = 76 > lo，区间仍在
    expect(clampToWorld(-50, tiny)).toBe(24);
    expect(clampToWorld(500, tiny)).toBe(76);
    // 完全退化：edgeMargin*2 >= width → 只能落在世界中心
    const degenerate: CabinWorld = { ...WORLD, width: 40, edgeMargin: 24 };
    expect(clampToWorld(-50, degenerate)).toBe(20);
    expect(clampToWorld(500, degenerate)).toBe(20);
  });

  it('点击世界外不会把人拽回屏幕内：反投影后仍可落在世界远端', () => {
    // 相机停在 0，点击屏幕最左（虚拟 x=-100）→ 世界 x=-100 → 被钳到 edgeMargin
    expect(clampToWorld(screenToWorldX(-100, 0))).toBe(WORLD.edgeMargin);
    // 相机在 3360（世界右缘对齐视口右缘），点击屏幕最右（虚拟 x=VIEW_W）→ 世界 x=3840 → 钳到右缘内
    expect(clampToWorld(screenToWorldX(VIEW_W, 3360))).toBe(WORLD.width - WORLD.edgeMargin);
  });
});

describe('G3-5 · 相机平滑与范围', () => {
  it('cameraMaxX = 世界宽 - 视口宽；视口比世界宽时为 0（不出现负值）', () => {
    expect(cameraMaxX(VIEW_W)).toBe(WORLD.width - VIEW_W);
    expect(cameraMaxX(WORLD.width + 1000)).toBe(0);
  });

  it('clampCameraX 双向钳制，且永不返回区间外值', () => {
    expect(clampCameraX(-500, VIEW_W)).toBe(0);
    expect(clampCameraX(1e9, VIEW_W)).toBe(WORLD.width - VIEW_W);
    expect(clampCameraX(2000, VIEW_W)).toBe(2000);
  });

  it('cameraTargetX 把人物放到视口 cameraLead 处，到世界边缘则被钳住', () => {
    // 人物在世界中部：相机 = 人物 - 半屏，人物落在屏幕正中
    expect(cameraTargetX(2000, VIEW_W)).toBe(2000 - VIEW_W * WORLD.cameraLead);
    // 人物贴着左缘：相机必须为 0（人物偏左显示，不会把画面推到世界外）
    expect(cameraTargetX(WORLD.edgeMargin, VIEW_W)).toBe(0);
    // 人物贴着右缘：相机被钳在 cameraMaxX
    expect(cameraTargetX(WORLD.width - WORLD.edgeMargin, VIEW_W)).toBe(WORLD.width - VIEW_W);
  });

  it('lerpCameraX 单调趋近目标，不跳变（帧率无关平滑）', () => {
    let cam = 0;
    const target = 1000;
    let prev = cam;
    for (let i = 0; i < 200; i++) {
      cam = lerpCameraX(cam, target, 16);
      expect(cam).toBeGreaterThanOrEqual(prev); // 单调
      expect(cam).toBeLessThanOrEqual(target); // 不超调
      prev = cam;
    }
    expect(cam).toBeCloseTo(target, 0); // 200 帧 ×16ms 后基本到位
  });

  it('lerpCameraX 对 dt<=0 / 非有限输入安全（resize 与大坐标下不产生 NaN）', () => {
    expect(lerpCameraX(100, 500, 0)).toBe(100);
    expect(lerpCameraX(100, 500, -16)).toBe(100);
    expect(lerpCameraX(NaN, 500, 16)).toBeNaN(); // 不掩盖：原样返回 NaN（由 clampCameraX 兜底）
    // dt 非有限时向上钳到 250ms 上限：仍平滑、有限、且不超调
    const inf = lerpCameraX(100, 500, Number.POSITIVE_INFINITY);
    expect(Number.isFinite(inf)).toBe(true);
    expect(inf).toBeGreaterThan(100);
    expect(inf).toBeLessThanOrEqual(500);
  });

  it('超长帧（dt 被钳到 250ms）不会让相机一步跳到位', () => {
    const oneFrame = lerpCameraX(0, 3000, 100000);
    expect(oneFrame).toBeGreaterThan(0);
    expect(oneFrame).toBeLessThan(3000);
  });
});

describe('相机滞后时人物不被甩出视口', () => {
  /**
   * 回归用例：相机是 lerp 平滑的，长距离快走时相机会滞后于人物。
   * 只靠 lerpCameraX + clampCameraX（仅管世界边界）时，人物会被甩到视口外
   * （本地数值重放实测达 2000 屏幕 px）。clampCameraToPerson 是硬约束。
   */
  it('小步移动时是恒等映射（不改变手感）', () => {
    const personX = 2000;
    const cam = cameraTargetX(personX, VIEW_W); // 人物居中，lerp 已在安全区内
    expect(clampCameraToPerson(cam, personX, VIEW_W)).toBe(cam);
  });

  it('相机严重滞后时强行拉回，使人物落在视口内', () => {
    const personX = 3800; // 人物已跑到世界右缘附近
    const lagCam = 1600; // 相机还停在很靠左的位置（模拟快走滞后）
    const fixed = clampCameraToPerson(lagCam, personX, VIEW_W);
    const personScreen = worldToScreenX(personX, fixed);
    expect(personScreen).toBeGreaterThanOrEqual(0);
    expect(personScreen).toBeLessThanOrEqual(VIEW_W);
  });

  it('无论相机多滞后，人物屏幕投影始终 ∈ [0, viewWidth]', () => {
    for (let personX = 0; personX <= WORLD.width; personX += 97) {
      const p = clampToWorld(personX);
      for (const cam of [0, 500, 1420, 3360, 99999, -99999]) {
        const fixed = clampCameraToPerson(clampCameraX(cam, VIEW_W), p, VIEW_W);
        const screen = worldToScreenX(p, fixed);
        expect(screen).toBeGreaterThanOrEqual(0);
        expect(screen).toBeLessThanOrEqual(VIEW_W);
      }
    }
  });

  it('视口比世界宽时退化为不越世界的钳制（不产生矛盾区间）', () => {
    const overWide = WORLD.width + 500;
    expect(clampCameraToPerson(123, 500, overWide)).toBe(0);
    expect(clampCameraToPerson(99999, 500, overWide)).toBe(0);
  });

  it('人物贴世界左缘时仍留在画面内（安全区崩取的分支）', () => {
    // personX=24 → hi = 24-24 = 0，lo = 0，区间退化为一点
    const cam = clampCameraToPerson(1600, 24, VIEW_W);
    const screen = worldToScreenX(24, cam);
    expect(screen).toBeGreaterThanOrEqual(0);
    expect(screen).toBeLessThanOrEqual(VIEW_W);
    expect(cam).toBe(0);
  });
});

describe('G3-3 · 图层几何宽度', () => {
  it('宽度始终盖住视口，且带 margin', () => {
    expect(layerVirtualWidth(VIEW_W, 96 / 2)).toBeGreaterThan(VIEW_W);
    expect(layerVirtualWidth(VIEW_W, 1)).toBeGreaterThan(VIEW_W);
  });

  it('极端视口（1px / 极大值）不返回非法宽度', () => {
    expect(layerVirtualWidth(1, 0)).toBeGreaterThan(1);
    const huge = layerVirtualWidth(1e7, 1e7);
    expect(Number.isFinite(huge)).toBe(true);
    expect(huge).toBeGreaterThan(1e7);
  });
});

describe('G3-6 · 世界边界视觉收束', () => {
  it('相机在世界中部时两侧都不渐隐', () => {
    expect(edgeFadeAlpha(2000, VIEW_W)).toBe(0);
  });

  it('相机贴到左右缘时渐隐达到上限', () => {
    expect(edgeFadeAlpha(0, VIEW_W)).toBeCloseTo(WORLD.edgeFadeMax, 6);
    expect(edgeFadeAlpha(WORLD.width - VIEW_W, VIEW_W)).toBeCloseTo(WORLD.edgeFadeMax, 6);
  });

  it('渐隐强度从边缘向世界内部单调减弱，且恒在 [0, edgeFadeMax] 内', () => {
    let prev = Number.POSITIVE_INFINITY;
    for (let cam = 0; cam <= WORLD.edgeFadePx; cam += 5) {
      const a = edgeFadeAlpha(cam, VIEW_W);
      expect(a).toBeLessThanOrEqual(prev); // 离边缘越远越淡
      expect(a).toBeGreaterThanOrEqual(0);
      expect(a).toBeLessThanOrEqual(WORLD.edgeFadeMax);
      prev = a;
    }
    // 左右两侧对称
    expect(edgeFadeAlpha(60, VIEW_W)).toBeCloseTo(edgeFadeAlpha(WORLD.width - VIEW_W - 60, VIEW_W), 6);
  });

  it('edgeFadePx<=0 时不渐隐（配置防御，不产生 NaN/Infinity）', () => {
    expect(edgeFadeAlpha(0, VIEW_W, { ...WORLD, edgeFadePx: 0 })).toBe(0);
  });
});

describe('resize 后重算', () => {
  it('视口变宽后相机上界同步收紧，且重钳后仍在新界内', () => {
    const narrow = clampCameraX(9999, VIEW_W); // 旧界
    const wide = clampCameraX(9999, 900); // 视口 900 → 上界收紧到 2940
    expect(narrow).toBe(WORLD.width - VIEW_W);
    expect(wide).toBe(WORLD.width - 900);
    expect(wide).toBeLessThan(narrow);
  });

  it('视口比世界还宽时相机恒为 0（画面不越界、也不抖动）', () => {
    const overWide = WORLD.width + 500;
    expect(clampCameraX(123, overWide)).toBe(0);
    expect(cameraTargetX(500, overWide)).toBe(0);
  });

  it('resize 不改变人物的世界坐标（只重算投影与相机界）', () => {
    const personWorldX = 2500;
    // resize 前后人物世界坐标不变，只是屏幕投影随相机/viewport 变化
    const camBefore = cameraTargetX(personWorldX, VIEW_W);
    const camAfter = cameraTargetX(personWorldX, 900);
    expect(clampToWorld(personWorldX, WORLD)).toBe(personWorldX);
    expect(worldToScreenX(personWorldX, camAfter)).not.toBe(worldToScreenX(personWorldX, camBefore));
  });
});

describe('大坐标不溢出', () => {
  it('世界右缘附近的坐标运算全部有限', () => {
    const edge = WORLD.width - WORLD.edgeMargin;
    const cam = cameraMaxX(VIEW_W);
    expect(Number.isFinite(worldToScreenX(edge, cam))).toBe(true);
    expect(worldToScreenX(edge, cam)).toBe(VIEW_W - WORLD.edgeMargin);
    expect(Number.isFinite(cameraTargetX(edge, VIEW_W))).toBe(true);
    expect(Number.isFinite(edgeFadeAlpha(cam, VIEW_W))).toBe(true);
  });

  it('极大世界宽度（1e7 虚拟 px）下映射仍精确、无精度崩坏', () => {
    const huge: CabinWorld = { ...WORLD, width: 1e7 };
    const x = 9_000_000;
    expect(screenToWorldX(worldToScreenX(x, 8_000_000), 8_000_000)).toBeCloseTo(x, 3);
    expect(clampToWorld(1e9, huge)).toBe(huge.width - huge.edgeMargin);
    expect(cameraMaxX(VIEW_W, huge)).toBe(huge.width - VIEW_W);
  });
});

/**
 * 源码级回归护栏（任务书点名的「G3-2 漏改点」就是这里）：
 * 旧实现有两处 `clamp(..., 30, width - 30)` 的**屏幕内**钳制 ——
 * 一处在 clampWalk、一处在 pointertap。漏改任何一处都会留下
 * 「能走出屏幕、但点击屏幕外又被拉回来」的半吊子实现。
 * 纯函数测试无法覆盖「场景层是否还残留旧 clamp」，故在此钉住源码。
 */
describe('cabinScene.ts 源码护栏：屏幕内 clamp 已彻底移除', () => {
  it('不再存在 width - 30 / 30 形式的屏幕内钳制', () => {
    expect(src).not.toContain('width - 30');
    expect(src).not.toContain('clamp(e.global.x');
  });

  it('指针点击改为世界反投影 + clampToWorld', () => {
    expect(src).toContain('screenToWorldX(screenVirtualX, cameraX)');
    expect(src).toContain('clampToWorld(screenToWorldX(screenVirtualX, cameraX))');
  });

  it('地面层接入 tilePosition（parallax = 1.0，与人物同速）', () => {
    expect(src).toContain('groundLayer.tilePosition.x = -cameraX * PARALLAX.ground');
    expect(src.match(/ground:\s*1\.0/)).toBeTruthy();
  });

  it('站立时不再把人物重置到 width*0.52（瞬移回中点的根因）', () => {
    expect(src).not.toContain('width * 0.52');
  });

  it('房屋锚定世界坐标 WORLD.houseX，而非 width*0.36', () => {
    expect(src).not.toContain('width * 0.36');
    expect(src).toContain('worldToScreenX(WORLD.houseX, cameraX)');
  });
});