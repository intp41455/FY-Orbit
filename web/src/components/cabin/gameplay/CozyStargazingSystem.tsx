import { useState, useEffect } from 'react';

export interface ConstellationDef {
  id: string;
  name: string;
  latin: string;
  stars: number;
  season: string;
  lore: string;
  discovered: boolean;
}

export const INITIAL_CONSTELLATIONS: ConstellationDef[] = [
  {
    id: 'ursa_major',
    name: '大熊座 · 北斗七星',
    latin: 'Ursa Major',
    stars: 7,
    season: '春夜',
    lore: '指引北极星的七颗晶亮星辰，夜空中最醒目的勺子。',
    discovered: true,
  },
  {
    id: 'cassiopeia',
    name: '仙后座 · 银河王座',
    latin: 'Cassiopeia',
    stars: 5,
    season: '秋夜',
    lore: '如璀璨的字母 W 悬挂在银河浅滩，闪耀着冰蓝色的光辉。',
    discovered: true,
  },
  {
    id: 'cygnus',
    name: '天鹅座 · 北十字',
    latin: 'Cygnus',
    stars: 6,
    season: '夏夜',
    lore: '展翅飞跃银河中心的展翅白天鹅，天津四如心跳般搏动。',
    discovered: false,
  },
  {
    id: 'orion',
    name: '猎户座 · 寒夜武仙',
    latin: 'Orion',
    stars: 7,
    season: '冬夜',
    lore: '腰带三连星如利剑出鞘，参宿四与参宿七一红一蓝交相辉映。',
    discovered: false,
  },
  {
    id: 'pegasus',
    name: '飞马座 · 秋季大四边形',
    latin: 'Pegasus',
    stars: 4,
    season: '秋夜',
    lore: '展开洁白飞翼的天马，在浩瀚天幕中奔腾而过。',
    discovered: false,
  },
  {
    id: 'crux',
    name: '南十字座 · 南海之灯',
    latin: 'Crux',
    stars: 4,
    season: '四季',
    lore: '南方天际的钻石十字，航行者世代仰望的坐标。',
    discovered: false,
  },
];

export interface CozyStargazingSystemProps {
  onWishUponStar?: (shards: number, coins: number) => void;
  onClose: () => void;
}

