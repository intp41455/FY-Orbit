import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CozyWorldMapSystem, WORLD_MAP_DATA } from './CozyWorldMapSystem';

describe('🗺️ 大世界场景舆图与探索 (CozyWorldMapSystem)', () => {
  it('渲染 9 大场景卡片与详情，支持选择与传送', () => {
    vi.useFakeTimers();
    const onSelectTheme = vi.fn();
    const onSelectTimeOfDay = vi.fn();
    const onClose = vi.fn();

    render(
      <CozyWorldMapSystem
        currentTheme="forest"
        onSelectTheme={onSelectTheme}
        timeOfDay="day"
        onSelectTimeOfDay={onSelectTimeOfDay}
        onClose={onClose}
      />,
    );

    expect(screen.getByTestId('world-map-modal')).toBeInTheDocument();
    expect(screen.getByTestId('world-map-grid')).toBeInTheDocument();

    // 9 个场景全部卡片渲染
    for (const m of WORLD_MAP_DATA) {
      expect(screen.getByTestId(`world-map-card-${m.id}`)).toBeInTheDocument();
    }

    // 选中 ink 水墨场景
    const inkCard = screen.getByTestId('world-map-card-ink');
    fireEvent.click(inkCard);

    // 详情区更新
    expect(screen.getByTestId('world-map-detail').textContent).toContain('古风水墨');
    expect(screen.getByTestId('specialty-清香白莲')).toBeInTheDocument();

    // 点击传送
    const teleportBtn = screen.getByTestId('world-map-teleport-btn');
    fireEvent.click(teleportBtn);

    expect(onSelectTheme).toHaveBeenCalledWith('ink');
    expect(onSelectTimeOfDay).toHaveBeenCalledWith('dawn');
    expect(screen.getByTestId('travel-status')).toBeInTheDocument();

    vi.advanceTimersByTime(500);
    expect(onClose).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('点击关闭按钮触发 onClose', () => {
    const onClose = vi.fn();
    render(
      <CozyWorldMapSystem
        currentTheme="forest"
        onSelectTheme={vi.fn()}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByTestId('world-map-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
