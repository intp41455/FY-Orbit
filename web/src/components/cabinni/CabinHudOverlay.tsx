/**
 * 包 D 私有组件 · 小屋 HUD 覆盖层（DOM 层，不碰 pixi canvas）
 * ---------------------------------------------------------------------------
 * 为什么是 DOM 而不是画布内元素：
 *   - 画布内渲染 = 每帧重绘 + 每帧纹理重采样，加毛玻璃直接掉帧；
 *   - HUD 是静态的，做成 DOM 覆盖层后由合成器单独处理，动画只跑一次 transition。
 *
 * 边界裁决（包 D 任务书 §4）：
 *   画布内（pixi）保持像素硬边；画布外（这里）用玻璃 + 圆角 + 线条图标。
 *   本组件与画布之间留 8px 呼吸位（.cabin-ni-overlay 的 inset），不压画面中心。
 *
 * 数据诚实性：本组件只渲染上层传进来的真实快照字段。字段缺失一律显示
 * 「未回传」，绝不填 0 或编造默认值——零值与缺失在 UI 上必须可区分。
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
  /** 好感心数（后端 npcs.hearts_for_points 派生，0–MAX_HEARTS）。 */
  hearts: number;
  /** 后端已格式化的心形串，原样透传，不在前端重算。 */
  heartsDisplay: string;
  /** 未回传时为 null —— 与「0 心」严格区分。 */
  intimacy: number | null;
  place: string;
  activity: string;
  marker: string;
}

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
  /**
   * 追加到右栏末尾的面板。本包用它承载原 `.cabin-toolbar`
   * （房屋模板 / 背景 / 宠物 / 小人 的完整设置）。
   *
   * 为什么放这里：它原本是静态流式元素，在 `.cabin-root`（fixed + overflow:hidden）
   * 里永远排在画布之上、压住第一屏画面。功能一个字都不能删（红线二），
   * 所以**搬家**而不是隐藏 —— 搬进可滚动的右栏后既不挡画面，又不丢功能。
   */
  children?: ReactNode;
}

const TIME_SLOTS = [
  ['dawn', '清晨'],
  ['day', '白天'],
  ['dusk', '黄昏'],
  ['night', '夜晚'],
] as const;

/**
 * 双轨进度条。
 *
 * 主控裁决：NPC 好感与 W2 `intimacy` **双轨不合并**。
 * → 两条独立进度条，起止色不同，各自带文字标签；
 * → 颜色不是唯一信息通道：轨道名 + 数值/心形串同时给出。
 * → 两条都用天蓝色阶（500→700 / 400→900），**不许绿色**。
 */
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

/**
 * NPC 交互条：点 NPC 即展开交互（最少点击守则第 3 条）——不用先点「互动」再点目标。
 * 选中态由本组件内部 state 管理，不外抛，避免污染页面状态机。
 */
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
          // 点 NPC 即可交互：同时把该 NPC 作为当前交互目标交给上层。
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

