import { useState } from 'react';

export interface EchoMessage {
  id: string;
  source: string;
  quote: string;
  reflection: string;
  coinsBonus: number;
}

export const ECHO_MESSAGES_POOL: EchoMessage[] = [
  {
    id: 'echo_1',
    source: '卡尔·荣格 · 潜意识与自性',
    quote: '往外张望的人在做梦，向内审视的人才清醒。',
    reflection: '今天请给自己留出十分钟独处，不带评判地倾听内心的真实声音。',
    coinsBonus: 30,
  },
  {
    id: 'echo_2',
    source: '老子 · 《道德经》',
    quote: '致虚极，守静笃。万物并作，吾以观复。',
    reflection: '如同小屋前的落叶，顺应自然节律，不必急于给出答案，答案就在流动中。',
    coinsBonus: 25,
  },
  {
    id: 'echo_3',
    source: '阿尔贝·加缪 · 荒诞与向阳',
    quote: '在隆冬，我终于知道，我身上有一个不可战胜的夏天。',
    reflection: '即便外界风雪交加，你内心的韧性与暖意始终不可磨灭。',
    coinsBonus: 35,
  },
  {
    id: 'echo_4',
    source: '罗洛·梅 · 《爱与意志》',
    quote: '勇气并非没有恐惧，而是认识到有比恐惧更为重要的事情。',
    reflection: '每一次微小的探险与行动，都是对生命的深情拥抱。',
    coinsBonus: 40,
  },
  {
    id: 'echo_5',
    source: '赫尔曼·黑塞 · 《流浪者之歌》',
    quote: '寻找意味着：有目标；而发现意味着：自由，敞开，没有目的。',
    reflection: '在林间散散步吧，不需要达成任何指标，单纯享受微风与阳光。',
    coinsBonus: 28,
  },
];

export interface MindEchoFountainProps {
  onGainCoins?: (coins: number) => void;
  onClose: () => void;
}

export function MindEchoFountain({ onGainCoins, onClose }: MindEchoFountainProps) {
  const [echoHistory, setEchoHistory] = useState<EchoMessage[]>([]);
  const [currentEcho, setCurrentEcho] = useState<EchoMessage | null>(null);
  const [casting, setCasting] = useState(false);

  const handleCastBottle = () => {
    setCasting(true);
    setTimeout(() => {
      const pick = ECHO_MESSAGES_POOL[Math.floor(Math.random() * ECHO_MESSAGES_POOL.length)];
      setCurrentEcho(pick);
      setEchoHistory((prev) => [pick, ...prev.filter((p) => p.id !== pick.id)]);
      setCasting(false);
      if (onGainCoins) {
        onGainCoins(pick.coinsBonus);
      }
    }, 400);
  };

  return (
    <div className="pixel-modal-backdrop" data-testid="echo-fountain-backdrop" onClick={onClose}>
      <div
        className="pixel-modal echo-fountain-modal"
        data-testid="echo-fountain-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="心境许愿泉与灵魂回响瓶"
      >
        <div className="pixel-modal-header">
          <h2 className="pixel-modal-title">🌊 心境清泉与灵魂回响瓶</h2>
          <button
            type="button"
            className="pixel-modal-close"
            data-testid="echo-fountain-close"
            onClick={onClose}
            aria-label="关闭清泉"
          >
            ✕
          </button>
        </div>

        <div className="pixel-modal-body echo-fountain-body">
          {/* 泉水水面与投掷动效 */}
          <div className="fountain-pool" data-testid="fountain-pool">
            <div className="pool-ripple" />
            <div className="pool-glow">✨ 碧水映月，波光幽微 ✨</div>
            <p className="pool-desc">
              投入一枚许愿树叶或金币，从微波浅滩中打捞封存的心灵启示漂流瓶。
            </p>
            <button
              type="button"
              className="pixel-btn primary highlight cast-btn"
              data-testid="cast-echo-btn"
              disabled={casting}
              onClick={handleCastBottle}
            >
              {casting ? '🌊 水波激荡中...' : '🍃 投下心愿 · 打捞回响瓶'}
            </button>
          </div>

          {/* 当前打捞出的启示 */}
          {currentEcho && (
            <div className="echo-bottle-card" data-testid="echo-bottle-card">
              <div className="bottle-header">
                <span className="bottle-tag">📜 哲思回响</span>
                <span className="bottle-bonus">🪙 幸运金币 +{currentEcho.coinsBonus}</span>
              </div>
              <blockquote className="echo-quote">“{currentEcho.quote}”</blockquote>
              <div className="echo-source">—— {currentEcho.source}</div>
              <div className="echo-reflection">
                <strong>💡 今日心境寄语：</strong>
                <span>{currentEcho.reflection}</span>
              </div>
            </div>
          )}

          {/* 历史启示记录 */}
          {echoHistory.length > 0 && (
            <div className="echo-history" data-testid="echo-history">
              <h4>📚 已收藏的灵魂笺语：</h4>
              <div className="history-list">
                {echoHistory.map((item) => (
                  <div key={item.id} className="history-item" data-testid={`history-item-${item.id}`}>
                    <span className="item-quote">“{item.quote}”</span>
                    <span className="item-src">({item.source})</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
