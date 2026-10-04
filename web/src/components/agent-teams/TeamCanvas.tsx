import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { TeamSnapshot } from '../../api/teams';
import {
  bezier,
  EDGE_MARKERS,
  edgeClassFor,
  SCOPE_LABEL,
  stateBadge,
  type EdgeGeom,
  type Pt,
} from './teamVisual';

/**
 * 21 号团队拓扑 · 无限画布。
 *
 * - 节点可任意拖拽（按住节点拖动，位移超阈值判定为拖拽并抑制当次点击选中）；
 * - 拖拽空白平移、滚轮以光标为锚缩放、左下角缩放/复位控件；
 * - 连线（有向贝塞尔）随节点位置实时重算，坐标全部在内容空间，
 *   跟随平移缩放整体变换；
 * - 布局/视口按团队 id 持久化到 localStorage，刷新不丢；
 * - 动画仅限执行/验证/传输状态（edgeFlow），遵循 prefers-reduced-motion。
 */

interface Props {
  snapshot: TeamSnapshot;
  selectedRole: string | null;
  onSelectRole: (role: string) => void;
}

const COORD_W = 320;
const MEMBER_W = 260;
const VERIFIER_W = 280;
const GAP_X = 96;
const GAP_Y = 130;
// 初始布局用的高度估算；渲染后以实测高度重算连线
const EST_COORD_H = 150;
const EST_MEMBER_H = 185;
const EST_VERIFIER_H = 96;
const ZOOM_MIN = 0.35;
const ZOOM_MAX = 2;

type LayoutMap = Record<string, Pt>;
type SizeMap = Record<string, { w: number; h: number }>;
const VERIFIER_KEY = '__verifier';

function storageKey(teamId: string): string {
  return `fy.canvas.layout.v1:${teamId}`;
}

function initialLayout(coordRole: string, memberRoles: string[]): LayoutMap {
  const lay: LayoutMap = {};
  lay[coordRole] = { x: -COORD_W / 2, y: 0 };
  const rowW = memberRoles.length * MEMBER_W + Math.max(0, memberRoles.length - 1) * GAP_X;
  memberRoles.forEach((role, i) => {
    lay[role] = { x: -rowW / 2 + i * (MEMBER_W + GAP_X), y: EST_COORD_H + GAP_Y };
  });
  lay[VERIFIER_KEY] = { x: -VERIFIER_W / 2, y: EST_COORD_H + GAP_Y + EST_MEMBER_H + GAP_Y };
  return lay;
}

