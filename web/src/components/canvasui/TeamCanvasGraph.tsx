/**
 * 包 B · 团队拓扑画布（水平三栏：左协调者 → 中并行成员 → 右验证节点）。
 *
 * - SVG 贝塞尔连线，箭头用 marker；返工边反向；
 * - 传输类边（派发/提交验证）在 running 状态下显示光点流动；
 * - 拖拽空白平移、滚轮以光标为锚缩放；
 * - 节点可点击选中（开抽屉）；键盘 Tab + Enter；双击改名；右键批次历史；
 * - 不做 backdrop-filter，避免与 SVG 连线叠出脏边。
 * - 坐标按容器宽度比例分布（18% / 50% / 82%），窗口变化保持比例。
 */
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { TeamMemberView, TeamSnapshot } from '../../api/teams';
import { LineIcon } from '../ui/LineIcon';
import { SCOPE_LABEL } from '../agent-teams/teamVisual';
import {
  edgeClassForState,
  edgeFlowActive,
  fmtDuration,
  statusMeta,
} from './statusMap';

/**
 * 可访问名口径：e2e/ui-team.spec.ts 的 memberNode(page, role) 按 role 键
 * （implementer / worker）在 .team-map 内检索节点。后端模板两个字段都返回
 * （agent_teams.py 同时给出 role 与 title），而 title 恒非空，所以可访问名
 * 必须显式带上 role 键，否则 role 永远检索不到、inspector 相关断言全部落空。
 */

interface Props {
  snapshot: TeamSnapshot;
  memberViews: TeamMemberView[];
  selectedRole: string | null;
  onSelectRole: (role: string | null) => void;
  hoveredRole: string | null;
  onHoverRole: (role: string | null) => void;
  onRenameMember: (role: string, title: string) => void;
}

interface Pt { x: number; y: number }
interface NodeGeom { x: number; y: number; w: number; h: number }

const COORD_W = 240;
const MEMBER_W = 230;
const VERIFIER_W = 200;
const MEMBER_GAP_Y = 150;
const MEMBER_TOP = 60;
const VERIFIER_KEY = '__verifier__';

// 三栏水平位置：协调者 18%，成员列居中，验证节点 82%（任务包 §4）
const COL_RATIO = { coord: 0.18, members: 0.5, verifier: 0.82 };
const CANVAS_W = 1000; // 逻辑坐标系宽度，随容器宽度等比缩放

const ZOOM_MIN = 0.4;
const ZOOM_MAX = 1.8;

