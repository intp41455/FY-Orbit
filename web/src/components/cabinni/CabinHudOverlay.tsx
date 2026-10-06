/**
 * 包 D 私有组件 · 小屋 HUD 覆盖层（DOM 层，不碰 pixi canvas）
 * ---------------------------------------------------------------------------
 * 重构说明（T1.2 & T1.3）：
 * 1. 右侧栏 UI：采用半透明磨砂玻璃卡片 + HUD Drawer 选项卡折叠抽屉架构。
 *    清晰分为四大折叠层：
 *      - 🗺️ 场景切换（背景地图、房屋模板、昼夜时段）
 *      - 👥 NPC 信息（村民社交、好感/亲密度双轨条、快速互动）
 *      - 🌍 世界环境状态（时间天气、金币财产、同步状态、资产挂画/BGM）
 *      - 🛋️ 室内布置/视口控制（进出室内、房间切换与扩建、家具布置、专属小人/宠物设置）
 *    彻底消除 z-index 与绝对定位重叠，支持侧栏抽屉整体折叠/展开。
 *
 * 2. 左上角功能面板：
 *    全功能打通，无任何死按钮：
 *      - 🖥️ 全屏（F11 浏览器原生全屏联动）
 *      - 🧭 探险任务（探险日志/玩法面板）
 *      - 🎒 玩家背包（弹窗查看随身物资）
 *      - 📜 任务日志（任务目标与进度）
 *      - ⚙️ 系统设置（时段音效与画质设置）
 *      - ⚡ 休闲玩法（垂钓/耕种/野炊/萌宠/观星/留声机/回响/舆图/AI画像快捷通道）
 *      - 🔙 返回私人空间
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { LineIcon } from '../../components/ui/LineIcon';
import { CabinNiIcon } from './CabinNiIcon';
import {
  CABIN_BACKGROUNDS,
  type CabinBackgroundId,
} from '../cabin/cabinConfig';
import { MAX_INTIMACY } from '../cabin/gameplay/balance';
import { MAX_HEARTS, type LifeSnapshot } from '../cabin/gameplay/lifeApi';
import type { HudModalType } from '../cabin/hud/CabinHud';

/** 归一化到 0–100 用于进度条宽度。null 与 NaN 一律走「未回传」分支。 */
function norm(value: number | null | undefined, max: number): number | null {
  if (value === null || value === undefined || typeof value !== 'number' || Number.isNaN(value)) {
    return null;
  }
  return Math.max(0, Math.min(100, (value / max) * 100));
}

export interface CabinNpcRow {
  id: string;
  name: string;
  role: string;
  hearts: number;
  heartsDisplay: string;
  intimacy: number | null;
  place: string;
  activity: string;
  marker: string;
}

export type CabinTabId = 'scene' | 'npc' | 'world' | 'decorate';

export interface CabinHudOverlayProps {
  view: 'outdoor' | 'indoor';
  editMode: boolean;
  currentTheme: CabinBackgroundId;
  timeOfDay: string;
  npcs: CabinNpcRow[];
  loading: boolean;
  error: string | null;
  notice: string | null;
  onSelectTheme: (id: CabinBackgroundId) => void;
  onSelectTimeOfDay: (t: 'dawn' | 'day' | 'dusk' | 'night') => void;
  onInteract: (npc: CabinNpcRow) => void;
  onToggleDecorate: () => void;
  onEnterIndoor: () => void;
  onExitIndoor: () => void;
  onOpenGameplay: () => void;
  gameplayOpen: boolean;
  children?: ReactNode;

  /* T1.3 快捷功能联动扩展 */
  onOpenModal?: (modal: HudModalType) => void;
  onNavigateBack?: () => void;
  coins?: number;
  dayText?: string;
  dialogueSource?: string;

  /* 四大选项卡槽位（支持分流渲染或由 children 统筹） */
  sceneSlot?: ReactNode;
  worldSlot?: ReactNode;
  decorateSlot?: ReactNode;
}

const TIME_SLOTS = [
  ['dawn', '清晨'],
  ['day', '白天'],
  ['dusk', '黄昏'],
  ['night', '夜晚'],
] as const;