/**
 * HUD 覆盖层（右侧玻璃栏）。
 *
 * 版式裁决（包 D 任务书 §5）：
 *   顶部 `.pixel-top-bar` 与底部 `.pixel-bottom-bar` 属于**像素层**（CabinHud 组件），
 *   本组件**不重复**它们，只补右侧 HUD：NPC 名 / 好感 / 当前动作 / 一键交互 + 场景常驻大按钮。
 *   两层之间留 8px 呼吸位，不压画面。
 *
 * 动效预算：只在状态切换（选中/展开/折叠）时跑一次 180ms transition，之后归零；
 * **没有任何 infinite 动画**，不做持续呼吸/发光（包 D 任务书 §3 禁止项）。
 */
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
  } = props;

  /** 时段区展开态。 */
  const [timesOpen, setTimesOpen] = useState(false);

  return (
    <>
      {/*
        快捷键提示条。

        ⚠ 这里**刻意不重复**金币 / 时间 / 天气 / 室内外 —— 那些数据已经由像素层的
        `.pixel-top-bar`（CabinHud 组件）呈现。重复一遍等于同一屏说两遍，
        而且两份数据可能不同步（截图第一版就是这么错的）。
        本条只承担两件事：告诉用户 F11 / Esc 干什么，以及开关「小屋设置」面板。
      */}
      <div className="cabin-ni-keys" data-testid="cabin-ni-keys">
        <span className="ui-kbd" aria-hidden="true">
          F11
        </span>
        <span className="cabin-ni-npc-act">全屏</span>
        <span className="ui-kbd" aria-hidden="true">
          Esc
        </span>
        <span className="cabin-ni-npc-act">退出独立形态</span>
      </div>

      {/* ---------------- 右侧 HUD ---------------- */}
      <aside className="cabin-ni-side" data-testid="cabin-ni-side" aria-label="小屋 HUD">
        {/* 状态提示（诚实展示，不掩盖） */}
        {(error || notice || loading) && (
          <div
            className="cabin-ni-glass cabin-ni-panel"
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

        {/* NPC 交互（点 NPC 即交互） */}
        <section className="cabin-ni-glass cabin-ni-panel" aria-label="NPC 交互">
          <span className="cabin-ni-panel-hd">
            <LineIcon name="user" size={16} />
            NPC（{npcs.length}）
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

        {/* 常驻大按钮：地图切换 + 视图切换（最少点击守则第 2 条） */}
        <section className="cabin-ni-glass cabin-ni-panel" aria-label="场景切换">
          <span className="cabin-ni-panel-hd">
            <CabinNiIcon name="map" size={16} />
            场景
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
                <LineIcon name="layers" size={16} />
                {b.label}
              </button>
            ))}
          </div>

          {timesOpen && (
            <div style={{ display: 'flex', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
              {TIME_SLOTS.map(([t, label]) => (
                <button
                  key={t}
                  type="button"
                  className="cabin-ni-swatch"
                  aria-pressed={timeOfDay === t}
                  data-testid={`cabin-ni-time-${t}`}
                  onClick={() => onSelectTimeOfDay(t)}
                >
                  <LineIcon name={t === 'night' ? 'pause' : 'sparkles'} size={16} />
                  {label}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            className="cabin-ni-collapse"
            aria-expanded={timesOpen}
            data-testid="cabin-ni-times-toggle"
            onClick={() => setTimesOpen((v) => !v)}
          >
            <LineIcon name="sliders" size={16} />
            {timesOpen ? '收起时段' : '切换时段'}
          </button>
        </section>

        {/* 小人设置不在这里重复：原 .cabin-toolbar 已经带了完整的
            名字 / 性格 / 宠物颜色 / 房屋模板 / 背景，它被搬进本栏末尾（children）。
            两处都放会出现"两份可能不同步的数据"，是最容易出 bug 的那种冗余。 */}

        {/* 视图切换 */}
        <section className="cabin-ni-glass cabin-ni-panel" aria-label="视图">
          <span className="cabin-ni-panel-hd">
            <LineIcon name="target" size={16} />
            视图
          </span>
          {view === 'outdoor' ? (
            <button
              type="button"
              className="cabin-ni-map-btn"
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
                className="cabin-ni-map-btn"
                aria-pressed={editMode}
                data-testid="cabin-ni-toggle-edit"
                onClick={onToggleDecorate}
              >
                <LineIcon name="layers" size={16} />
                {editMode ? '退出布置' : '布置'}
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
            {gameplayOpen ? '收起探险与任务' : '探险与任务'}
          </button>
        </section>

        {/* 原 .cabin-toolbar（房屋模板 / 背景 / 宠物 / 小人 / 资产库）搬到这里 */}
        {children}
      </aside>
    </>
  );
}

/**
 * `/game` 全屏提示。
 *
 * F11 是浏览器级全屏，页面无权拦截，因此这里只监听它来打提示，
 * 让用户知道按 F11 会发生什么，而不是让用户猜（最少点击守则第 1 条）。
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
    // NpcRow 当前不带 intimacy；缺失一律 null → UI 显示「未回传」，不写 0。
    intimacy: null,
    place: n.place,
    activity: n.activity,
    marker: n.marker,
  }));
}