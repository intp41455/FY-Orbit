import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { WbIcon } from './WbIcon';

/**
 * A 布局骨架（P1-02 原版 → 包 A UI 重构版）。
 *
 * 结构：中部「左：文件与任务 + 中：编辑与变更 + 右：验证与放行」可拖拽三栏，
 * 底部为可折叠、可拖拽调高的终端坞（置底停靠）。
 *
 * 与旧版的三处关键差异（08-包A-体验规范 §12.3 / §14）：
 *  1. 左区宽度不再写内联 `width: %`（窄屏会横向溢出），改由 CSS 变量
 *     `--wb-left-pct` 驱动，断点行为交给 workbench.css 的 media query。
 *  2. 新增中区 `wb-zone-middle`（原版只有左右两栏，中区内容被塞在右栏里）。
 *  3. 新增 `sections` 持久化字段（底部四区段的展开态）。**只加字段**，
 *     `leftPct` / `terminalHeight` / `terminalCollapsed` 三键名与语义一字不动，
 *     否则 `layout-a.spec.ts` 的路由往返断言会读到旧值。
 *
 * 冻结契约（不许改名/删除）：
 *   data-testid = wb-layout-a / wb-zone-left / wb-zone-right /
 *                 wb-split-handle / wb-terminal-dock / wb-terminal-resize /
 *                 wb-terminal-toggle
 *   行为 = 拖拽改宽高、折叠不渲染 resize 分隔条、状态写 sessionStorage。
 */

interface Props {
  left: ReactNode;
  middle: ReactNode;
  right: ReactNode;
  bottom: ReactNode;
  /** 受控布局状态：由 WorkbenchPage 持有，使底部四区段与三区/坞共享同一份存储。 */
  state: LayoutState;
  onStateChange: (updater: (s: LayoutState) => LayoutState) => void;
}

export interface LayoutState {
  leftPct: number;
  terminalHeight: number;
  terminalCollapsed: boolean;
  /** 底部四区段展开态（差异审查 / 文档树 / 提交图 / 备份与回滚）。 */
  sections: [boolean, boolean, boolean, boolean];
}

const STORAGE_KEY = 'fy.workbench.layout-a';
const DEFAULT_SECTIONS: LayoutState['sections'] = [true, true, true, true];
const DEFAULTS: LayoutState = {
  leftPct: 22,
  terminalHeight: 260,
  terminalCollapsed: false,
  sections: DEFAULT_SECTIONS,
};

const MIN_LEFT_PCT = 14;
const MAX_LEFT_PCT = 44;
const MIN_TERMINAL_HEIGHT = 120;
const MAX_TERMINAL_HEIGHT = 520;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function normalizeSections(raw: unknown): LayoutState['sections'] {
  if (!Array.isArray(raw) || raw.length !== 4) return DEFAULT_SECTIONS;
  return [0, 1, 2, 3].map((i) => Boolean(raw[i])) as LayoutState['sections'];
}

export function readLayoutState(): LayoutState {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<LayoutState>;
    return {
      leftPct: clamp(Number(parsed.leftPct) || DEFAULTS.leftPct, MIN_LEFT_PCT, MAX_LEFT_PCT),
      terminalHeight: clamp(
        Number(parsed.terminalHeight) || DEFAULTS.terminalHeight,
        MIN_TERMINAL_HEIGHT,
        MAX_TERMINAL_HEIGHT,
      ),
      terminalCollapsed: Boolean(parsed.terminalCollapsed),
      sections: normalizeSections(parsed.sections),
    };
  } catch {
    return DEFAULTS;
  }
}

export function writeLayoutState(s: LayoutState): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* private mode / quota — 布局状态仅退化为会话内不持久 */
  }
}

/**
 * 三区高度依赖坞的展开态：坞折叠时视口余量要让给三区。布局状态由
 * WorkbenchPage 受控持有（它同时管底部四区段的展开态），避免出现两个写入者
 * 互相覆盖同一份 `fy.workbench.layout-a`。
 */