/** 双轨进度条 */
function DualTrack({ npc }: { npc: CabinNpcRow }) {
  const heartPct = norm(npc.hearts, MAX_HEARTS);
  const intimacyPct = norm(npc.intimacy, MAX_INTIMACY);

  return (
    <>
      <span className="cabin-ni-track">
        <span>好感</span>
        <span className="cabin-ni-track-bar">
          <span
            className="cabin-ni-track-fill"
            style={{ width: `${heartPct ?? 0}%` }}
            data-testid={`cabin-ni-hearts-bar-${npc.id}`}
          />
        </span>
        <span className="cabin-ni-track-val">
          {typeof npc.hearts === 'number'
            ? `${npc.hearts}/${MAX_HEARTS}`
            : '未回传'}
        </span>
      </span>

      <span className="cabin-ni-track">
        <span>亲密</span>
        <span className="cabin-ni-track-bar">
          <span
            className="cabin-ni-track-fill cabin-ni-track-fill--b"
            style={{ width: `${intimacyPct ?? 0}%` }}
            data-testid={`cabin-ni-intimacy-bar-${npc.id}`}
          />
        </span>
        <span className="cabin-ni-track-val">
          {intimacyPct === null ? '未回传' : `${Math.round(intimacyPct)}/100`}
        </span>
      </span>
    </>
  );
}

/** NPC 交互条 */
function NpcRow({ npc, onInteract }: { npc: CabinNpcRow; onInteract: (n: CabinNpcRow) => void }) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  return (
    <li style={{ listStyle: 'none' }}>
      <button
        type="button"
        className={open ? 'cabin-ni-npc is-active' : 'cabin-ni-npc'}
        aria-expanded={open}
        data-testid={`cabin-ni-npc-${npc.id}`}
        onClick={() => {
          toggle();
          onInteract(npc);
        }}
      >
        <span className="cabin-ni-npc-name">
          <LineIcon name="user" size={16} />
          <span>{npc.name}</span>
          <span className="ui-badge ui-badge--neutral">{npc.role}</span>
        </span>
        <span className="cabin-ni-npc-act">
          {npc.place ? `${npc.place}${npc.activity ? ` · ${npc.activity}` : ''}` : '位置未回传'}
        </span>
        <DualTrack npc={npc} />
      </button>
    </li>
  );
}

