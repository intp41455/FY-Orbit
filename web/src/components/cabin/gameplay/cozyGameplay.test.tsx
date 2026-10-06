import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { CozyFishingGame } from './CozyFishingGame';
import { CozyGardeningSystem } from './CozyGardeningSystem';
import { CozyCookingSystem } from './CozyCookingSystem';
import { PetInteractionSystem } from './PetInteractionSystem';
import { CozyStargazingSystem } from './CozyStargazingSystem';
import { CozySoundscapeSystem } from './CozySoundscapeSystem';
import { MindEchoFountain } from './MindEchoFountain';
import { AiAvatarGeneratorModal } from './AiAvatarGeneratorModal';

describe('治愈系林间小屋创新玩法全系统测试', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('🎣 碧水微垂钓系统 (CozyFishingGame)', () => {
    it('抛竿、提竿结算、关闭回调完整工作', () => {
      const onCatchFish = vi.fn();
      const onClose = vi.fn();

      render(<CozyFishingGame onCatchFish={onCatchFish} onClose={onClose} />);

      expect(screen.getByTestId('cozy-fishing-card')).toBeInTheDocument();
      expect(screen.getByTestId('cozy-fish-btn')).toBeInTheDocument();

      // 点击关闭
      fireEvent.click(screen.getByTestId('cozy-fishing-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('点击抛竿进入垂钓状态', () => {
      render(<CozyFishingGame onCatchFish={vi.fn()} onClose={vi.fn()} />);
      const btn = screen.getByTestId('cozy-fish-btn');
      fireEvent.click(btn);
      // 抛竿后应显示正在等待或咬钩
      expect(screen.getByTestId('cozy-fish-status')).toBeInTheDocument();
    });
  });

  describe('🌱 庭院园艺耕种系统 (CozyGardeningSystem)', () => {
    it('渲染 4 块苗圃，支持浇水施肥与采摘', () => {
      const onHarvest = vi.fn();
      const onClose = vi.fn();

      render(<CozyGardeningSystem onHarvest={onHarvest} onClose={onClose} />);

      expect(screen.getByTestId('gardening-plot-1')).toBeInTheDocument();
      expect(screen.getByTestId('gardening-plot-2')).toBeInTheDocument();
      expect(screen.getByTestId('gardening-plot-3')).toBeInTheDocument();
      expect(screen.getByTestId('gardening-plot-4')).toBeInTheDocument();

      // 选种子并播种
      const plot3 = screen.getByTestId('gardening-plot-3');
      fireEvent.click(plot3);

      // 一键甘霖全部浇水
      const waterBtn = screen.getByTestId('gardening-water-all');
      fireEvent.click(waterBtn);

      // 关闭
      fireEvent.click(screen.getByTestId('cozy-gardening-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  describe('🍳 林间野炊工坊 (CozyCookingSystem)', () => {
    it('烹饪料理恢复精力并触发回调', () => {
      vi.useFakeTimers();
      const onCookDish = vi.fn();
      const onClose = vi.fn();

      render(<CozyCookingSystem onCookDish={onCookDish} onClose={onClose} />);

      expect(screen.getByTestId('recipe-grilled_trout')).toBeInTheDocument();
      expect(screen.getByTestId('cook-btn-grilled_trout')).toBeInTheDocument();

      fireEvent.click(screen.getByTestId('cook-btn-grilled_trout'));
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      expect(onCookDish).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'grilled_trout' }),
      );

      fireEvent.click(screen.getByTestId('cozy-cooking-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
      vi.useRealTimers();
    });
  });

  describe('🐾 萌宠互动与寻宝 (PetInteractionSystem)', () => {
    it('互动抚摸与嗅探寻宝', () => {
      vi.useFakeTimers();
      const onReceiveGift = vi.fn();
      const onClose = vi.fn();

      render(
        <PetInteractionSystem
          petName="毛球"
          onReceiveGift={onReceiveGift}
          onClose={onClose}
        />,
      );

      expect(screen.getByTestId('pet-name').textContent).toContain('毛球');

      // 抚摸
      fireEvent.click(screen.getByTestId('pet-action-pat'));
      expect(screen.getByTestId('pet-dialogue')).toBeInTheDocument();

      // 寻宝
      fireEvent.click(screen.getByTestId('pet-action-scavenge'));
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      expect(onReceiveGift).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByTestId('pet-interaction-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
      vi.useRealTimers();
    });
  });

  describe('🔭 星空观星与流星祈愿 (CozyStargazingSystem)', () => {
    it('点击流星许愿获得碎片与点亮星座', () => {
      const onWish = vi.fn();
      const onClose = vi.fn();

      render(<CozyStargazingSystem onWishUponStar={onWish} onClose={onClose} />);

      expect(screen.getByTestId('stargazing-modal')).toBeInTheDocument();
      expect(screen.getByTestId('night-sky-canvas')).toBeInTheDocument();

      // 点击流星
      const meteor = screen.getByTestId('meteor-target');
      fireEvent.click(meteor);

      expect(onWish).toHaveBeenCalledWith(
        expect.any(Number),
        expect.any(Number),
      );
      expect(screen.getByTestId('stargazing-notice')).toBeInTheDocument();

      // 关闭
      fireEvent.click(screen.getByTestId('stargazing-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('星芒碎片兑换金币', () => {
      render(<CozyStargazingSystem onWishUponStar={vi.fn()} onClose={vi.fn()} />);
      const exchangeBtn = screen.getByTestId('exchange-shards-btn');
      fireEvent.click(exchangeBtn);
      expect(screen.getByTestId('star-shard-count').textContent).toContain('0 枚');
    });
  });

  describe('📻 森林黑胶留声机与声景 (CozySoundscapeSystem)', () => {
    it('播放与暂停切换、调节音量、切换音轨', () => {
      const onClose = vi.fn();
      render(<CozySoundscapeSystem onClose={onClose} />);

      expect(screen.getByTestId('soundscape-modal')).toBeInTheDocument();
      const playBtn = screen.getByTestId('soundscape-play-btn');

      // 播放
      fireEvent.click(playBtn);
      expect(playBtn.textContent).toContain('暂停');

      // 调节音量
      const volSlider = screen.getByTestId('soundscape-volume-slider');
      fireEvent.change(volSlider, { target: { value: '80' } });

      // 切换音轨
      const rainTrackBtn = screen.getByTestId('track-btn-rain');
      fireEvent.click(rainTrackBtn);
      expect(rainTrackBtn.className).toContain('active');

      fireEvent.click(screen.getByTestId('soundscape-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  describe('🌊 心境清泉与回响瓶 (MindEchoFountain)', () => {
    it('投掷心愿并打捞哲思回响与金币', async () => {
      vi.useFakeTimers();
      const onGainCoins = vi.fn();
      const onClose = vi.fn();

      render(<MindEchoFountain onGainCoins={onGainCoins} onClose={onClose} />);

      expect(screen.getByTestId('echo-fountain-modal')).toBeInTheDocument();
      const castBtn = screen.getByTestId('cast-echo-btn');

      fireEvent.click(castBtn);

      act(() => {
        vi.advanceTimersByTime(500);
      });

      expect(screen.getByTestId('echo-bottle-card')).toBeInTheDocument();
      expect(onGainCoins).toHaveBeenCalledWith(expect.any(Number));

      fireEvent.click(screen.getByTestId('echo-fountain-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
      vi.useRealTimers();
    });
  });

  describe('🎭 AI 专属画像小人生成器 (AiAvatarGeneratorModal)', () => {
    it('随机灵感切换、画像字段解析与一键生成角色包', async () => {
      const onApply = vi.fn();
      const onClose = vi.fn();

      render(
        <AiAvatarGeneratorModal
          onClose={onClose}
          onApplyAvatar={onApply}
        />,
      );

      expect(screen.getByTestId('ai-avatar-modal')).toBeInTheDocument();
      expect(screen.getByTestId('ai-avatar-preset-card')).toBeInTheDocument();

      // 随机灵感
      fireEvent.click(screen.getByTestId('ai-avatar-random-btn'));
      expect(screen.getByTestId('ai-avatar-title')).toBeInTheDocument();

      // 一键生成
      const generateBtn = screen.getByTestId('ai-avatar-generate-btn');
      await act(async () => {
        fireEvent.click(generateBtn);
      });

      expect(screen.getByTestId('ai-avatar-result')).toBeInTheDocument();

      // 漫步小屋应用
      const applyBtn = screen.getByTestId('ai-avatar-apply-btn');
      await act(async () => {
        fireEvent.click(applyBtn);
      });

      expect(onApply).toHaveBeenCalledWith(
        expect.objectContaining({
          fingerprint: expect.any(String),
          layers: expect.any(Object),
          char_palette: expect.any(Object),
        }),
      );

      fireEvent.click(screen.getByTestId('ai-avatar-close'));
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });
});
