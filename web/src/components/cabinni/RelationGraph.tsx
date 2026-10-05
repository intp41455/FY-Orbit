/**
 * 包 D 私有组件 · 特征群关系网（2D SVG）
 * ---------------------------------------------------------------------------
 * 视觉契约（基准原文「协作画布必须保留可视化拓扑」的同款要求）：
 *   - 连线**不是**普通文字箭头：语义用「实线 / 虚线 + 方向」区分；
 *   - 节点**不是**纯色圆点：同时给形状描边色 + 状态文字 + 置信度；
 *   - 颜色不得是唯一信息通道：节点审核状态走九档语义色 + 文字标签。
 *
 * 连线语义映射（沿用基准 §连线语义）：
 *   抑制 inhibits → 虚线（无向，说明是负关系）
 *   其他         → 实线
 *   增强 reinforces / 引用 references → 实线加粗（strength 越粗）
 *
 * 可访问性：SVG 里的节点用 `<g role="button" tabIndex={0}>` 而不是裸 onClick，
 * 键盘可达（Tab 聚焦 + Enter/Space 选中），并且每个节点带 `<title>`。
 */
import { useCallback, type KeyboardEvent } from 'react';
import type { ProfileClusterNode, ProfileEdge } from '../../api/profiles';
import { reviewMeta } from './ProfileDimensions';

export interface RelationGraphProps {
  nodes: ProfileClusterNode[];
  edges: ProfileEdge[];
  selectedId: string | null;
  onSelect: (node: ProfileClusterNode) => void;
}

const VIEW_W = 520;
const VIEW_H = 280;

export function RelationGraph({ nodes, edges, selectedId, onSelect }: RelationGraphProps) {
  const pick = useCallback(
    (node: ProfileClusterNode) => () => onSelect(node),
    [onSelect],
  );

  const onKey = useCallback(
    (node: ProfileClusterNode) => (e: KeyboardEvent<SVGGElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onSelect(node);
      }
    },
    [onSelect],
  );

  if (nodes.length === 0) {
    return (
      <p className="cabin-ni-evi-empty" data-testid="profile-graph-empty">
        这次推演没有产出可展示的关系节点。
      </p>
    );
  }

  return (
    <div className="cabin-ni-graph">
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="group" aria-label="特征群关系网">
        <title>特征群与推演关系网</title>

        {/* ---- 连线：先画，压在节点下面 ---- */}
        <g aria-hidden="true">
          {edges.map((e, idx) => {
            const src = nodes.find((n) => n.id === e.source);
            const dst = nodes.find((n) => n.id === e.target);
            if (!src || !dst) return null;
            const x1 = src.x ?? 120;
            const y1 = src.y ?? 100;
            const x2 = dst.x ?? 360;
            const y2 = dst.y ?? 180;
            const dash = e.relation === 'inhibits' ? '5 4' : undefined;
            const width = 1.2 + Math.min(1.4, (e.strength ?? 0) * 1.4);
            return (
              <g key={`${e.id}-${idx}`}>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke="var(--ui-line-strong)"
                  strokeWidth={width}
                  strokeDasharray={dash}
                />
                <text
                  x={(x1 + x2) / 2}
                  y={(y1 + y2) / 2 - 5}
                  fontSize={10}
                  fill="var(--ui-ink-3)"
                  textAnchor="middle"
                >
                  {e.relation}
                </text>
              </g>
            );
          })}
        </g>

        {/* ---- 节点：描边色 + 内点 + 状态文字 ---- */}
        <g>
          {nodes.map((node) => {
            const nx = node.x ?? 150;
            const ny = node.y ?? 120;
            const selected = selectedId === node.id;
            const meta = reviewMeta(node.review_status);
            const short = node.label.length > 7 ? `${node.label.slice(0, 7)}…` : node.label;
            return (
              <g
                key={node.id}
                className="cabin-ni-graph-node"
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                aria-label={`${node.label}，${meta.label}，置信度 ${(node.confidence * 100).toFixed(0)}%`}
                transform={`translate(${nx}, ${ny})`}
                onClick={pick(node)}
                onKeyDown={onKey(node)}
                data-testid={`profile-graph-node-${node.id}`}
              >
                <title>{`${node.label} · ${meta.label}`}</title>
                <circle r="22" fill="var(--ui-glass-3)" stroke={`var(--ui-st-${meta.tone})`} strokeWidth={selected ? 3 : 2} />
                <circle r="5" fill={`var(--ui-st-${meta.tone})`} />
                <text y="32" fontSize="10" fontWeight={selected ? 700 : 500} fill="var(--ui-ink-1)" textAnchor="middle">
                  {short}
                </text>
                {/* 状态文字：颜色之外的第二信息通道 */}
                <text y="43" fontSize="9" fill="var(--ui-ink-3)" textAnchor="middle">
                  {meta.label} · {(node.confidence * 100).toFixed(0)}%
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <p className="cabin-ni-evi" style={{ marginTop: 'var(--ui-s-2)' }}>
        <span className="cabin-ni-evi-main">
          实线 = 引用/增强关系，粗细随强度；虚线 = 抑制关系。点击（或键盘 Tab + Enter）节点，
          在下方展开证据链，不跳页。
        </span>
      </p>
    </div>
  );
}