export function TeamCanvas({ snapshot, selectedRole, onSelectRole }: Props) {
  const coordRole = snapshot.team.coordinator_role;
  const members = useMemo(
    () => snapshot.members.filter((m) => m.role !== coordRole),
    [snapshot, coordRole],
  );

  const canvasRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const [layout, setLayout] = useState<LayoutMap>(() =>
    initialLayout(coordRole, members.map((m) => m.role)),
  );
  const [pan, setPan] = useState<Pt>({ x: 260, y: 40 });
  const [zoom, setZoom] = useState(1);
  const [sizes, setSizes] = useState<SizeMap>({});
  const [panning, setPanning] = useState(false);
  const loadedTeamRef = useRef<string>('');
  const suppressClickRef = useRef<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);

  const setNodePos = useCallback((key: string, pos: Pt) => {
    setLayout((prev) => ({ ...prev, [key]: pos }));
  }, []);

  // ---------------------------------------------------------------- 载入/持久化
  useEffect(() => {
    if (loadedTeamRef.current === snapshot.team.id) return;
    loadedTeamRef.current = snapshot.team.id;
    let restored = false;
    try {
      const raw = localStorage.getItem(storageKey(snapshot.team.id));
      if (raw) {
        const saved = JSON.parse(raw) as { layout?: LayoutMap; pan?: Pt; zoom?: number };
        if (saved.layout && Object.keys(saved.layout).length > 0) {
          setLayout(saved.layout);
          if (saved.pan) setPan(saved.pan);
          if (typeof saved.zoom === 'number') setZoom(saved.zoom);
          restored = true;
        }
      }
    } catch {
      // 损坏的存档按无存档处理
    }
    if (!restored) {
      const lay = initialLayout(coordRole, members.map((m) => m.role));
      setLayout(lay);
      // 视口尺寸就绪后居中
      requestAnimationFrame(() => {
        const cv = canvasRef.current;
        if (!cv) return;
        setZoom(1);
        setPan({ x: cv.clientWidth / 2, y: 36 });
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot.team.id]);

  useEffect(() => {
    if (loadedTeamRef.current !== snapshot.team.id) return;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      try {
        localStorage.setItem(
          storageKey(snapshot.team.id),
          JSON.stringify({ layout, pan, zoom }),
        );
      } catch {
        // 配额满/隐私模式：仅本次会话内生效
      }
    }, 350);
    return () => {
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    };
  }, [layout, pan, zoom, snapshot.team.id]);

  // ---------------------------------------------------------------- 实测节点尺寸
  useLayoutEffect(() => {
    const cv = canvasRef.current;
    if (!cv || zoom <= 0) return;
    const next: SizeMap = {};
    let changed = false;
    for (const [key, el] of nodeRefs.current) {
      const r = el.getBoundingClientRect();
      const w = Math.round((r.width / zoom) * 10) / 10;
      const h = Math.round((r.height / zoom) * 10) / 10;
      const prev = sizes[key];
      if (!prev || prev.w !== w || prev.h !== h) changed = true;
      next[key] = { w, h };
    }
    if (changed || Object.keys(sizes).length !== Object.keys(next).length) setSizes(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, zoom, snapshot, members.length]);

  // ---------------------------------------------------------------- 滚轮缩放
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = cv.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      setZoom((z) => {
        const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * Math.exp(-e.deltaY * 0.0012)));
        if (nz === z) return z;
        setPan((p) => ({
          x: cx - ((cx - p.x) * nz) / z,
          y: cy - ((cy - p.y) * nz) / z,
        }));
        return nz;
      });
    };
    cv.addEventListener('wheel', onWheel, { passive: false });
    return () => cv.removeEventListener('wheel', onWheel);
  }, []);

  // ---------------------------------------------------------------- 拖拽节点/平移
  const dragRef = useRef<{
    mode: 'node' | 'pan' | null;
    key: string;
    startClient: Pt;
    orig: Pt;
    moved: boolean;
  } | null>(null);

  const onNodePointerDown = (key: string) => (e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    const pos = layout[key];
    if (!pos) return;
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    dragRef.current = {
      mode: 'node', key,
      startClient: { x: e.clientX, y: e.clientY },
      orig: pos, moved: false,
    };
  };

  const onCanvasPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('[data-node]')) return; // 节点自身处理
    canvasRef.current?.setPointerCapture?.(e.pointerId);
    dragRef.current = {
      mode: 'pan', key: '',
      startClient: { x: e.clientX, y: e.clientY },
      orig: pan, moved: false,
    };
    setPanning(true);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startClient.x;
    const dy = e.clientY - d.startClient.y;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    if (d.mode === 'node') {
      setNodePos(d.key, {
        x: Math.round(d.orig.x + dx / zoom),
        y: Math.round(d.orig.y + dy / zoom),
      });
    } else {
      setPan({ x: d.orig.x + dx, y: d.orig.y + dy });
    }
  };

  const onPointerUp = () => {
    const d = dragRef.current;
    if (d?.mode === 'node' && d.moved) suppressClickRef.current = d.key;
    dragRef.current = null;
    setPanning(false);
  };

  const onNodeClick = (role: string) => () => {
    if (suppressClickRef.current === role) {
      suppressClickRef.current = null;
      return;
    }
    onSelectRole(role);
  };

  const zoomAtCenter = (factor: number) => {
    const cv = canvasRef.current;
    const cx = cv ? cv.clientWidth / 2 : 0;
    const cy = cv ? cv.clientHeight / 2 : 0;
    setZoom((z) => {
      const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor));
      if (nz === z) return z;
      setPan((p) => ({ x: cx - ((cx - p.x) * nz) / z, y: cy - ((cy - p.y) * nz) / z }));
      return nz;
    });
  };

  const resetView = useCallback(() => {
    const lay = initialLayout(coordRole, members.map((m) => m.role));
    setLayout(lay);
    setZoom(1);
    const cv = canvasRef.current;
    setPan({ x: cv ? cv.clientWidth / 2 : 260, y: 36 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coordRole, members.length]);

  // ---------------------------------------------------------------- 连线（内容空间）
  const edges = useMemo<EdgeGeom[]>(() => {
    const out: EdgeGeom[] = [];
    const cPos = layout[coordRole];
    const cSize = sizes[coordRole] ?? { w: COORD_W, h: EST_COORD_H };
    const vPos = layout[VERIFIER_KEY];
    const vSize = sizes[VERIFIER_KEY] ?? { w: VERIFIER_W, h: EST_VERIFIER_H };
    if (!cPos || !vPos) return out;
    let anyRunning = false;
    let allDone = members.length > 0;
    for (const m of members) {
      const pos = layout[m.role];
      if (!pos) continue;
      const size = sizes[m.role] ?? { w: MEMBER_W, h: EST_MEMBER_H };
      const st: string = m.state;
      if (st === 'running' || st === 'starting') anyRunning = true;
      if (st !== 'completed' && st !== 'succeeded') allDone = false;
      out.push(bezier(
        { x: cPos.x + cSize.w / 2, y: cPos.y + cSize.h },
        { x: pos.x + size.w / 2, y: pos.y },
        `out-${m.role}`, edgeClassFor(st), '派发任务',
      ));
      out.push(bezier(
        { x: pos.x + size.w / 2, y: pos.y + size.h },
        { x: vPos.x + vSize.w / 2, y: vPos.y },
        `in-${m.role}`,
        st === 'running' || st === 'starting' ? 'edge-running'
          : st === 'waiting_rework' ? 'edge-rework'
          : st === 'failed' ? 'edge-failed'
          : st === 'blocked' ? 'edge-blocked'
          : 'edge-idle',
        '提交验证',
      ));
    }
    if (members.length > 0 && !anyRunning && allDone) {
      for (const e of out) {
        if (e.key.startsWith('in-') && e.cls === 'edge-idle') e.cls = 'edge-complete';
      }
    }
    return out;
  }, [layout, sizes, members, coordRole]);

  const setNodeRef = (key: string) => (el: HTMLElement | null) => {
    if (el) nodeRefs.current.set(key, el);
    else nodeRefs.current.delete(key);
  };

  const node = (
    key: string,
    width: number,
    cls: string,
    content: React.ReactNode,
    clickable: boolean,
  ) => (
    <div
      key={key}
      ref={setNodeRef(key)}
      data-node={key}
      className={`team-node ${cls} ${clickable && selectedRole === key ? 'selected' : ''}`}
      style={{ left: layout[key]?.x ?? 0, top: layout[key]?.y ?? 0, width }}
      {...(clickable ? {
        role: 'button' as const,
        'aria-pressed': selectedRole === key,
        tabIndex: 0,
        onClick: onNodeClick(key),
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onSelectRole(key);
          }
        },
      } : {})}
      onPointerDown={clickable ? onNodePointerDown(key) : (e) => {
        if (e.button !== 0) return;
        const pos = layout[key];
        if (!pos) return;
        e.stopPropagation();
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        dragRef.current = {
          mode: 'node', key,
          startClient: { x: e.clientX, y: e.clientY },
          orig: pos, moved: false,
        };
      }}
    >
      {content}
    </div>
  );

  const coordMember = snapshot.members.find((m) => m.role === coordRole);
  const coordRequested = String(snapshot.team.default_binding.model_id ?? '未设置');

  return (
    <>
      <div
        ref={canvasRef}
        className={`team-canvas ${panning ? 'panning' : ''}`}
        onPointerDown={onCanvasPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <div
          className="team-canvas-inner"
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
        >
          <svg className="team-connections" aria-hidden="true">
            <defs>
              {Object.entries(EDGE_MARKERS).map(([cls, color]) => (
                <marker key={cls} id={`arrow-${cls}`} viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill={color} />
                </marker>
              ))}
            </defs>
            {edges.map((e) => (
              <path key={e.key} className={`team-edge ${e.cls}`} d={e.d}
                markerEnd={`url(#arrow-${e.cls})`} />
            ))}
            {edges.map((e) => (
              <text key={`label-${e.key}`} className="edge-label" x={e.mid.x} y={e.mid.y}
                textAnchor="middle" dominantBaseline="middle">{e.label}</text>
            ))}
          </svg>

          {node(coordRole, COORD_W, 'coordinator', (
            <>
              <div className="node-head">
                <span>主协调 · 任务拆解与验收</span>
                <span>v{snapshot.team.version}</span>
              </div>
              <div className="node-role">{coordRole}</div>
              <div className="node-line">宿主：Find Yourself</div>
              <div className="node-line">请求：{coordRequested} · 团队默认</div>
              <div className="node-line">
                实际：{coordMember?.effective_model ?? '未执行'}
              </div>
            </>
          ), true)}

          {members.map((m) => {
            const b = stateBadge(m.state);
            return node(m.role, MEMBER_W, `${m.state === 'blocked' || m.state === 'failed' ? 'blocked' : ''} ${m.state === 'paused' || m.state === 'waiting_rework' ? 'paused' : ''}`, (
              <>
                <div className="node-head">
                  <span>执行成员 · 独立会话</span>
                  <span className={b.cls}>{b.text}</span>
                </div>
                <div className="node-role">{m.title || m.role}</div>
                <div className="node-line">角色：{m.role}</div>
                <div className="node-line">
                  请求：<strong>{m.requested_model || '未设置'}</strong> ·{' '}
                  {SCOPE_LABEL[m.inherited_from] ?? m.inherited_from}
                </div>
                <div className="node-line">
                  实际：<strong>{m.effective_model}</strong>
                  {m.effective_confidence === 'unknown' && '（供应商隐藏路由）'}
                </div>
                {m.blocked_reason && (
                  <div className="node-line" style={{ color: 'var(--rose)' }}>
                    阻塞：{m.blocked_reason}
                  </div>
                )}
              </>
            ), true);
          })}

          {node(VERIFIER_KEY, VERIFIER_W, 'verifier', (
            <>
              独立测试与版本核对
              <span>成员自报成功不能绕过验证与产物版本门禁</span>
            </>
          ), false)}
        </div>

        <div className="canvas-controls">
          <button type="button" onClick={() => zoomAtCenter(1 / 1.2)} aria-label="缩小">−</button>
          <span>{Math.round(zoom * 100)}%</span>
          <button type="button" onClick={() => zoomAtCenter(1.2)} aria-label="放大">＋</button>
          <button type="button" onClick={resetView}>复位视图</button>
          <span className="canvas-hint">拖空白平移 · 滚轮缩放 · 拖节点移动</span>
        </div>
      </div>

      <div className="status-legend" aria-label="节点和连线状态图例">
        <strong>状态图例</strong>
        <span className="legend-pending">○ 待执行</span>
        <span className="legend-running">● 执行中</span>
        <span className="legend-complete">✓ 已完成</span>
        <span className="legend-waiting">◷ 等待/审批</span>
        <span className="legend-verifying">⟳ 验证中</span>
        <span className="legend-rework">↩ 返工</span>
        <span className="legend-failed">× 失败</span>
        <span className="legend-blocked">▣ 阻塞</span>
        <span className="legend-paused">Ⅱ 暂停/取消</span>
        <span className="legend-unknown">? 未知</span>
      </div>
    </>
  );
}
