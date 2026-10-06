import { describe, expect, it } from 'vitest';
import cabinSceneSrc from './cabinScene.ts?raw';
import {
  WORLD,
  cameraTargetY,
  clampCameraY,
  lerpCameraY,
  depthPerspectiveScale,
} from './cabinConfig';
import {
  createCabinInput,
  isEditableElement,
} from './cabinInput';
import {
  evaluateTrailAtDepth,
  evaluateHorizonRidgeOffset,
  getWindingPathDef,
} from './cabinWindingPaths';
import type { CanonicalThemeId } from './cabinThemedWorlds';

describe('1. Cabin Keyboard Input System (cabinInput.ts)', () => {
  it('初始状态全部动作为 false，移动轴向量为零', () => {
    const input = createCabinInput({ target: window });
    expect(input.isActionActive('move_left')).toBe(false);
    expect(input.isActionActive('move_right')).toBe(false);
    expect(input.isActionActive('move_up')).toBe(false);
    expect(input.isActionActive('move_down')).toBe(false);
    expect(input.isActionActive('jump')).toBe(false);
    expect(input.isActionActive('crouch')).toBe(false);
    expect(input.getMovementAxis()).toEqual({ x: 0, depth: 0 });
    input.destroy();
  });

  it('支持 WASD 与方向键映射到四向移动与纵深探索', () => {
    const input = createCabinInput({ target: window });

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyA' }));
    expect(input.isActionActive('move_left')).toBe(true);
    expect(input.getMovementAxis().x).toBe(-1);

    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyA' }));
    expect(input.isActionActive('move_left')).toBe(false);

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowRight' }));
    expect(input.isActionActive('move_right')).toBe(true);
    expect(input.getMovementAxis().x).toBe(1);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'ArrowRight' }));

    // 深入探索（W / ArrowUp -> depth -1）
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW' }));
    expect(input.isActionActive('move_up')).toBe(true);
    expect(input.getMovementAxis().depth).toBe(-1);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyW' }));

    // 向前走近（S / ArrowDown -> depth +1）
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowDown' }));
    expect(input.isActionActive('move_down')).toBe(true);
    expect(input.getMovementAxis().depth).toBe(1);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'ArrowDown' }));

    input.destroy();
  });

  it('跳跃支持单次消费 (consumeJump) 与按键缓冲 (hasJumpBuffered)', () => {
    const input = createCabinInput({ target: window, jumpBufferMs: 150 });

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space' }));
    expect(input.hasJumpBuffered()).toBe(true);
    expect(input.consumeJump()).toBe(true);
    // 消费后立即失效，防止连跳
    expect(input.consumeJump()).toBe(false);
    expect(input.hasJumpBuffered()).toBe(false);

    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'Space' }));
    input.destroy();
  });

  it('长按 S 键且无水平移动时处于下蹲状态 (isCrouchHeld)', () => {
    const input = createCabinInput({ target: window });

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyS' }));
    expect(input.isActionActive('move_down')).toBe(true);
    expect(input.isActionActive('crouch')).toBe(true);
    expect(input.isCrouchHeld()).toBe(true);

    // 水平移动时解除下蹲（优先跑动）
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyD' }));
    expect(input.isCrouchHeld()).toBe(false);

    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyD' }));
    expect(input.isCrouchHeld()).toBe(true);

    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyS' }));
    expect(input.isCrouchHeld()).toBe(false);

    input.destroy();
  });

  it('在文本输入框 (input/textarea/contenteditable) 中按键不干扰角色操作', () => {
    const input = createCabinInput({ target: window });

    const inputElem = document.createElement('input');
    document.body.appendChild(inputElem);
    expect(isEditableElement(inputElem)).toBe(true);

    inputElem.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyA', bubbles: true }));
    expect(input.isActionActive('move_left')).toBe(false);

    const textarea = document.createElement('textarea');
    document.body.appendChild(textarea);
    expect(isEditableElement(textarea)).toBe(true);

    textarea.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', bubbles: true }));
    expect(input.hasJumpBuffered()).toBe(false);
    expect(input.consumeJump()).toBe(false);

    document.body.removeChild(inputElem);
    document.body.removeChild(textarea);
    input.destroy();
  });

  it('调用 destroy 后彻底解绑事件并重置所有动作状态', () => {
    const input = createCabinInput({ target: window });

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyD' }));
    expect(input.isActionActive('move_right')).toBe(true);

    input.destroy();
    expect(input.isActionActive('move_right')).toBe(false);
    expect(input.getActiveKeys().size).toBe(0);

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyD' }));
    expect(input.isActionActive('move_right')).toBe(false);
  });
});

