import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * A 布局骨架（P1-02）：中部「左：代码/文件区 + 右：预览窗」可拖拽分隔的双栏，
 * 底部为可折叠、可拖拽调高的终端面板（置底停靠）。
 *
 * 纯前端 state 驱动：拖拽只改分区宽度/高度，不触碰任何后端。
 * 布局状态写入 sessionStorage，会话内（含 SPA 路由往返）不丢失。
 */

interface Props {
  left: ReactNode;
  right: ReactNode;
  bottom: ReactNode;
}

interface LayoutState {
  leftPct: number;
  terminalHeight: number;
  terminalCollapsed: boolean;
}

const STORAGE_KEY = 'fy.workbench.layout-a';
const DEFAULTS: LayoutState = { leftPct: 55, terminalHeight: 260, terminalCollapsed: false };

const MIN_LEFT_PCT = 20;
const MAX_LEFT_PCT = 75;
const MIN_TERMINAL_HEIGHT = 120;
const MAX_TERMINAL_HEIGHT = 520;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function readStoredState(): LayoutState {
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
    };
  } catch {
    return DEFAULTS;
  }
}

function writeStoredState(s: LayoutState): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* private mode / quota — 布局状态仅退化为会话内不持久 */
  }
}

export function WorkbenchLayout({ left, right, bottom }: Props) {
  const [state, setState] = useState<LayoutState>(readStoredState);
  const [dragging, setDragging] = useState<'split' | 'terminal' | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);
  // 拖拽起点快照：以增量方式计算新尺寸，避免容器 rect 抖动带来的跳变。
  const dragStart = useRef({ x: 0, y: 0, leftPct: 55, terminalHeight: 260 });

  useEffect(() => {
    writeStoredState(state);
  }, [state]);

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
  }, [dragging]);

  const { leftPct, terminalHeight, terminalCollapsed } = state;

  return (
    <section
      className={`wb-layout${dragging ? ' wb-layout-dragging' : ''}`}
      data-testid="wb-layout-a"
      data-dragging={dragging ?? undefined}
    >
      <div className="wb-main" ref={mainRef}>
        <div className="wb-zone-left" style={{ width: `${leftPct}%` }} data-testid="wb-zone-left">
          {left}
        </div>
        <div
          className="wb-split-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整左右分区宽度"
          data-testid="wb-split-handle"
          onPointerDown={startSplitDrag}
        />
        <div className="wb-zone-right" data-testid="wb-zone-right">
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
      >
        <div className="wb-dock-bar">
          <strong className="dock-title">终端</strong>
          <span className="muted small">停靠底部</span>
          <span className="dock-spacer" />
          <button
            type="button"
            className="small"
            data-testid="wb-terminal-toggle"
            aria-expanded={!terminalCollapsed}
            onClick={() =>
              setState((s) => ({ ...s, terminalCollapsed: !s.terminalCollapsed }))
            }
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