export function WorkbenchLayout({ left, middle, right, bottom, state, onStateChange }: Props) {
  const [dragging, setDragging] = useState<'split' | 'terminal' | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);
  // 拖拽起点快照：以增量方式计算新尺寸，避免容器 rect 抖动带来的跳变。
  const dragStart = useRef({ x: 0, y: 0, leftPct: 22, terminalHeight: 260 });

  const setState = onStateChange;

  const startSplitDrag = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      dragStart.current = {
        x: e.clientX,
        y: e.clientY,
        leftPct: state.leftPct,
        terminalHeight: state.terminalHeight,
      };
      setDragging('split');
    },
    [state.leftPct, state.terminalHeight],
  );

  const startTerminalDrag = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      dragStart.current = {
        x: e.clientX,
        y: e.clientY,
        leftPct: state.leftPct,
        terminalHeight: state.terminalHeight,
      };
      setDragging('terminal');
    },
    [state.leftPct, state.terminalHeight],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      if (dragging === 'split' && mainRef.current) {
        const rect = mainRef.current.getBoundingClientRect();
        if (rect.width <= 0) return;
        const dx = e.clientX - dragStart.current.x;
        const deltaPct = (dx / rect.width) * 100;
        setState((s) => ({
          ...s,
          leftPct: clamp(dragStart.current.leftPct + deltaPct, MIN_LEFT_PCT, MAX_LEFT_PCT),
        }));
      } else if (dragging === 'terminal') {
        // 向上拖动 → 终端变高。
        const dy = dragStart.current.y - e.clientY;
        setState((s) => ({
          ...s,
          terminalHeight: clamp(
            dragStart.current.terminalHeight + dy,
            MIN_TERMINAL_HEIGHT,
            MAX_TERMINAL_HEIGHT,
          ),
        }));
      }
    };
    const onUp = () => setDragging(null);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [dragging, setState]);

  const { leftPct, terminalHeight, terminalCollapsed } = state;

  return (
    <section
      className={`wb-layout${dragging ? ' wb-layout-dragging' : ''}`}
      data-testid="wb-layout-a"
      data-dragging={dragging ?? undefined}
      style={{ ['--wb-left-pct' as string]: String(leftPct) }}
    >
      <div className="wb-main" ref={mainRef}>
        <div className="wb-zone-left" data-testid="wb-zone-left" role="region" aria-label="文件与任务">
          {left}
        </div>
        <div
          className="wb-split-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整左侧分区宽度"
          data-testid="wb-split-handle"
          onPointerDown={startSplitDrag}
        />
        <div className="wb-zone-middle" role="region" aria-label="编辑与变更">
          {middle}
        </div>
        <div className="wb-zone-right" data-testid="wb-zone-right" role="complementary" aria-label="验证与放行">
          {right}
        </div>
      </div>

      {!terminalCollapsed && (
        <div
          className="wb-terminal-resize"
          role="separator"
          aria-orientation="horizontal"
          aria-label="调整终端面板高度"
          data-testid="wb-terminal-resize"
          onPointerDown={startTerminalDrag}
        />
      )}

      <div
        className={`wb-terminal-dock${terminalCollapsed ? ' collapsed' : ''}`}
        data-testid="wb-terminal-dock"
        data-collapsed={terminalCollapsed || undefined}
        role="region"
        aria-label="终端坞"
      >
        <div className="wb-dock-bar">
          <strong className="dock-title">
            <WbIcon name="chevronUp" size={16} />
            终端
          </strong>
          <span className="muted small">停靠底部</span>
          <span className="dock-spacer" />
          <button
            type="button"
            className="ui-btn ui-btn--sm ui-btn--ghost"
            data-testid="wb-terminal-toggle"
            aria-expanded={!terminalCollapsed}
            onClick={() => setState((s) => ({ ...s, terminalCollapsed: !s.terminalCollapsed }))}
          >
            {terminalCollapsed ? '展开终端 ▲' : '折叠终端 ▼'}
          </button>
        </div>
        {!terminalCollapsed && (
          <div className="wb-terminal-body" style={{ height: terminalHeight }}>
            {bottom}
          </div>
        )}
      </div>
    </section>
  );
}
