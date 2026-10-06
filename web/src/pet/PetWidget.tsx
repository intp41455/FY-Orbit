/**
 * W10-A · 桌面宠物浮窗组件（透明置顶小窗里的像素小人/宠物）。
 *
 * 挂载在独立路由 ``/pet``（不套主 Layout，透明窗无导航）。职责：
 *   1. canvas 逐格绘制复用自 cabin 的两帧走路矩阵，按 interval 换帧；
 *   2. 点击宠物弹气泡：台词来自本地「预生成台词池」并标注来源
 *      （butler 真模型未配置时的诚实回落，绝不假称 AI 生成）；
 *   3. 右键菜单：打开小屋 / 打开工作台 / 隐藏；
 *   4. 读真实 ``GET /api/cabin/save`` 的亲密度派生心情；低心情气泡提示去照料。
 *
 * Tauri 桥接通过 props 注入（``bridge``），不在组件里硬依赖 @tauri/api：
 * 浏览器/web 预览降级为 ``window.location`` 跳转；真实 Tauri 由 Rust 侧命令
 * 唤起主窗并隐藏浮窗。这让组件可在 jsdom 下单测纯逻辑。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchSave } from '../components/cabin/gameplay/cabinGameplayApi';
import {
  colorToCss,
  frameSize,
  frameToCells,
  moodFromIntimacy,
  PET_COLORS,
  PET_FRAMES,
  type Mood,
  pickPetLine,
} from './petRenderer';

/** 与 Tauri Rust 侧命令对应的桥接面（可选；web 预览为 undefined）。 */
export interface PetBridge {
  openRoute: (route: '/cabin' | '/workbench') => void;
  hideWindow: () => void;
}

/** 浏览器降级桥：直接改 location。Tauri 打包时由 Rust/preload 覆盖 window.__fyPetBridge。 */
const fallbackBridge: PetBridge = {
  openRoute: (route) => {
    window.location.assign(route);
  },
  hideWindow: () => {
    window.dispatchEvent(new CustomEvent('fy:pet-hide'));
  },
};

/** Tauri preload 注入的桥（真实打包环境）；浏览器预览下不存在，走 fallback。 */
function resolveBridge(propBridge?: PetBridge): PetBridge {
  if (propBridge) return propBridge;
  const g = window as unknown as { __fyPetBridge?: PetBridge };
  return g.__fyPetBridge ?? fallbackBridge;
}

const PIXEL_SCALE = 4; // 20px 宽的小矩阵放大到 ~80px，浮窗里清晰可见。
const WALK_INTERVAL_MS = 420;

function paintFrame(canvas: HTMLCanvasElement, frameIdx: number): void {
  const ctx = canvas.getContext('2d');
  if (!ctx) return; // jsdom 无 canvas：静默跳过（不报错）。
  const rows = PET_FRAMES[frameIdx];
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const cell of frameToCells(rows, PET_COLORS)) {
    ctx.fillStyle = colorToCss(cell.color);
    ctx.fillRect(cell.x * PIXEL_SCALE, cell.y * PIXEL_SCALE, PIXEL_SCALE, PIXEL_SCALE);
  }
}

export function PetWidget({ bridge }: { bridge?: PetBridge }) {
  const resolvedBridge = resolveBridge(bridge);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [frame, setFrame] = useState(0);
  const [mood, setMood] = useState<Mood>({ level: 'ok', needsCare: false, label: '心情未知' });
  const [bubble, setBubble] = useState<string>('');

  // 两帧走路动画。
  useEffect(() => {
    const id = window.setInterval(() => setFrame((f) => (f + 1) % PET_FRAMES.length), WALK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (canvasRef.current) {
      const rows = PET_FRAMES[0];
      const { width, height } = frameSize(rows);
      canvasRef.current.width = width * PIXEL_SCALE;
      canvasRef.current.height = height * PIXEL_SCALE;
      paintFrame(canvasRef.current, frame);
    }
  }, [frame]);

  // 读真实存档派生心情；读不到就诚实降级，不伪造。
  useEffect(() => {
    let cancelled = false;
    fetchSave()
      .then((save) => {
        if (cancelled) return;
        setMood(moodFromIntimacy(typeof save.intimacy === 'number' ? save.intimacy : null));
      })
      .catch(() => {
        if (cancelled) return;
        setMood({ level: 'ok', needsCare: false, label: '心情未知（未连接小屋）' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onPetClick = useCallback(() => {
    setBubble(pickPetLine(mood.level, Date.now() % 100000));
  }, [mood.level]);

  const onContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      // 极简原生菜单：用 window.prompt 式不可行；这里用 confirm 双选会丢体验。
      // 诚实方案：弹出一个由三个按钮组成的绝对定位菜单（见下方 state）。
      setMenuOpen(true);
    },
    [],
  );

  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div
      style={{
        // 透明窗：根节点不设背景，让 Tauri transparent 生效。
        position: 'relative',
        width: PET_FRAMES[0][0].length * PIXEL_SCALE,
        fontFamily: 'system-ui, sans-serif',
        userSelect: 'none',
        cursor: 'pointer',
      }}
      onContextMenu={onContextMenu}
    >
      {bubble && (
        <div
          data-testid="pet-bubble"
          style={{
            position: 'absolute',
            bottom: '100%',
            left: 0,
            marginBottom: 8,
            /* 台词气泡：深底 + 冰蓝字。原先写死 rgba(15,23,42,.85) / #e2f3ff，
               改由令牌派生，改主题时不必回来改这里。 */
            background: 'color-mix(in srgb, var(--ui-ink-1) 85%, transparent)',
            color: 'var(--ui-sky-100)',
            fontSize: 12,
            lineHeight: 1.4,
            padding: '6px 10px',
            borderRadius: 10,
            maxWidth: 200,
            whiteSpace: 'normal',
          }}
        >
          {bubble}
          <div style={{ fontSize: 10, opacity: 0.6, marginTop: 2 }}>预生成台词池</div>
        </div>
      )}

      <canvas
        ref={canvasRef}
        onClick={onPetClick}
        data-testid="pet-canvas"
        style={{ display: 'block', imageRendering: 'pixelated' }}
      />

      <div data-testid="pet-mood" style={{ fontSize: 10, color: 'var(--ui-ink-1)', marginTop: 2 }}>
        {mood.label}
        {mood.needsCare ? ' · 点小屋喂我' : ''}
      </div>

      {menuOpen && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            background: 'var(--ui-glass-3)',
            border: '1px solid var(--ui-line-1)',
            borderRadius: 8,
            boxShadow: 'var(--ui-shadow-2)',
            fontSize: 12,
            zIndex: 10,
          }}
        >
          {([
            ['打开小屋', '/cabin' as const],
            ['打开工作台', '/workbench' as const],
          ] as const).map(([label, route]) => (
            <button
              key={route}
              onClick={() => {
                setMenuOpen(false);
                resolvedBridge.openRoute(route);
              }}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '6px 12px', border: 'none', background: 'transparent', cursor: 'pointer' }}
            >
              {label}
            </button>
          ))}
          <button
            onClick={() => {
              setMenuOpen(false);
              resolvedBridge.hideWindow();
            }}
            style={{ display: 'block', width: '100%', textAlign: 'left', padding: '6px 12px', border: 'none', borderTop: '1px solid var(--ui-line-1)', background: 'transparent', cursor: 'pointer' }}
          >
            隐藏
          </button>
        </div>
      )}
    </div>
  );
}
