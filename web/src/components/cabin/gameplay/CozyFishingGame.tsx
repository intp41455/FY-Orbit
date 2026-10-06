import React, { useState, useEffect, useRef } from 'react';
import './cozyGameplay.css';

export interface FishCatch {
  id: string;
  name: string;
  rarity: 'common' | 'rare' | 'epic' | 'legendary';
  weight: number;
  coinsValue: number;
  icon: string;
  description: string;
}

const FISH_CATALOG: FishCatch[] = [
  { id: 'silver_minnow', name: '晨光银鱼', rarity: 'common', weight: 0.8, coinsValue: 15, icon: '🐟', description: '林间清泉常见的小鱼，鳞片在晨光下闪闪发亮。' },
  { id: 'river_trout', name: '碧溪彩鲑', rarity: 'common', weight: 2.1, coinsValue: 28, icon: '🐠', description: '肉质鲜美的冷水鲑鱼，适合做成慢烤鱼排。' },
  { id: 'golden_carp', name: '好运金鲤', rarity: 'rare', weight: 4.5, coinsValue: 65, icon: '🐡', description: '传说能给钓到它的人带来好运与意外财宝。' },
  { id: 'moon_jelly', name: '夜光水母', rarity: 'rare', weight: 1.2, coinsValue: 80, icon: '🪼', description: '在静夜水潭中散发柔和幽光的奇妙生物。' },
  { id: 'star_bubble', name: '星光气泡鱼', rarity: 'epic', weight: 3.6, coinsValue: 140, icon: '✨', description: '体表有如同银河般流转的璀璨光斑，十分罕见。' },
  { id: 'ancient_coelacanth', name: '远古腔棘鱼', rarity: 'legendary', weight: 12.8, coinsValue: 350, icon: '👑', description: '自远古时代繁衍至今的活化石，沉睡在深邃水底。' },
];

export interface CozyFishingGameProps {
  onCatchFish: (fish: FishCatch) => void;
  onClose: () => void;
}

type FishingState = 'idle' | 'cast' | 'waiting' | 'bite' | 'reeling' | 'caught' | 'escaped';

