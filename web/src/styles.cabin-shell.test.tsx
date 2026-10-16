import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CabinShell } from './components/cabin/CabinShell';

/**
 * 回归护栏：`/cabin` 路由必须自带内嵌外壳。
 *
 * 缺陷复现：`/cabin` 直接渲染 `.cabin-root`（`position: fixed; inset: 0`）而没有
 * `.cabin-shell` 包裹，于是游戏铺满整个视口、侧栏与工作台外壳完全点不到 ——
 * I3 文档承诺的「内嵌工作台」形态在结构上并不存在。
 *
 * 这里断言的是**结构性事实**（外壳容器在不在、形态对不对），不是渲染不抛异常。
 */
describe('cabin 内嵌外壳契约', () => {
  it('内嵌形态必须提供有确定尺寸的容器，把 fixed 的游戏核心收进去', () => {
    render(
      <CabinShell mode="embedded" label="内嵌工作台 · 我的小屋">
        <div data-testid="cabin-root" />
      </CabinShell>,
    );
    const shell = screen.getByTestId('cabin-shell');
    expect(shell.getAttribute('data-mode')).toBe('embedded');
    // 内嵌容器必须在场，且游戏核心是它的直接子节点（CSS 选择器依赖这个结构）。
    expect(shell.contains(screen.getByTestId('cabin-root'))).toBe(true);
  });

  it('独立全屏形态才允许外壳自己铺满视口', () => {
    render(
      <CabinShell mode="fullscreen">
        <div data-testid="cabin-root" />
      </CabinShell>,
    );
    expect(screen.getByTestId('cabin-shell').getAttribute('data-mode'))
      .toBe('fullscreen');
  });
});
