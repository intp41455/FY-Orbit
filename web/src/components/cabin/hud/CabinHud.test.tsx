import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CabinHud } from './CabinHud';
import { CABIN_BACKGROUNDS } from '../cabinConfig';

describe('A6 · UI 像素化重构 (CabinHud)', () => {
  it('渲染 32px 顶部状态栏：返回按钮、天气时间、金币与 64×64 小地图', () => {
    const onNavigateBack = vi.fn();
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={onNavigateBack}
        coins={2500}
        dayText="第5天 下午 晴"
        timeOfDay="day"
      />,
    );

    expect(screen.getByTestId('pixel-top-bar')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-back-btn')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-time-weather').textContent).toContain('第5天 下午 晴');
    expect(screen.getByTestId('pixel-coins').textContent).toContain('2,500');
    expect(screen.getByTestId('pixel-minimap')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('pixel-back-btn'));
    expect(onNavigateBack).toHaveBeenCalledTimes(1);
  });

  it('渲染 40px 底部工具栏：6 个圆形 32×32 像素操作按钮', () => {
    const onToggleDecorate = vi.fn();
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={onToggleDecorate}
        onNavigateBack={vi.fn()}
      />,
    );

    const bottomBar = screen.getByTestId('pixel-bottom-bar');
    expect(bottomBar).toBeInTheDocument();

    expect(screen.getByTestId('hud-btn-backpack')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-craft')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-quest')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-npc')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-decorate')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-map')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('hud-btn-decorate'));
    expect(onToggleDecorate).toHaveBeenCalledTimes(1);
  });

  it('点击地图按钮弹出像素弹窗，展示 9 个场景并支持主题切换', () => {
    const onSelectTheme = vi.fn();
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        currentTheme="forest"
        onSelectTheme={onSelectTheme}
      />,
    );

    // 点击地图按钮
    fireEvent.click(screen.getByTestId('hud-btn-map'));
    expect(screen.getByTestId('pixel-modal-map')).toBeInTheDocument();

    // 9 个背景全部对应 CABIN_BACKGROUNDS 标签
    for (const bg of CABIN_BACKGROUNDS) {
      expect(screen.getByText(bg.label)).toBeInTheDocument();
    }

    // 点击其中一个主题（例如 古风桃源 ink）
    fireEvent.click(screen.getByText('古风桃源'));
    expect(onSelectTheme).toHaveBeenCalledWith('ink');

    // 弹窗自动关闭
    expect(screen.queryByTestId('pixel-modal-map')).not.toBeInTheDocument();
  });

  it('点击设置可切换 4 个昼夜时段（清晨/白天/黄昏/夜晚）', () => {
    const onSelectTimeOfDay = vi.fn();
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        timeOfDay="day"
        onSelectTimeOfDay={onSelectTimeOfDay}
      />,
    );

    fireEvent.click(screen.getByTestId('pixel-settings-btn'));
    expect(screen.getByTestId('pixel-modal-settings')).toBeInTheDocument();

    fireEvent.click(screen.getByText('夜晚'));
    expect(onSelectTimeOfDay).toHaveBeenCalledWith('night');

    fireEvent.click(screen.getByText('黄昏'));
    expect(onSelectTimeOfDay).toHaveBeenCalledWith('dusk');
  });

  it('点击背包展开背包弹窗，点击关闭按钮关闭弹窗', () => {
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId('hud-btn-backpack'));
    expect(screen.getByTestId('pixel-modal-backpack')).toBeInTheDocument();
    expect(screen.getByText(/玩家背包/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('pixel-modal-close'));
    expect(screen.queryByTestId('pixel-modal-backpack')).not.toBeInTheDocument();
  });
});
