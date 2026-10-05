import { useState } from 'react';
import { CABIN_BACKGROUNDS, type CabinBackgroundId, type TimeOfDay } from '../cabinConfig';
import { craftLockReason, sortedBag, type LifeSnapshot } from '../gameplay/lifeApi';

export type HudModalType = 'backpack' | 'craft' | 'quest' | 'npc' | 'map' | 'settings' | null;

export type CabinHudProps = {
  view: 'outdoor' | 'indoor';
  editMode: boolean;
  onToggleDecorate: () => void;
  onNavigateBack: () => void;
  currentTheme?: CabinBackgroundId;
  onSelectTheme?: (id: CabinBackgroundId) => void;
  timeOfDay?: TimeOfDay;
  onSelectTimeOfDay?: (time: TimeOfDay) => void;
  loading?: boolean;
  error?: string | null;
  onAction?: (action: string, args?: Record<string, unknown>) => Promise<void> | void;
} & (
  | {
      /** 判据 U2b：接线时金币由调用方传入真实数据，TS 编译期强制约束（无假默认值） */
      coins: number;
      /** 判据 U2b：时间与天气文案由调用方传入真实数据，TS 编译期强制约束（无假默认值） */
      dayText: string;
      snapshot?: LifeSnapshot | null;
    }
  | {
      coins?: number;
      dayText?: string;
      snapshot?: undefined;
    }
);

export function CabinHud({
  view,
  editMode,
  onToggleDecorate,
  onNavigateBack,
  currentTheme = 'forest',
  onSelectTheme,
  timeOfDay = 'day',
  onSelectTimeOfDay,
  coins,
  dayText,
  snapshot,
  loading,
  error,
  onAction,
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
            <span className="time-text">{dayText ?? ''}</span>
          </div>
        </div>

        <div className="pixel-top-center" data-testid="pixel-coins">
          <span className="coin-icon">🪙</span>
          <span className="coin-amount">{coins !== undefined ? coins.toLocaleString() : '0'}</span>
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
              {error && (
                <div className="pixel-modal-error" role="alert" data-testid="pixel-modal-error">
                  数据加载失败：{error}
                </div>
              )}
              {loading && !error && (
                <div className="pixel-modal-loading" data-testid="pixel-modal-loading">
                  正在同步数据...
                </div>
              )}

              {activeModal === 'backpack' && (
                <div className="pixel-backpack-view">
                  {(() => {
                    const bagItems = sortedBag(snapshot?.save?.bag ?? {});
                    if (bagItems.length === 0) {
                      return <div className="pixel-empty-state" data-testid="pixel-empty-backpack">背包空空如也</div>;
                    }
                    return (
                      <div className="pixel-grid-items" data-testid="pixel-grid-items">
                        {bagItems.map((item) => (
                          <div
                            key={item.id}
                            className="pixel-item-slot"
                            data-testid={`bag-item-${item.id}`}
                            title={`${item.id} ×${item.qty}`}
                          >
                            {item.id} ×{item.qty}
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                </div>
              )}

              {activeModal === 'craft' && (
                <div className="pixel-craft-view">
                  {(() => {
                    const recipes = snapshot?.craft ?? [];
                    if (recipes.length === 0) {
                      return <div className="pixel-empty-state" data-testid="pixel-empty-craft">暂无配方</div>;
                    }
                    return (
                      <div className="pixel-recipe-list" data-testid="pixel-recipe-list">
                        {recipes.map((r) => (
                          <div className="pixel-recipe-card" key={r.id} data-testid={`craft-item-${r.id}`}>
                            <div className="recipe-info">
                              <span className="recipe-label">{r.label}</span>
                              <span className="recipe-desc">{craftLockReason(r)}</span>
                            </div>
                            <button
                              type="button"
                              className={`pixel-btn small ${r.unlocked ? 'primary' : ''}`}
                              disabled={!r.unlocked}
                              onClick={() => onAction?.('craft', { recipe_id: r.id })}
                            >
                              {r.unlocked ? '制作' : '未解锁'}
                            </button>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                </div>
              )}

              {activeModal === 'quest' && (
                <div className="pixel-quest-view">
                  {(() => {
                    const entries = snapshot?.save?.quest_log?.entries ?? [];
                    if (entries.length === 0) {
                      return <div className="pixel-empty-state" data-testid="pixel-empty-quest">暂无任务</div>;
                    }
                    return (
                      <div className="pixel-quest-list" data-testid="pixel-quest-list">
                        {entries.map((e) => (
                          <div className="pixel-quest-item" key={e.quest_id} data-testid={`quest-item-${e.quest_id}`}>
                            <strong>{e.quest_id}</strong>
                            <p>
                              进度: {e.progress}{' '}
                              {e.done ? (e.claimed ? '（已领奖）' : '（已完成）') : '（进行中）'}
                            </p>
                            {e.done && !e.claimed && (
                              <button
                                type="button"
                                className="pixel-btn small primary"
                                onClick={() => onAction?.('claim_quest', { quest_id: e.quest_id })}
                              >
                                领奖
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                </div>
              )}

              {activeModal === 'npc' && (
                <div className="pixel-npc-view">
                  {(() => {
                    const npcs = snapshot?.npcs ?? [];
                    if (npcs.length === 0) {
                      return <div className="pixel-empty-state" data-testid="pixel-empty-npc">暂无村民</div>;
                    }
                    return (
                      <div className="pixel-npc-list" data-testid="pixel-npc-list">
                        {npcs.map((n) => (
                          <div className="pixel-npc-card" key={n.id} data-testid={`npc-item-${n.id}`}>
                            <div className="npc-info">
                              <span className="npc-name">{n.name} · {n.role}</span>
                              <span className="heart-rating" title={`好感度: ${n.hearts}心`}>
                                {n.hearts_display}
                              </span>
                              {n.place && (
                                <span className="npc-place">
                                  位置: {n.place} {n.activity ? `(${n.activity})` : ''}
                                </span>
                              )}
                            </div>
                            <button
                              type="button"
                              className="pixel-btn small"
                              onClick={() => onAction?.('gift', { npc_id: n.id })}
                            >
                              赠礼
                            </button>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
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