describe('2. 2.5D 双轴纵深相机与微透视 (cabinConfig.ts)', () => {
  it('cameraTargetY 在基准深度 (0.55) 为 0，向前向后对称偏移', () => {
    expect(cameraTargetY(0.55)).toBeCloseTo(0, 3);

    const span = WORLD.cameraYSpan ?? 80;
    // 向纵深内部走（depth 变小）-> 相机目标 Y 为负（视野下移，聚焦远山深林）
    const farTargetY = cameraTargetY(0.0);
    expect(farTargetY).toBeLessThan(0);
    expect(farTargetY).toBeCloseTo(-span, 1);

    // 向屏幕前方走（depth 变大）-> 相机目标 Y 为正（视野上抬，聚焦近景脚下）
    const nearTargetY = cameraTargetY(1.0);
    expect(nearTargetY).toBeGreaterThan(0);
    expect(nearTargetY).toBeCloseTo(span, 1);
  });

  it('clampCameraY 严格限制在上下 span 范围之内', () => {
    const span = WORLD.cameraYSpan ?? 80;
    expect(clampCameraY(-999)).toBe(-span);
    expect(clampCameraY(999)).toBe(span);
    expect(clampCameraY(0)).toBe(0);
    expect(clampCameraY(25)).toBe(25);
  });

  it('lerpCameraY 平滑收敛且不发生震荡', () => {
    let curY = 0;
    const targetY = -35;
    for (let i = 0; i < 60; i++) {
      curY = lerpCameraY(curY, targetY, 16.6);
    }
    // 60 帧后应平滑接近目标值
    expect(curY).toBeLessThan(-30);
    expect(curY).toBeGreaterThanOrEqual(-35);
  });

  it('depthPerspectiveScale 随纵深单调递减并落在 [0.82, 1.05] 契约区间', () => {
    const scaleFar = depthPerspectiveScale(0.0);
    const scaleMid = depthPerspectiveScale(0.5);
    const scaleNear = depthPerspectiveScale(1.0);

    expect(scaleFar).toBeCloseTo(0.82, 2);
    expect(scaleNear).toBeCloseTo(1.05, 2);
    expect(scaleFar).toBeLessThan(scaleMid);
    expect(scaleMid).toBeLessThan(scaleNear);

    // 边界钳制
    expect(depthPerspectiveScale(-0.5)).toBe(0.82);
    expect(depthPerspectiveScale(1.5)).toBe(1.05);
  });
});

describe('3. 地图纵深蜿蜒小道系统 (cabinWindingPaths.ts)', () => {
  const THEMES: CanonicalThemeId[] = ['forest', 'garden', 'golden_field', 'stream', 'observatory'];

  it('5 大核心主题全部具备完整配置且远近透视收缩比正确', () => {
    for (const theme of THEMES) {
      const def = getWindingPathDef(theme);
      expect(def).toBeDefined();
      expect(def.name).toBeTruthy();
      expect(def.colors.pathSurface).toBeGreaterThan(0);
      expect(def.farWidth).toBeLessThan(def.nearWidth);
      expect(def.farWidth).toBeGreaterThanOrEqual(16);
      expect(def.nearWidth).toBeGreaterThanOrEqual(56);
      expect(def.controlPoints.length).toBeGreaterThanOrEqual(4);
      expect(def.decorations.length).toBeGreaterThanOrEqual(4);
    }
  });

  it('evaluateTrailAtDepth 在任意纵深点 [0, 1] 输出平滑连续的世界 X 坐标与透视宽度', () => {
    const def = getWindingPathDef('forest');

    const sample0 = evaluateTrailAtDepth(def, 0);
    const sample05 = evaluateTrailAtDepth(def, 0.5);
    const sample1 = evaluateTrailAtDepth(def, 1);

    expect(sample0.width).toBeCloseTo(def.farWidth, 1);
    expect(sample1.width).toBeCloseTo(def.nearWidth, 1);
    expect(sample05.width).toBeGreaterThan(sample0.width);
    expect(sample05.width).toBeLessThan(sample1.width);

    expect(typeof sample05.worldX).toBe('number');
    expect(Number.isFinite(sample05.worldX)).toBe(true);
  });

  it('evaluateHorizonRidgeOffset 破除水平呆板切线，提供自然起伏高度', () => {
    const def = getWindingPathDef('stream');
    const y1 = evaluateHorizonRidgeOffset(100, def);
    const y2 = evaluateHorizonRidgeOffset(500, def);
    const y3 = evaluateHorizonRidgeOffset(900, def);

    // 保证起伏有波动，不是单一常量 0
    expect(new Set([y1, y2, y3]).size).toBeGreaterThan(1);
    expect(typeof y1).toBe('number');
    expect(Number.isFinite(y1)).toBe(true);
  });
});

describe('4. 场景渲染层契约与源码守卫 (cabinScene.ts Regression Guards)', () => {
  it('严格保留原 G3 视差与投影关键源码守卫，防止破坏历史测试', () => {
    expect(cabinSceneSrc).toContain('screenToWorldX(screenVirtualX, cameraX)');
    expect(cabinSceneSrc).toContain('clampToWorld(screenToWorldX(screenVirtualX, cameraX))');
    expect(cabinSceneSrc).toContain('groundLayer.tilePosition.x = -cameraX * PARALLAX.ground');
    expect(cabinSceneSrc).toContain('ground: 1.0');
    expect(cabinSceneSrc).toContain('worldToScreenX(WORLD.houseX, cameraX)');
  });

  it('包含 2.5D 双轴摄像机、微透视与键盘输入关键实现', () => {
    expect(cabinSceneSrc).toContain('cameraTargetY');
    expect(cabinSceneSrc).toContain('lerpCameraY');
    expect(cabinSceneSrc).toContain('clampCameraY');
    expect(cabinSceneSrc).toContain('createCabinInput');
    expect(cabinSceneSrc).toContain('depthPerspectiveScale');
    expect(cabinSceneSrc).toContain('buildWindingPathGraphics');
    expect(cabinSceneSrc).toContain('getInputSystem');
    expect(cabinSceneSrc).toContain('getWindingPath');
    expect(cabinSceneSrc).toContain('getJumpState');
  });
});
