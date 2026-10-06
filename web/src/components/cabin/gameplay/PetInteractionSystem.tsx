import React, { useState } from 'react';
import './cozyGameplay.css';

export interface PetGift {
  name: string;
  icon: string;
  coinsBonus: number;
}

const PET_GIFTS: PetGift[] = [
  { name: '亮晶晶的古铜币', icon: '🪙', coinsBonus: 30 },
  { name: '树根下挖出的小松果', icon: '🌰', coinsBonus: 15 },
  { name: '溪边捡到的彩光贝壳', icon: '🐚', coinsBonus: 25 },
  { name: '神秘的古代星光种子', icon: '✨', coinsBonus: 50 },
];

export interface PetInteractionProps {
  petName?: string;
  petColor?: string;
  onReceiveGift: (gift: PetGift) => void;
  onClose: () => void;
}

export const PetInteractionSystem: React.FC<PetInteractionProps> = ({
  petName = '毛球',
  onReceiveGift,
  onClose,
}) => {
  const [affection, setAffection] = useState<number>(3); // 1-5 hearts
  const [actionMessage, setActionMessage] = useState<string>('小家伙欢快地摇着尾巴，期待着你的陪伴！');
  const [isForaging, setIsForaging] = useState<boolean>(false);
  const [lastGift, setLastGift] = useState<PetGift | null>(null);

  const handlePet = () => {
    setActionMessage(`你温柔地抚摸了 ${petName}，它舒服地眯起眼睛，发出轻柔的呼噜声～ (❤️ 好感度上升)`);
    setAffection((prev) => Math.min(5, prev + 1));
  };

  const handleFeed = () => {
    setActionMessage(`你喂给 ${petName} 一块香脆小鱼饼干，它吧唧吧唧吃得一干二净！(🍪 精力满满)`);
    setAffection((prev) => Math.min(5, prev + 1));
  };

  const handleGroom = () => {
    setActionMessage(`你用小毛刷帮 ${petName} 梳理了毛发，小家伙变得格外蓬松整洁！(✨ 魅力提升)`);
  };

  const handleForage = () => {
    setIsForaging(true);
    setLastGift(null);
    setActionMessage(`${petName} 嗅了嗅地面，兴奋地刨开草丛去寻宝啦...`);

    setTimeout(() => {
      setIsForaging(false);
      const gift = PET_GIFTS[Math.floor(Math.random() * PET_GIFTS.length)];
      setLastGift(gift);
      setActionMessage(`汪！${petName} 摇着尾巴叼回来了一个礼物：${gift.icon} ${gift.name}！`);
      onReceiveGift(gift);
    }, 1200);
  };

  return (
    <div className="pet-interaction-modal" data-testid="pet-interaction-modal">
      <div className="pet-interaction-card">
        <div className="cozy-modal-header">
          <h3>🐾 萌宠亲密互动</h3>
          <button type="button" className="cozy-close-btn" data-testid="pet-interaction-close" onClick={onClose}>✕</button>
        </div>

        <div className="pet-stage-view">
          <div className="pet-avatar-circle animate-bounce">
            <span className="big-pet-emoji">🐕</span>
          </div>

          <h4 className="pet-headline" data-testid="pet-name">{petName}</h4>
          <div className="pet-hearts-row" title={`亲密度：${affection}/5`}>
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className={`heart-icon ${i < affection ? 'active' : 'empty'}`}>
                {i < affection ? '💖' : '🤍'}
              </span>
            ))}
          </div>

          <div className="pet-dialogue-bubble" data-testid="pet-dialogue">
            <p>{actionMessage}</p>
          </div>

          <div className="pet-action-grid">
            <button type="button" className="cozy-pet-btn" data-testid="pet-action-pat" onClick={handlePet}>
              ✋ 抚摸揉揉
            </button>
            <button type="button" className="cozy-pet-btn" data-testid="pet-action-feed" onClick={handleFeed}>
              🍪 投喂零食
            </button>
            <button type="button" className="cozy-pet-btn" data-testid="pet-action-groom" onClick={handleGroom}>
              🪮 梳理毛发
            </button>
            <button
              type="button"
              className="cozy-pet-btn highlight"
              data-testid="pet-action-scavenge"
              disabled={isForaging}
              onClick={handleForage}
            >
              {isForaging ? '🐾 寻宝中...' : '🦴 出门寻宝！'}
            </button>
          </div>

          {lastGift && (
            <div className="pet-gift-toast animate-pulse">
              <span>🎁 寻宝收获：{lastGift.icon} {lastGift.name} (+{lastGift.coinsBonus}🪙)</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