export function CabinHudOverlay(props: CabinHudOverlayProps) {
  const {
    view,
    editMode,
    currentTheme,
    timeOfDay,
    npcs,
    loading,
    error,
    notice,
    onSelectTheme,
    onSelectTimeOfDay,
    onInteract,
    onToggleDecorate,
    onEnterIndoor,
    onExitIndoor,
    onOpenGameplay,
    gameplayOpen,
    children,
    onOpenModal,
    onNavigateBack,
    coins = 0,
    dayText = '',
    sceneSlot,
    worldSlot,
    decorateSlot,
  } = props;

  // 时段区折叠态
  const [timesOpen, setTimesOpen] = useState(false);

  // 选项卡状态：默认场景，当进入布置模式或进屋时自动切换到布置页
  const [activeTab, setActiveTab] = useState<CabinTabId>('scene');
  const [drawerOpen, setDrawerOpen] = useState(true);

  // 快捷菜单下拉态
  const [cozyMenuOpen, setCozyMenuOpen] = useState(false);

  // 全屏状态
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const handleFsChange = () => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };
    document.addEventListener('fullscreenchange', handleFsChange);
    return () => document.removeEventListener('fullscreenchange', handleFsChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (typeof document === 'undefined') return;
    try {
      if (!document.fullscreenElement) {
        if (document.documentElement.requestFullscreen) {
          void document.documentElement.requestFullscreen().catch(() => {});
        }
        setIsFullscreen(true);
      } else {
        if (document.exitFullscreen) {
          void document.exitFullscreen().catch(() => {});
        }
        setIsFullscreen(false);
      }
    } catch {
      setIsFullscreen((v) => !v);
    }
  }, []);

  // 当进入室内或者进入编辑模式时，自适应切到布置卡片
  useEffect(() => {
    if (editMode || view === 'indoor') {
      setActiveTab('decorate');
    }
  }, [editMode, view]);

  const handleOpenModal = useCallback(
    (modal: HudModalType) => {
      setCozyMenuOpen(false);
      onOpenModal?.(modal);
    },
    [onOpenModal],
  );

  return (
    <>
      {/* ---------------- 左上角功能面板（打通全部联动） ---------------- */}
      <nav
        className="cabin-ni-keys"
        data-testid="cabin-ni-keys"
        aria-label="快捷功能与操作"
      >
        {/* 全屏切换 */}
        <button
          type="button"
          className={`cabin-fn-btn ${isFullscreen ? 'is-active' : ''}`}
          data-testid="cabin-fn-fullscreen"
          title="F11 切换全屏沉浸模式"
          onClick={toggleFullscreen}
        >
          <CabinNiIcon name="maximize" size={14} />
          <span>{isFullscreen ? '窗口化' : '全屏'}</span>
          <span className="ui-kbd" aria-hidden="true">F11</span>
        </button>

        {/* 探险与任务 */}
        <button
          type="button"
          className={`cabin-fn-btn ${gameplayOpen ? 'is-active' : ''}`}
          data-testid="cabin-fn-gameplay"
          title="探险任务日志与大世界探索"
          onClick={onOpenGameplay}
        >
          <LineIcon name="target" size={14} />
          <span>探险任务</span>
        </button>

        {/* 背包 */}
        <button
          type="button"
          className="cabin-fn-btn"
          data-testid="cabin-fn-backpack"
          title="查看玩家背包物资"
          onClick={() => handleOpenModal('backpack')}
        >
          <span role="img" aria-label="背包">🎒</span>
          <span>背包</span>
        </button>

        {/* 任务日志 */}
        <button
          type="button"
          className="cabin-fn-btn"
          data-testid="cabin-fn-quest"
          title="查看村民委托与任务日志"
          onClick={() => handleOpenModal('quest')}
        >
          <span role="img" aria-label="任务">📜</span>
          <span>日志</span>
        </button>

        {/* 休闲玩法 / 快捷操作下拉 */}
        <div className="cabin-fn-menu-wrap">
          <button
            type="button"
            className={`cabin-fn-btn ${cozyMenuOpen ? 'is-active' : ''}`}
            data-testid="cabin-fn-cozy-menu"
            aria-expanded={cozyMenuOpen}
            title="快捷展开农场耕种、垂钓、野炊、观星等休闲活动"
            onClick={() => setCozyMenuOpen((v) => !v)}
          >
            <span role="img" aria-label="玩法">⚡</span>
            <span>休闲玩法 ▾</span>
          </button>

          {cozyMenuOpen && (
            <div
              className="cabin-fn-dropdown"
              data-testid="cabin-fn-dropdown"
              role="menu"
            >
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-fishing"
                onClick={() => handleOpenModal('fishing')}
              >
                <span>🎣</span>
                <span>碧水垂钓</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-gardening"
                onClick={() => handleOpenModal('gardening')}
              >
                <span>🌱</span>
                <span>庭院耕种</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-cooking"
                onClick={() => handleOpenModal('cooking')}
              >
                <span>🍳</span>
                <span>林间野炊</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-pet"
                onClick={() => handleOpenModal('pet')}
              >
                <span>🐾</span>
                <span>萌宠互动</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-stargazing"
                onClick={() => handleOpenModal('stargazing')}
              >
                <span>🔭</span>
                <span>星空祈愿</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-soundscape"
                onClick={() => handleOpenModal('soundscape')}
              >
                <span>📻</span>
                <span>森林留声</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-echo"
                onClick={() => handleOpenModal('echo')}
              >
                <span>🌊</span>
                <span>心境回响</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-worldmap"
                onClick={() => handleOpenModal('worldmap')}
              >
                <span>🗺️</span>
                <span>手绘舆图</span>
              </button>
              <button
                type="button"
                className="cabin-fn-dropdown-item"
                data-testid="cabin-fn-item-avatar"
                onClick={() => handleOpenModal('avatar')}
              >
                <span>🎭</span>
                <span>AI画像</span>
              </button>
            </div>
          )}
        </div>

        {/* 系统设置 */}
        <button
          type="button"
          className="cabin-fn-btn"
          data-testid="cabin-fn-settings"
          title="游戏系统与时段设置"
          onClick={() => handleOpenModal('settings')}
        >
          <LineIcon name="settings" size={14} />
          <span>设置</span>
        </button>

        {/* 返回私人空间 */}
        {onNavigateBack && (
          <button
            type="button"
            className="cabin-fn-btn cabin-fn-btn--back"
            data-testid="cabin-fn-back"
            title="退出小屋返回私人空间"
            onClick={onNavigateBack}
          >
            <CabinNiIcon name="arrowLeft" size={14} />
            <span>返回空间</span>
          </button>
        )}
      </nav>

      {/* ---------------- 右侧 HUD 抽屉栏（重构选项卡层级） ---------------- */}
      <aside
        className={`cabin-ni-side ${drawerOpen ? '' : 'is-collapsed'}`}
        data-testid="cabin-ni-side"
        aria-label="小屋 HUD 控制台"
      >
        {/* 抽屉顶部导航栏：Tab 切换 + 折叠抽屉按钮 */}
        <div className="cabin-drawer-header">
          {drawerOpen ? (
            <div className="cabin-drawer-tabs" role="tablist" aria-label="HUD 栏目切换">
              <button
                type="button"
                role="tab"
                id="cabin-tab-btn-scene"
                aria-selected={activeTab === 'scene'}
                aria-controls="cabin-panel-scene"
                className={`cabin-drawer-tab ${activeTab === 'scene' ? 'is-active' : ''}`}
                onClick={() => setActiveTab('scene')}
                data-testid="cabin-tab-scene"
                title="场景切换与房屋模板"
              >
                <LineIcon name="layers" size={15} />
                <span>场景</span>
              </button>

              <button
                type="button"
                role="tab"
                id="cabin-tab-btn-npc"
                aria-selected={activeTab === 'npc'}
                aria-controls="cabin-panel-npc"
                className={`cabin-drawer-tab ${activeTab === 'npc' ? 'is-active' : ''}`}
                onClick={() => setActiveTab('npc')}
                data-testid="cabin-tab-npc"
                title="NPC 社交与状态"
              >
                <LineIcon name="user" size={15} />
                <span>NPC ({npcs.length})</span>
              </button>

              <button
                type="button"
                role="tab"
                id="cabin-tab-btn-world"
                aria-selected={activeTab === 'world'}
                aria-controls="cabin-panel-world"
                className={`cabin-drawer-tab ${activeTab === 'world' ? 'is-active' : ''}`}
                onClick={() => setActiveTab('world')}
                data-testid="cabin-tab-world"
                title="世界环境状态与多媒体"
              >
                <CabinNiIcon name="map" size={15} />
                <span>环境</span>
              </button>

              <button
                type="button"
                role="tab"
                id="cabin-tab-btn-decorate"
                aria-selected={activeTab === 'decorate'}
                aria-controls="cabin-panel-decorate"
                className={`cabin-drawer-tab ${activeTab === 'decorate' ? 'is-active' : ''}`}
                onClick={() => setActiveTab('decorate')}
                data-testid="cabin-tab-decorate"
                title="室内布置与视口控制"
              >
                <LineIcon name="target" size={15} />
                <span>布置</span>
              </button>
            </div>
          ) : (
            <span className="cabin-drawer-collapsed-title">HUD</span>
          )}

          <button
            type="button"
            className="cabin-drawer-toggle"
            aria-label={drawerOpen ? '收起控制台' : '展开控制台'}
            title={drawerOpen ? '收起控制台' : '展开控制台'}
            data-testid="cabin-drawer-toggle"
            onClick={() => setDrawerOpen((v) => !v)}
          >
            <CabinNiIcon name={drawerOpen ? 'arrowRight' : 'arrowLeft'} size={15} />
          </button>
        </div>

        {/* 折叠态快捷按钮列 */}
        {!drawerOpen && (
          <div className="cabin-drawer-collapsed-nav">
            <button
              type="button"
              className="cabin-drawer-mini-btn"
              title="展开场景切换"
              onClick={() => {
                setActiveTab('scene');
                setDrawerOpen(true);
              }}
            >
              <LineIcon name="layers" size={16} />
            </button>
            <button
              type="button"
              className="cabin-drawer-mini-btn"
              title="展开 NPC 信息"
              onClick={() => {
                setActiveTab('npc');
                setDrawerOpen(true);
              }}
            >
              <LineIcon name="user" size={16} />
            </button>
            <button
              type="button"
              className="cabin-drawer-mini-btn"
              title="展开世界环境"
              onClick={() => {
                setActiveTab('world');
                setDrawerOpen(true);
              }}
            >
              <CabinNiIcon name="map" size={16} />
            </button>
            <button
              type="button"
              className="cabin-drawer-mini-btn"
              title="展开室内布置"
              onClick={() => {
                setActiveTab('decorate');
                setDrawerOpen(true);
              }}
            >
              <LineIcon name="target" size={16} />
            </button>
          </div>
        )}

        {/* 抽屉内容容器：DOM 节点常驻保持测试桩稳定，hidden 切换视野 */}
        <div className={`cabin-drawer-content ${drawerOpen ? '' : 'is-hidden'}`}>
          {/* ==================== TAB 1: 场景切换 ==================== */}
          <div
            id="cabin-panel-scene"
            role="tabpanel"
            aria-labelledby="cabin-tab-btn-scene"
            className={`cabin-tab-panel ${activeTab === 'scene' ? 'is-active' : ''}`}
          >
            {/* 场景背景切换 */}
            <section className="cabin-ni-panel" aria-label="场景背景选择">
              <span className="cabin-ni-panel-hd">
                <CabinNiIcon name="map" size={16} />
                背景主题
              </span>
              <div className="cabin-ni-map" data-testid="cabin-ni-map">
                {CABIN_BACKGROUNDS.map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    className="cabin-ni-map-btn"
                    aria-pressed={currentTheme === b.id}
                    data-testid={`cabin-ni-map-${b.id}`}
                    onClick={() => onSelectTheme(b.id)}
                  >
                    <LineIcon name="layers" size={14} />
                    <span>{b.label}</span>
                  </button>
                ))}
              </div>

              {/* 昼夜时段切换 */}
              <div className="cabin-ni-time-header">
                <span className="cabin-ni-panel-hd">
                  <CabinNiIcon name="clock" size={16} />
                  时段与色温
                </span>
                <button
                  type="button"
                  className="cabin-ni-collapse"
                  aria-expanded={timesOpen}
                  data-testid="cabin-ni-times-toggle"
                  onClick={() => setTimesOpen((v) => !v)}
                >
                  <LineIcon name="sliders" size={14} />
                  <span>{timesOpen ? '收起时段' : '展开时段'}</span>
                </button>
              </div>

              {timesOpen && (
                <div className="cabin-ni-swatches">
                  {TIME_SLOTS.map(([t, label]) => (
                    <button
                      key={t}
                      type="button"
                      className="cabin-ni-swatch"
                      aria-pressed={timeOfDay === t}
                      data-testid={`cabin-ni-time-${t}`}
                      onClick={() => onSelectTimeOfDay(t)}
                    >
                      <LineIcon name={t === 'night' ? 'pause' : 'sparkles'} size={14} />
                      <span>{label}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>

            {/* 房屋模板槽位（由 CabinPage 传入） */}
            {sceneSlot}
          </div>

          {/* ==================== TAB 2: NPC 信息 ==================== */}
          <div
            id="cabin-panel-npc"
            role="tabpanel"
            aria-labelledby="cabin-tab-btn-npc"
            className={`cabin-tab-panel ${activeTab === 'npc' ? 'is-active' : ''}`}
          >
            <section className="cabin-ni-panel" aria-label="NPC 村民社交">
              <span className="cabin-ni-panel-hd">
                <LineIcon name="user" size={16} />
                村民社交（{npcs.length}）
              </span>
              {npcs.length === 0 ? (
                <span className="cabin-ni-evi-empty">
                  <LineIcon name="info" size={16} />
                  尚未回传 NPC 数据
                </span>
              ) : (
                <ul
                  style={{
                    listStyle: 'none',
                    margin: 0,
                    padding: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 'var(--ui-s-2)',
                  }}
                >
                  {npcs.map((n) => (
                    <NpcRow key={n.id} npc={n} onInteract={onInteract} />
                  ))}
                </ul>
              )}
            </section>
          </div>

          {/* ==================== TAB 3: 世界环境状态 ==================== */}
          <div
            id="cabin-panel-world"
            role="tabpanel"
            aria-labelledby="cabin-tab-btn-world"
            className={`cabin-tab-panel ${activeTab === 'world' ? 'is-active' : ''}`}
          >
            {/* 状态与同步提示 */}
            {(error || notice || loading) && (
              <div
                className="cabin-ni-glass cabin-ni-panel cabin-ni-status-card"
                role={error ? 'alert' : 'status'}
                data-testid="cabin-ni-status"
              >
                <span className="cabin-ni-panel-hd">
                  <LineIcon name={error ? 'alert' : 'info'} size={16} />
                  {error ? '数据加载失败' : notice ? '提示' : '同步中'}
                </span>
                <span className="cabin-ni-npc-act">{error ?? notice ?? '正在与后端同步…'}</span>
              </div>
            )}

            {/* 环境快照卡片 */}
            <section className="cabin-ni-panel" aria-label="环境与时间">
              <span className="cabin-ni-panel-hd">
                <CabinNiIcon name="clock" size={16} />
                世界状态
              </span>
              <div className="cabin-world-info-row">
                <span className="cabin-world-badge">
                  {timeOfDay === 'night' ? '🌙 夜晚' : timeOfDay === 'dusk' ? '🌅 黄昏' : '☀️ 白天'}
                </span>
                <span className="cabin-world-text">{dayText || '第1天 上午 晴'}</span>
              </div>
              <div className="cabin-world-info-row">
                <span className="cabin-world-badge">🪙 金币</span>
                <span className="cabin-world-val">{coins.toLocaleString()}</span>
              </div>
            </section>

            {/* 世界多媒体与说明（挂画 + BGM + 台词来源） */}
            {worldSlot}
          </div>

          {/* ==================== TAB 4: 室内布置 / 视口控制 ==================== */}
          <div
            id="cabin-panel-decorate"
            role="tabpanel"
            aria-labelledby="cabin-tab-btn-decorate"
            className={`cabin-tab-panel ${activeTab === 'decorate' ? 'is-active' : ''}`}
          >
            {/* 视图控制大按钮 */}
            <section className="cabin-ni-panel" aria-label="场景视口控制">
              <span className="cabin-ni-panel-hd">
                <LineIcon name="target" size={16} />
                视口切换
              </span>
              <div className="cabin-view-actions">
                {view === 'outdoor' ? (
                  <button
                    type="button"
                    className="cabin-ni-map-btn cabin-ni-btn-highlight"
                    data-testid="cabin-ni-enter-indoor"
                    onClick={onEnterIndoor}
                  >
                    <CabinNiIcon name="door" size={16} />
                    进屋布置
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className="cabin-ni-map-btn"
                      data-testid="cabin-ni-exit-indoor"
                      onClick={onExitIndoor}
                    >
                      <CabinNiIcon name="door" size={16} />
                      出门回院子
                    </button>
                    <button
                      type="button"
                      className="cabin-ni-map-btn cabin-ni-btn-highlight"
                      aria-pressed={editMode}
                      data-testid="cabin-ni-toggle-edit"
                      onClick={onToggleDecorate}
                    >
                      <LineIcon name="layers" size={16} />
                      {editMode ? '退出布置' : '开始布置'}
                    </button>
                  </>
                )}

                <button
                  type="button"
                  className="cabin-ni-map-btn"
                  aria-pressed={gameplayOpen}
                  data-testid="cabin-ni-gameplay"
                  onClick={onOpenGameplay}
                >
                  <LineIcon name="target" size={16} />
                  {gameplayOpen ? '收起探险' : '探险与任务'}
                </button>
              </div>
            </section>

            {/* 室内布置与小人/宠物设置槽位 */}
            {decorateSlot}
          </div>

          {/* 附加 children 保底承载 */}
          {children && (
            <div className="cabin-drawer-children-wrap">
              {children}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

/**
 * `/game` 全屏提示
 */
export function useGameFullscreenHint(active: boolean): { hint: string } {
  const [hint, setHint] = useState('');

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F11') setHint('F11 全屏由浏览器接管；按 Esc 可退出独立形态回工作台。');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active]);

  return { hint };
}

/** 由 LifeSnapshot 派生 HUD 用的 NPC 行（只做形状转换，不造数据）。 */
export function npcRowsFromSnapshot(snapshot: LifeSnapshot | null): CabinNpcRow[] {
  if (!snapshot?.npcs) return [];
  return snapshot.npcs.map((n) => ({
    id: n.id,
    name: n.name,
    role: n.role,
    hearts: typeof n.hearts === 'number' ? n.hearts : 0,
    heartsDisplay: n.hearts_display,
    intimacy: null,
    place: n.place,
    activity: n.activity,
    marker: n.marker,
  }));
}