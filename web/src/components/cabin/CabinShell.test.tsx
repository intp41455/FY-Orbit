/**
 * I3 · 双形态外壳回归护栏。
 *
 * 锁的是「游戏核心与外壳解耦」这件事本身，而非像素：外壳只决定容器与形态，
 * 不感知游戏内容；形态切换只回传目标形态，不做路由跳转（路由由页面决定）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CabinShell } from './CabinShell';
import { CABIN_SHELL_STORAGE_KEY } from './cabinViewport';

beforeEach(() => {
  localStorage.clear();
});

describe('CabinShell · 双形态外壳', () => {
  it('内嵌形态：data-mode=embedded，游戏核心原样挂在外壳容器内', () => {
    render(
      <CabinShell mode="embedded">
        <div data-testid="game-core">核心</div>
      </CabinShell>,
    );
    const shell = screen.getByTestId('cabin-shell');
    expect(shell.dataset.mode).toBe('embedded');
    expect(screen.getByTestId('game-core').parentElement).toBe(shell);
  });

  it('独立全屏形态：data-mode=fullscreen', () => {
    render(
      <CabinShell mode="fullscreen">
        <div data-testid="game-core">核心</div>
      </CabinShell>,
    );
    expect(screen.getByTestId('cabin-shell').dataset.mode).toBe('fullscreen');
  });

  it('未传 onRequestMode 时不渲染切换按钮（外壳退化为纯容器）', () => {
    render(
      <CabinShell mode="embedded">
        <div data-testid="game-core">核心</div>
      </CabinShell>,
    );
    expect(screen.queryByTestId('cabin-shell-toggle')).toBeNull();
  });

  it('切换按钮只回传「目标形态」，外壳自己不跳转路由', () => {
    const onRequestMode = vi.fn();
    render(
      <CabinShell mode="embedded" onRequestMode={onRequestMode}>
        <div data-testid="game-core">核心</div>
      </CabinShell>,
    );
    const btn = screen.getByTestId('cabin-shell-toggle');
    expect(btn.dataset.targetMode).toBe('fullscreen');
    fireEvent.click(btn);
    expect(onRequestMode).toHaveBeenCalledWith('fullscreen');
  });

  it('全屏形态下按钮指向 embedded（双向可逆）', () => {
    const onRequestMode = vi.fn();
    render(
      <CabinShell mode="fullscreen" onRequestMode={onRequestMode}>
        <div data-testid="game-core">核心</div>
      </CabinShell>,
    );
    expect(screen.getByTestId('cabin-shell-toggle').dataset.targetMode).toBe('embedded');
    fireEvent.click(screen.getByTestId('cabin-shell-toggle'));
    expect(onRequestMode).toHaveBeenCalledWith('embedded');
  });
});

describe('外壳偏好持久化', () => {
  it('非法值回退默认内嵌；写入后可正确读回', async () => {
    localStorage.setItem(CABIN_SHELL_STORAGE_KEY, 'not-a-mode');
    const mod = await import('./cabinViewport');
    expect(mod.loadShellMode()).toBe('embedded');
    mod.saveShellMode('fullscreen');
    expect(mod.loadShellMode()).toBe('fullscreen');
  });
});