export function CozyStargazingSystem({ onWishUponStar, onClose }: CozyStargazingSystemProps) {
  const [constellations, setConstellations] = useState<ConstellationDef[]>(INITIAL_CONSTELLATIONS);
  const [starShards, setStarShards] = useState(12);
  const [meteorActive, setMeteorActive] = useState(true);
  const [meteorPosition, setMeteorPosition] = useState({ x: 25, y: 30 });
  const [wishingNotice, setWishingNotice] = useState<string | null>(null);

  // 流星定时划过
  useEffect(() => {
    const timer = setInterval(() => {
      setMeteorActive(true);
      setMeteorPosition({
        x: Math.floor(Math.random() * 70) + 10,
        y: Math.floor(Math.random() * 40) + 15,
      });
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const handleCatchMeteor = () => {
    if (!meteorActive) return;
    setMeteorActive(false);
    const gainedShards = Math.floor(Math.random() * 3) + 2;
    const gainedCoins = gainedShards * 15;
    const nextShards = starShards + gainedShards;
    setStarShards(nextShards);
    setWishingNotice(`🌟 抓到了流星！许愿成功，获得【星芒碎片 ×${gainedShards}】与【金币 +${gainedCoins}】！`);

    // 随机点亮一个未发现的星座
    const undiscovered = constellations.filter((c) => !c.discovered);
    if (undiscovered.length > 0) {
      const pick = undiscovered[0];
      setConstellations((prev) =>
        prev.map((c) => (c.id === pick.id ? { ...c, discovered: true } : c)),
      );
    }

    if (onWishUponStar) {
      onWishUponStar(gainedShards, gainedCoins);
    }
  };

  const handleExchangeShards = () => {
    if (starShards < 5) return;
    const coinsReward = starShards * 12;
    setStarShards(0);
    setWishingNotice(`🪙 成功将所有星芒碎片兑换为 ${coinsReward} 金币！`);
    if (onWishUponStar) {
      onWishUponStar(0, coinsReward);
    }
  };

  return (
    <div className="pixel-modal-backdrop" data-testid="stargazing-backdrop" onClick={onClose}>
      <div
        className="pixel-modal stargazing-modal"
        data-testid="stargazing-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="星空观星与流星祈愿"
      >
        <div className="pixel-modal-header">
          <h2 className="pixel-modal-title">🔭 沉浸观星台与流星祈愿</h2>
          <button
            type="button"
            className="pixel-modal-close"
            data-testid="stargazing-close"
            onClick={onClose}
            aria-label="关闭观星台"
          >
            ✕
          </button>
        </div>

        <div className="pixel-modal-body stargazing-body">
          {wishingNotice && (
            <div className="stargazing-notice" role="status" data-testid="stargazing-notice">
              {wishingNotice}
            </div>
          )}

          {/* 交互夜空观测区 */}
          <div className="night-sky-canvas" data-testid="night-sky-canvas">
            <div className="sky-nebula-glow" />
            <div className="twinkling-stars">
              <span className="star s1" style={{ top: '15%', left: '20%' }}>✦</span>
              <span className="star s2" style={{ top: '35%', left: '75%' }}>✧</span>
              <span className="star s3" style={{ top: '65%', left: '40%' }}>✦</span>
              <span className="star s4" style={{ top: '25%', left: '85%' }}>★</span>
              <span className="star s5" style={{ top: '70%', left: '15%' }}>✧</span>
              <span className="star s6" style={{ top: '80%', left: '80%' }}>✦</span>
            </div>

            {/* 流星实体 */}
            {meteorActive && (
              <button
                type="button"
                className="meteor-btn"
                data-testid="meteor-target"
                style={{ top: `${meteorPosition.y}%`, left: `${meteorPosition.x}%` }}
                onClick={handleCatchMeteor}
                title="点击流星许愿！"
              >
                <span className="meteor-trail">💫 点击许愿!</span>
              </button>
            )}

            <div className="sky-hint">
              {meteorActive ? '✨ 天际滑过一颗流星，快点击它许愿！' : '🔭 调校望远镜焦点，静待下一颗流星划过...'}
            </div>
          </div>

          {/* 资产兑换与状态 */}
          <div className="stargazing-status-bar">
            <div className="shard-count" data-testid="star-shard-count">
              <span>🌟 已收集星芒碎片：</span>
              <strong>{starShards} 枚</strong>
            </div>
            <button
              type="button"
              className="pixel-btn primary small"
              data-testid="exchange-shards-btn"
              disabled={starShards < 5}
              onClick={handleExchangeShards}
            >
              兑换为金币 ({starShards * 12}金)
            </button>
          </div>

          {/* 星座图鉴 */}
          <div className="constellation-section">
            <h4 className="constellation-title">🌌 治愈星座图鉴档案</h4>
            <div className="constellation-grid" data-testid="constellation-grid">
              {constellations.map((c) => (
                <div
                  key={c.id}
                  className={`constellation-card ${c.discovered ? 'discovered' : 'locked'}`}
                  data-testid={`constellation-${c.id}`}
                >
                  <div className="constellation-header">
                    <strong>{c.discovered ? c.name : '未知星宿 ???'}</strong>
                    <span className="latin">{c.discovered ? c.latin : 'Locked'}</span>
                  </div>
                  <p className="constellation-lore">
                    {c.discovered ? c.lore : '尚未在夜空中完成连线观测。等待流星许愿解锁。'}
                  </p>
                  <div className="constellation-meta">
                    <span>{c.discovered ? `主星: ${c.stars}颗` : '需观测'}</span>
                    <span>{c.discovered ? `季候: ${c.season}` : ''}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
