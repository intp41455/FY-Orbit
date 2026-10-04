import { useState } from 'react';
import { CABIN_BACKGROUNDS, type CabinBackgroundId, type TimeOfDay } from '../cabinConfig';

export type HudModalType = 'backpack' | 'craft' | 'quest' | 'npc' | 'map' | 'settings' | null;

export interface CabinHudProps {
  view: 'outdoor' | 'indoor';
  editMode: boolean;
  onToggleDecorate: () => void;
  onNavigateBack: () => void;
  currentTheme?: CabinBackgroundId;
  onSelectTheme?: (id: CabinBackgroundId) => void;
  timeOfDay?: TimeOfDay;
  onSelectTimeOfDay?: (time: TimeOfDay) => void;
  coins?: number;
  dayText?: string;
}

export function CabinHud({
  view,
  editMode,
  onToggleDecorate,
  onNavigateBack,
  currentTheme = 'forest',
  onSelectTheme,
  timeOfDay = 'day',
  onSelectTimeOfDay,
  coins = 1280,
  dayText = '第3天 上午 晴',
}: CabinHudProps) {
  const [activeModal, setActiveModal] = useState<HudModalType>(null);

  const weatherIcon = timeOfDay === 'night' ? '🌙' : timeOfDay === 'dusk' ? '🌅' : '☀️';

  return (
    <div className="cabin-hud-root" data-testid="cabin-hud-root">
      {/* 顶部状态栏（高 32px） */}
      <header className="pixel-top-bar" data-testid="pixel-top-bar">
        <div className="pixel-top-left">
          <button
            type="button"
            className="pixel-btn pixel-back-btn"
            onClick={onNavigateBack}
            data-testid="pixel-back-btn"
            title="返回私人空间"
          >
            ← 返回
          </button>
          <div className="pixel-time-weather" data-testid="pixel-time-weather">
            <span className="weather-icon">{weatherIcon}</span>
            <span className="time-text">{dayText}</span>
          </div>
        </div>

        <div className="pixel-top-center" data-testid="pixel-coins">
          <span className="coin-icon">🪙</span>
          <span className="coin-amount">{coins.toLocaleString()}</span>
        </div>

        <div className="pixel-top-right">
          <button
            type="button"
            className="pixel-btn pixel-settings-btn"
            data-testid="pixel-settings-btn"
            title="设置"
            onClick={() => setActiveModal('settings')}
          >
            ⚙️ 设置
          </button>

          {/* 圆形小地图 64×64 */}
          <div
            className="pixel-minimap"
            data-testid="pixel-minimap"
            title="小地图：显示当前位置与周边"
            onClick={() => setActiveModal('map')}
          >
            <div className="minimap-inner">
              <span className="minimap-house-marker" title="我的小屋">🏠</span>
              <span className="minimap-player-dot" title="玩家当前位置" />
              <div className="minimap-grid-ring" />
            </div>
          </div>
        </div>
      </header>

      {/* 底部工具栏（高 40px，6 个 32×32 圆形按钮） */}
      <nav className="pixel-bottom-bar" data-testid="pixel-bottom-bar" aria-label="游戏工具栏">
        <button
          type="button"
          className={`pixel-circle-btn ${activeModal === 'backpack' ? 'active' : ''}`}
          data-testid="hud-btn-backpack"
          title="背包"
          onClick={() => setActiveModal(activeModal === 'backpack' ? null : 'backpack')}
        >
          🎒
        </button>
        <button
          type="button"
          className={`pixel-circle-btn ${activeModal === 'craft' ? 'active' : ''}`}
          data-testid="hud-btn-craft"
          title="制作台"
          onClick={() => setActiveModal(activeModal === 'craft' ? null : 'craft')}
        >
          🔨
        </button>
        <button
          type="button"
          className={`pixel-circle-btn ${activeModal === 'quest' ? 'active' : ''}`}
          data-testid="hud-btn-quest"
          title="任务列表"
          onClick={() => setActiveModal(activeModal === 'quest' ? null : 'quest')}
        >
          📜
        </button>
        <button
          type="button"
          className={`pixel-circle-btn ${activeModal === 'npc' ? 'active' : ''}`}
          data-testid="hud-btn-npc"
          title="NPC社交"
          onClick={() => setActiveModal(activeModal === 'npc' ? null : 'npc')}
        >
          👥
        </button>
        <button
          type="button"
          className={`pixel-circle-btn ${editMode || view === 'indoor' ? 'active' : ''}`}
          data-testid="hud-btn-decorate"
          title={view === 'indoor' ? '退出室内' : '装修小屋'}
          onClick={onToggleDecorate}
        >
          🪑
        </button>
        <button
          type="button"
          className={`pixel-circle-btn ${activeModal === 'map' ? 'active' : ''}`}
          data-testid="hud-btn-map"
          title="地图传送"
          onClick={() => setActiveModal(activeModal === 'map' ? null : 'map')}
        >
          🗺️
        </button>
      </nav>

      {/* 像素弹窗（圆角 4px，4px 边框，浅米色底 + 深棕像素字） */}
      {activeModal && (
        <div className="pixel-modal-backdrop" data-testid="pixel-modal-backdrop" onClick={() => setActiveModal(null)}>
          <div
            className="pixel-modal"
            data-testid={`pixel-modal-${activeModal}`}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="pixel-modal-header">
              <h2 className="pixel-modal-title">
                {activeModal === 'backpack' && '🎒 玩家背包'}
                {activeModal === 'craft' && '🔨 制作台'}
                {activeModal === 'quest' && '📜 任务日志'}
                {activeModal === 'npc' && '👥 村民社交'}
                {activeModal === 'map' && '🗺️ 地图传送'}
                {activeModal === 'settings' && '⚙️ 游戏设置'}
              </h2>
              <button
                type="button"
                className="pixel-modal-close"
                data-testid="pixel-modal-close"
                onClick={() => setActiveModal(null)}
              >
                ✕
              </button>
            </div>

            <div className="pixel-modal-body">
              {activeModal === 'backpack' && (
                <div className="pixel-grid-items">
                  <div className="pixel-item-slot" title="木材 ×12">🪵 ×12</div>
                  <div className="pixel-item-slot" title="石料 ×8">🪨 ×8</div>
                  <div className="pixel-item-slot" title="野红果 ×5">🍎 ×5</div>
                  <div className="pixel-item-slot" title="发光矿石 ×3">💎 ×3</div>
                  <div className="pixel-item-slot" title="魔法草药 ×6">🌿 ×6</div>
                  <div className="pixel-item-slot empty" />
                  <div className="pixel-item-slot empty" />
                  <div className="pixel-item-slot empty" />
                </div>
              )}

              {activeModal === 'craft' && (
                <div className="pixel-recipe-list">
                  <div className="pixel-recipe-card">
                    <span>原木长椅 (木材×4)</span>
                    <button type="button" className="pixel-btn small">制作</button>
                  </div>
                  <div className="pixel-recipe-card">
                    <span>石砌花坛 (石料×6)</span>
                    <button type="button" className="pixel-btn small">制作</button>
                  </div>
                  <div className="pixel-recipe-card">
                    <span>微光提灯 (发光矿石×2, 木材×2)</span>
                    <button type="button" className="pixel-btn small">制作</button>
                  </div>
                </div>
              )}

              {activeModal === 'quest' && (
                <div className="pixel-quest-list">
                  <div className="pixel-quest-item">
                    <strong>主线：初到森林小屋</strong>
                    <p>修缮并布置你的第一个温馨小院 (1/3)</p>
                  </div>
                  <div className="pixel-quest-item">
                    <strong>日常：采集草药</strong>
                    <p>在森林中采集 3 株魔法草药 (已完成)</p>
                  </div>
                </div>
              )}

              {activeModal === 'npc' && (
                <div className="pixel-npc-list">
                  <div className="pixel-npc-card">
                    <span>森林守护者 · 艾尔</span>
                    <span className="heart-rating">❤️❤️❤️🤍🤍 (3心)</span>
                  </div>
                  <div className="pixel-npc-card">
                    <span>杂货店主 · 诺姆</span>
                    <span className="heart-rating">❤️❤️❤️❤️🤍 (4心)</span>
                  </div>
                </div>
              )}

              {activeModal === 'map' && (
                <div className="pixel-map-selector">
                  <p className="pixel-modal-subtitle">选择目标传送场景：</p>
                  <div className="pixel-map-buttons">
                    {CABIN_BACKGROUNDS.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={`pixel-btn ${currentTheme === item.id ? 'primary active' : ''}`}
                        onClick={() => {
                          onSelectTheme?.(item.id);
                          setActiveModal(null);
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {activeModal === 'settings' && (
                <div className="pixel-settings-content">
                  <div className="pixel-setting-row">
                    <span>时段与色温：</span>
                    <div className="pixel-btn-group">
                      {(['dawn', 'day', 'dusk', 'night'] as TimeOfDay[]).map((t) => (
                        <button
                          key={t}
                          type="button"
                          className={`pixel-btn small ${timeOfDay === t ? 'active' : ''}`}
                          onClick={() => onSelectTimeOfDay?.(t)}
                        >
                          {t === 'dawn' ? '清晨' : t === 'day' ? '白天' : t === 'dusk' ? '黄昏' : '夜晚'}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