export const CozyFishingGame: React.FC<CozyFishingGameProps> = ({ onCatchFish, onClose }) => {
  const [state, setState] = useState<FishingState>('idle');
  const [tension, setTension] = useState<number>(50);
  const [currentFish, setCurrentFish] = useState<FishCatch | null>(null);
  const [biteCountdown, setBiteCountdown] = useState<number>(0);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  // 开始抛竿
  const handleCast = () => {
    setState('cast');
    setTimeout(() => {
      setState('waiting');
      const waitTime = 2000 + Math.random() * 3000;
      timerRef.current = setTimeout(() => {
        // 鱼咬钩！
        setState('bite');
        setBiteCountdown(3);
      }, waitTime);
    }, 600);
  };

  // 咬钩后的 3 秒倒计时
  useEffect(() => {
    if (state === 'bite') {
      const interval = setInterval(() => {
        setBiteCountdown((prev) => {
          if (prev <= 1) {
            clearInterval(interval);
            setState('escaped');
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [state]);

  // 玩家按下提竿
  const handleStrike = () => {
    if (state !== 'bite') return;
    // 随机选一条鱼
    const rand = Math.random();
    let picked: FishCatch;
    if (rand < 0.5) picked = FISH_CATALOG[0];
    else if (rand < 0.75) picked = FISH_CATALOG[1];
    else if (rand < 0.90) picked = FISH_CATALOG[2];
    else if (rand < 0.96) picked = FISH_CATALOG[3];
    else if (rand < 0.99) picked = FISH_CATALOG[4];
    else picked = FISH_CATALOG[5];

    // 附带微小浮动重量
    const finalFish: FishCatch = {
      ...picked,
      weight: parseFloat((picked.weight * (0.85 + Math.random() * 0.3)).toFixed(1)),
    };
    setCurrentFish(finalFish);
    setState('reeling');
    setTension(50);
  };

  // 卷线耐力拉扯
  const handleReel = () => {
    if (state !== 'reeling') return;
    setTension((prev) => {
      const next = prev + 18;
      if (next >= 100) {
        // 成功钓起！
        if (currentFish) {
          onCatchFish(currentFish);
        }
        setState('caught');
        return 100;
      }
      return next;
    });
  };

  // 耐力自然回退
  useEffect(() => {
    if (state === 'reeling') {
      const interval = setInterval(() => {
        setTension((prev) => {
          if (prev <= 5) {
            setState('escaped');
            return 0;
          }
          return Math.max(0, prev - 4);
        });
      }, 150);
      return () => clearInterval(interval);
    }
  }, [state]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return (
    <div className="cozy-fishing-modal" data-testid="cozy-fishing-modal">
      <div className="cozy-fishing-card" data-testid="cozy-fishing-card">
        <div className="cozy-modal-header">
          <h3>🎣 碧水垂钓</h3>
          <button type="button" className="cozy-close-btn" data-testid="cozy-fishing-close" onClick={onClose}>✕</button>
        </div>

        <div className="cozy-water-stage">
          {/* 水波动画背景 */}
          <div className="cozy-water-ripples" />

          {state === 'idle' && (
            <div className="cozy-fish-status" data-testid="cozy-fish-status">
              <span className="big-icon">🌊</span>
              <p>微风徐徐，水波潋滟。抛出鱼线试试手气吧！</p>
              <button type="button" className="cozy-action-btn primary" data-testid="cozy-fish-btn" onClick={handleCast}>
                抛竿投线
              </button>
            </div>
          )}

          {state === 'cast' && (
            <div className="cozy-fish-status" data-testid="cozy-fish-status">
              <span className="big-icon animate-bounce">🪝</span>
              <p>咻——鱼线轻柔划破水面，浮标入水...</p>
            </div>
          )}

          {state === 'waiting' && (
            <div className="cozy-fish-status" data-testid="cozy-fish-status">
              <span className="big-icon animate-pulse">🪿</span>
              <p>浮标在水面轻轻摇曳，静心等待鱼儿靠近...</p>
            </div>
          )}

          {state === 'bite' && (
            <div className="cozy-fish-status bite-alert">
              <span className="big-icon animate-ping">❗</span>
              <p className="highlight-text">有鱼咬钩了！快提竿！（{biteCountdown}秒）</p>
              <button type="button" className="cozy-action-btn pulse-btn" onClick={handleStrike}>
                ⚡ 立即提竿！
              </button>
            </div>
          )}

          {state === 'reeling' && (
            <div className="cozy-fish-status reeling-zone">
              <p>收紧鱼线！快速连击收起大鱼！</p>
              <div className="tension-bar-track">
                <div className="tension-bar-fill" style={{ width: `${tension}%` }} />
              </div>
              <button type="button" className="cozy-action-btn primary big-reel" onClick={handleReel}>
                🎣 快速收线 ({Math.round(tension)}%)
              </button>
            </div>
          )}

          {state === 'caught' && currentFish && (
            <div className="cozy-fish-status victory">
              <span className="big-icon">{currentFish.icon}</span>
              <h4 className={`rarity-${currentFish.rarity}`}>{currentFish.name}！</h4>
              <p className="fish-meta">重量：{currentFish.weight} kg · 价值：🪙 {currentFish.coinsValue} 金币</p>
              <p className="fish-desc">{currentFish.description}</p>
              <button type="button" className="cozy-action-btn primary" onClick={() => setState('idle')}>
                再钓一次
              </button>
            </div>
          )}

          {state === 'escaped' && (
            <div className="cozy-fish-status failed">
              <span className="big-icon">💨</span>
              <p>扑通！鱼儿机敏地溜走了，鱼钩空空如也...</p>
              <button type="button" className="cozy-action-btn secondary" onClick={() => setState('idle')}>
                重整鱼线
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