export function TeamCanvasGraph({
  snapshot,
  memberViews,
  selectedRole,
  onSelectRole,
  hoveredRole,
  onHoverRole,
  onRenameMember,
}: Props) {
  const coordRole = snapshot.team.coordinator_role;
  const coordMember = memberViews.find((m) => m.role === coordRole);
  const members = useMemo(
    () => memberViews.filter((m) => m.role !== coordRole),
    [memberViews, coordRole],
  );

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLDivElement>());
  const [pan, setPan] = useState<Pt>({ x: 80, y: 40 });
  const [zoom, setZoom] = useState(1);
  const [sizeMap, setSizeMap] = useState<Record<string, { w: number; h: number }>>({});
  const [panning, setPanning] = useState(false);
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; role: string } | null>(null);
  const dragRef = useRef<{ mode: 'pan' | null; start: Pt; orig: Pt } | null>(null);
  const suppressClickRef = useRef(false);

  // ---- 自动布局（按比例分布，窗口变化保持比例）----
  const layout = useMemo<Record<string, NodeGeom>>(() => {
    const out: Record<string, NodeGeom> = {};
    const centerY = MEMBER_TOP + ((members.length - 1) * MEMBER_GAP_Y) / 2;
    out[coordRole] = {
      x: CANVAS_W * COL_RATIO.coord - COORD_W / 2,
      y: centerY - 70,
      w: COORD_W,
      h: 140,
    };
    members.forEach((m, i) => {
      out[m.role] = {
        x: CANVAS_W * COL_RATIO.members - MEMBER_W / 2,
        y: MEMBER_TOP + i * MEMBER_GAP_Y,
        w: MEMBER_W,
        h: 140,
      };
    });
    out[VERIFIER_KEY] = {
      x: CANVAS_W * COL_RATIO.verifier - VERIFIER_W / 2,
      y: centerY - 60,
      w: VERIFIER_W,
      h: 120,
    };
    return out;
  }, [coordRole, members]);

  // ---- 居中 ----
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const t = requestAnimationFrame(() => {
      setZoom(1);
      setPan({ x: el.clientWidth / 2 - CANVAS_W / 2, y: el.clientHeight / 2 - 200 });
    });
    return () => cancelAnimationFrame(t);
  }, [snapshot.team.id, members.length]);

  // ---- 实测节点尺寸 ----
  useLayoutEffect(() => {
    const next: Record<string, { w: number; h: number }> = {};
    let changed = false;
    nodeRefs.current.forEach((el, key) => {
      const r = el.getBoundingClientRect();
      const w = Math.round((r.width / zoom) * 10) / 10;
      const h = Math.round((r.height / zoom) * 10) / 10;
      const prev = sizeMap[key];
      if (!prev || prev.w !== w || prev.h !== h) changed = true;
      next[key] = { w, h };
    });
    if (changed || Object.keys(next).length !== Object.keys(sizeMap).length) {
      setSizeMap(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, zoom, snapshot, members.length]);

  // ---- 滚轮缩放 ----
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      setZoom((z) => {
        const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * Math.exp(-e.deltaY * 0.0012)));
        if (nz === z) return z;
        setPan((p) => ({ x: cx - ((cx - p.x) * nz) / z, y: cy - ((cy - p.y) * nz) / z }));
        return nz;
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  // ---- 平移 ----
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('[data-cv-node]')) return;
    wrapRef.current?.setPointerCapture?.(e.pointerId);
    dragRef.current = { mode: 'pan', start: { x: e.clientX, y: e.clientY }, orig: pan };
    setPanning(true);
    setContextMenu(null);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setPan({ x: d.orig.x + (e.clientX - d.start.x), y: d.orig.y + (e.clientY - d.start.y) });
  };
  const onPointerUp = () => {
    dragRef.current = null;
    setPanning(false);
  };

  const zoomAtCenter = (factor: number) => {
    const el = wrapRef.current;
    const cx = el ? el.clientWidth / 2 : 0;
    const cy = el ? el.clientHeight / 2 : 0;
    setZoom((z) => {
      const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor));
      if (nz === z) return nz;
      setPan((p) => ({ x: cx - ((cx - p.x) * nz) / z, y: cy - ((cy - p.y) * nz) / z }));
      return nz;
    });
  };

  // ---- 连线几何 ----
  interface Edge {
    key: string;
    d: string;
    cls: string;
    flow: boolean;
    rework: boolean;
    from: Pt;
    to: Pt;
  }
  const edges = useMemo<Edge[]>(() => {
    const out: Edge[] = [];
    const c = layout[coordRole];
    if (!c) return out;
    const v = layout[VERIFIER_KEY];
    if (!v) return out;

    for (const m of members) {
      const g = layout[m.role];
      if (!g) continue;
      const isRework = m.state === 'waiting_rework';
      // 协调者 → 成员：水平 S 曲线
      const from: Pt = { x: c.x + c.w, y: c.y + c.h / 2 };
      const to: Pt = { x: g.x, y: g.y + g.h / 2 };
      const mx = (from.x + to.x) / 2;
      out.push({
        key: `d-${m.role}`,
        d: `M ${from.x} ${from.y} C ${mx} ${from.y}, ${mx} ${to.y}, ${to.x} ${to.y}`,
        cls: edgeClassForState(m.state),
        flow: edgeFlowActive(m.state),
        rework: false,
        from, to,
      });
      // 成员 → 验证节点
      const f2: Pt = { x: g.x + g.w, y: g.y + g.h / 2 };
      const t2: Pt = { x: v.x, y: v.y + v.h / 2 };
      const mx2 = (f2.x + t2.x) / 2;
      out.push({
        key: `u-${m.role}`,
        d: `M ${f2.x} ${f2.y} C ${mx2} ${f2.y}, ${mx2} ${t2.y}, ${t2.x} ${t2.y}`,
        cls: edgeClassForState(m.state),
        flow: edgeFlowActive(m.state),
        rework: isRework,
        from: f2, to: t2,
      });
    }
    return out;
  }, [layout, members, coordRole]);

  // ---- 箭头 marker ----
  const markerColors: Record<string, string> = {
    'cv-edge--idle': 'var(--ui-line-1)',
    'cv-edge--running': 'var(--ui-st-running)',
    'cv-edge--complete': 'var(--ui-st-complete)',
    'cv-edge--waiting': 'var(--ui-st-waiting)',
    'cv-edge--verifying': 'var(--ui-st-verifying)',
    'cv-edge--rework': 'var(--ui-st-rework)',
    'cv-edge--failed': 'var(--ui-st-failed)',
    'cv-edge--blocked': 'var(--ui-st-blocked)',
    'cv-edge--paused': 'var(--ui-st-paused)',
    'cv-edge--external': 'var(--ui-st-external)',
  };

  const renderNode = (key: string, geom: NodeGeom, node: TeamMemberView | null, kind: 'coord' | 'member' | 'verifier') => {
    const selected = selectedRole === key;
    const meta = node ? statusMeta(node.state) : null;
    const isInteractive = kind !== 'verifier';
    const doneCount = members.filter((m) => m.state === 'completed').length;

    return (
      <div
        key={key}
        ref={(el) => {
          if (el) nodeRefs.current.set(key, el);
          else nodeRefs.current.delete(key);
        }}
        data-cv-node={key}
        className={[
          'cv-node',
          kind === 'coord' ? 'cv-node--coordinator' : '',
          kind === 'verifier' ? 'cv-node--verifier' : '',
          selected ? 'is-active' : '',
        ].join(' ')}
        style={{ left: geom.x, top: geom.y, width: geom.w }}
        role={isInteractive ? 'button' : undefined}
        tabIndex={isInteractive ? 0 : undefined}
        aria-label={
          node
            ? `成员 ${node.title || node.role}（${node.role}），状态 ${meta?.text ?? node.state}，正在 ${node.current_goal?.slice(0, 30) || '待命'}，已持续 ${fmtDuration(node.updated_at)}`
            : `独立验证节点，${doneCount}/${members.length} 成员已完成待核对`
        }
        onClick={() => {
          if (suppressClickRef.current) { suppressClickRef.current = false; return; }
          if (isInteractive) onSelectRole(key);
        }}
        onDoubleClick={() => {
          if (isInteractive && node) {
            setRenameValue(node.title || node.role);
            setRenameTarget(key);
          }
        }}
        onContextMenu={(e) => {
          if (!isInteractive) return;
          e.preventDefault();
          setContextMenu({ x: e.clientX, y: e.clientY, role: key });
        }}
        onKeyDown={(e) => {
          if (!isInteractive) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onSelectRole(key);
          }
          if (e.key === 'Escape') onSelectRole(null);
        }}
        onMouseEnter={() => isInteractive && onHoverRole(key)}
        onMouseLeave={() => onHoverRole(null)}
      >
        {kind === 'verifier' ? (
          <>
            <div className="cv-node__head">
              <span className="cv-node__role-tag">独立验证</span>
              <span className="cv-status cv-status--verifying">
                <span className="cv-status__dot" />
                <LineIcon name="target" size={16} />
                验证中
              </span>
            </div>
            <div className="cv-node__title">版本核对</div>
            <div className="cv-node__rows">
              <span>已完成 <strong>{doneCount}</strong> / {members.length} 成员</span>
              <span>成员自报成功不能绕过</span>
            </div>
          </>
        ) : node && meta ? (
          <>
            <div className="cv-node__head">
              <span className="cv-node__role-tag">
                {kind === 'coord' ? '主协调者' : '执行成员'}
              </span>
              <span className={`cv-status cv-status--${meta.tier}`}>
                <span className="cv-status__dot" />
                <LineIcon name={meta.icon} size={16} />
                {meta.text}
              </span>
            </div>
            <div className="cv-node__title">{node.title || node.role}</div>
            <div className="cv-node__rows">
              <span>宿主 <strong>{node.agent_host}</strong></span>
              <span>
                请求 <strong>{node.requested_model || '继承'}</strong>
                {' · '}{SCOPE_LABEL[node.inherited_from] ?? node.inherited_from}
              </span>
              <span>
                实际 <strong>{node.effective_model || '未执行'}</strong>
                {node.effective_confidence === 'unknown' && '（路由未知）'}
              </span>
              <span>批次 #{node.run_batch}</span>
              {node.current_goal && (
                <span style={{ color: 'var(--ui-ink-3)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  任务：{node.current_goal.slice(0, 40)}
                </span>
              )}
            </div>
            {node.blocked_reason && (
              <div className="cv-node__blocked">阻塞：{node.blocked_reason}</div>
            )}
            <div className="cv-node__duration">
              <span style={{ display: 'inline-flex', verticalAlign: 'middle', marginRight: 4 }}>
                <LineIcon name="clock" size={16} />
              </span>
              {fmtDuration(node.updated_at)}
            </div>
          </>
        ) : null}
      </div>
    );
  };

  return (
    <div
      ref={wrapRef}
      className={`cv-canvas ${panning ? 'panning' : ''}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <div
        className="cv-canvas-inner"
        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
      >
        <svg className="cv-connections" style={{ overflow: 'visible' }}>
          <defs>
            {Object.entries(markerColors).map(([cls, color]) => (
              <marker
                key={cls}
                id={`cv-arrow-${cls.replace('cv-edge--', '')}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={color} />
              </marker>
            ))}
          </defs>
          {edges.map((e) => {
            // 包 B §4：悬停节点 → 它的入边出边加亮到 --ui-sky-400，其余降到 --ui-line-2
            const related = !hoveredRole || e.key.includes(hoveredRole);
            const tone = !hoveredRole ? '' : related ? ' cv-edge--focus' : ' cv-edge--dim';
            const markerId = `cv-arrow-${e.cls.replace('cv-edge--', '')}`;
            return (
              <g key={e.key}>
                <path
                  d={e.d}
                  className={`cv-edge ${e.cls}${tone}`}
                  markerEnd={e.rework ? undefined : `url(#${markerId})`}
                  markerStart={e.rework ? `url(#${markerId})` : undefined}
                />
                {e.flow && related && (
                  <path d={e.d} className="cv-edge-flow" />
                )}
              </g>
            );
          })}
        </svg>

        {layout[coordRole] &&
          renderNode(coordRole, layout[coordRole], coordMember ?? null, 'coord')}
        {members.map((m) =>
          layout[m.role] ? renderNode(m.role, layout[m.role], m, 'member') : null,
        )}
        {layout[VERIFIER_KEY] && renderNode(VERIFIER_KEY, layout[VERIFIER_KEY], null, 'verifier')}
      </div>

      {/* 双击改名（PATCH members，更新 title） */}
      {renameTarget && (
        <div
          className="cv-modal-scrim"
          onClick={() => setRenameTarget(null)}
          onKeyDown={(e) => { if (e.key === 'Escape') setRenameTarget(null); }}
        >
          <form
            className="cv-modal ui-panel ui-panel--pad"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              const v = renameValue.trim();
              const role = renameTarget;
              setRenameTarget(null);
              if (v && role && v !== memberViews.find((m) => m.role === role)?.title) {
                onRenameMember(role, v.slice(0, 200));
              }
            }}
          >
            <p className="ui-panel-title">重命名成员</p>
            <label className="cv-modal-field" htmlFor="cv-rename-input">
              显示名称
              <input
                id="cv-rename-input"
                autoFocus
                type="text"
                value={renameValue}
                maxLength={200}
                onChange={(e) => setRenameValue(e.target.value)}
              />
            </label>
            <p className="ui-hint">
              改名会生成新的计划版本；在途批次保持其冻结模型。
            </p>
            <div className="cv-modal-actions">
              <button
                type="button"
                className="ui-btn ui-btn--sm"
                onClick={() => setRenameTarget(null)}
              >
                取消
              </button>
              <button type="submit" className="ui-btn ui-btn--primary ui-btn--sm">
                <LineIcon name="check" size={14} /> 确认改名
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 右键菜单 */}
      {contextMenu && (
        <div
          className="cv-ctx-menu ui-panel ui-panel--pad"
          style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            zIndex: 20,
            minWidth: 180,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="ui-panel-title" style={{ fontSize: 13, marginBottom: 8 }}>
            操作 · {contextMenu.role}
          </div>
          <button type="button" className="ui-btn ui-btn--sm ui-btn--block" onClick={() => { onSelectRole(contextMenu.role); setContextMenu(null); }}>
            <LineIcon name="edit" size={16} /> 编辑职责与模型
          </button>
          <button type="button" className="ui-btn ui-btn--sm ui-btn--block" onClick={() => { onSelectRole(contextMenu.role); setContextMenu(null); }}>
            <LineIcon name="history" size={16} /> 查看批次历史
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--sm ui-btn--block"
            disabled
            title="后端 /api/teams 未提供成员复制与重排接口；此处不放假按钮。"
          >
            <LineIcon name="copy" size={16} /> 复制成员（后端未提供）
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--sm ui-btn--block"
            disabled
            title="节点布局按 18%/50%/82% 比例自动分布，暂不开放手工重排。"
          >
            <LineIcon name="rotate" size={16} /> 手工重排（未开放）
          </button>
          <p className="ui-hint" style={{ margin: '6px 0 0' }}>
            灰项为本轮未开放能力，不做点得动的假动作。
          </p>
        </div>
      )}

      {/* 底部缩放控件 */}
      <div className="cv-bottombar">
        <button type="button" onClick={() => zoomAtCenter(1 / 1.2)} aria-label="缩小">
          <LineIcon name="minus" size={16} />
        </button>
        <span className="cv-zoom-label">{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={() => zoomAtCenter(1.2)} aria-label="放大">
          <LineIcon name="plus" size={16} />
        </button>
        <button
          type="button"
          onClick={() => {
            const el = wrapRef.current;
            if (el) setPan({ x: el.clientWidth / 2 - CANVAS_W / 2, y: el.clientHeight / 2 - 200 });
            setZoom(1);
          }}
          aria-label="复位视图"
          title="复位视图"
        >
          <LineIcon name="rotate" size={16} />
        </button>
      </div>
    </div>
  );
}
