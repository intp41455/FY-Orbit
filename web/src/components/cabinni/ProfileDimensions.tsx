/**
 * 包 D 私有组件 · 多维画像维度条 + 证据链
 * ---------------------------------------------------------------------------
 * 档位与颜色的对应关系（包 D 任务书 §6）：
 *   最强 = `--ui-st-complete` 深蓝，最弱 = `--ui-st-external` 紫灰，**不用绿色**。
 *
 * ⚠ 可访问性硬约束（总纲 §3 / 基准原文）：颜色不得是唯一信息通道。
 *   因此每档都同时给出：档位**文字** + 线条**图标** + 数值。
 *   只靠颜色区分档位会让色觉障碍用户读不出强弱，这是不可接受的。
 *
 * ⚠ 诚实性：维度得分可能缺失（后端只给 raw_value）。缺失一律显示「未回传」，
 *   绝不填 0 假装有数据。
 */
import { LineIcon, type LineIconName } from '../../components/ui/LineIcon';
import { CabinNiIcon } from './CabinNiIcon';
import type { ProfileClusterNode, ProfileMetric } from '../../api/profiles';

/**
 * ⚠ LineIcon.tsx 集里没有 `clock`，但总纲 §3 状态表要求「等待/审批 搭配 LineIcon clock」。
 *   冻结文件不可改，故此处从本地补录集取；交付报告已列为地基缺陷。
 */
type StatusIconName = LineIconName | 'clock';

export function StatusIcon({ name, size = 14 }: { name: StatusIconName; size?: number }) {
  if (name === 'clock') return <CabinNiIcon name="clock" size={size} />;
  return <LineIcon name={name} size={size} />;
}

/** 强弱档位 → 状态语义 + 图标 + 文案。颜色 / 图标 / 文字三者同时给。 */
const TIERS: readonly { min: number; tone: string; icon: StatusIconName; label: string }[] = [
  { min: 85, tone: 'complete', icon: 'check', label: '最强' },
  { min: 70, tone: 'running', icon: 'sparkles', label: '强' },
  { min: 55, tone: 'verifying', icon: 'eye', label: '中上' },
  { min: 40, tone: 'waiting', icon: 'clock', label: '中' },
  { min: 25, tone: 'paused', icon: 'pause', label: '偏弱' },
  { min: -Infinity, tone: 'external', icon: 'info', label: '弱' },
];

export interface DimensionTier {
  tone: string;
  icon: StatusIconName;
  label: string;
}

export function tierOf(score: number | undefined): DimensionTier {
  if (typeof score !== 'number' || Number.isNaN(score)) {
    return { tone: 'external', icon: 'info', label: '未回传' };
  }
  const hit = TIERS.find((t) => score >= t.min);
  return hit ?? { tone: 'external', icon: 'info', label: '弱' };
}

export interface DimensionBarsProps {
  metrics: ProfileMetric[];
}

/**
 * 客观量化推演维度。
 *
 * 条宽 = score（0–100），条色 = 档位色，条右侧同时给档位文字 + 图标 + 数值。
 * `metric_type`（语料统计 / 用户自述）属于**来源标注**而非状态语义，
 * 所以用中性徽标表达，不套九档状态色（避免与强弱档位混淆）。
 */
export function DimensionBars({ metrics }: DimensionBarsProps) {
  if (metrics.length === 0) {
    return (
      <p className="cabin-ni-evi-empty">
        <LineIcon name="info" size={16} />
        这次推演没有产出可重算的量化维度。
      </p>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {metrics.map((m, i) => {
        const tier = tierOf(m.score);
        const raw = m.score;
        const hasScore = typeof raw === 'number' && !Number.isNaN(raw);
        const width = hasScore ? Math.min(100, Math.max(2, raw)) : 0;
        const displayVal =
          m.display_value || (m.raw_value !== undefined ? String(m.raw_value) : '未回传');
        const typeLabel =
          m.metric_type === 'corpus_stat'
            ? '语料统计'
            : m.metric_type === 'self_report'
              ? '用户自述'
              : '派生';

        return (
          <div className="cabin-ni-dims" key={`${m.dimension}-${i}`}>
            <span className="cabin-ni-dim-name">{m.dimension}</span>

            <span className="cabin-ni-dim-bar">
              <span
                className="cabin-ni-dim-fill"
                style={{ width: `${width}%`, background: `var(--ui-st-${tier.tone})` }}
                data-testid={`profile-dim-bar-${i}`}
              />
            </span>

            <span className="cabin-ni-dim-val">
              <span className={`ui-badge ui-badge--${tier.tone}`} data-testid={`profile-dim-tier-${i}`}>
                <StatusIcon name={tier.icon} size={14} />
                {tier.label}
              </span>
              <span>{displayVal}</span>
              <span className="ui-badge ui-badge--neutral">{typeLabel}</span>
            </span>

            {m.calculation_formula && (
              <span
                style={{
                  gridColumn: '1 / -1',
                  fontSize: 'var(--ui-fs-xs)',
                  color: 'var(--ui-ink-4)',
                }}
              >
                公式: <code>{m.calculation_formula}</code> · 证据切片数: {m.evidence_count}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 审核状态 → 九档状态语义（accepted=完成深蓝，rejected=失败红，其余走中性 / 外部未知）。 */
const REVIEW_MAP: Record<string, { tone: string; icon: StatusIconName; label: string }> = {
  accepted: { tone: 'complete', icon: 'check', label: '已确认' },
  edited: { tone: 'verifying', icon: 'edit', label: '已修订' },
  rejected: { tone: 'failed', icon: 'xCircle', label: '已否定' },
  uncertain: { tone: 'waiting', icon: 'clock', label: '待验证' },
  invalidated: { tone: 'paused', icon: 'pause', label: '已作废' },
  candidate: { tone: 'external', icon: 'sparkles', label: '候选' },
  pending: { tone: 'external', icon: 'info', label: '待审' },
};

export function reviewMeta(status: string): { tone: string; icon: StatusIconName; label: string } {
  return REVIEW_MAP[status] ?? { tone: 'external', icon: 'info', label: status };
}

export interface EvidenceChainProps {
  node: ProfileClusterNode;
  /** 「定位到切片」：把选中的证据条目高亮并展开（不跳页，最少点击守则第 4 条）。 */
  onLocate: (evidenceId: string) => void;
  onFeedback: (evidenceId: string, action: 'accept' | 'edit' | 'reject' | 'uncertain') => void;
  locatedId: string | null;
}

/**
 * 证据链。
 *
 * 「点进原文」的能力边界（红线二：不许写假效果）：
 *   后端 `profilesApi` 目前只暴露 import 记录与切片 id，**没有**取回原文正文的接口，
 *   因此「打开原文件」做成**禁用态 + 明示原因**，而不是点下去弹个空窗口。
 *   「定位到切片」是真实可用的：在本页展开并高亮对应条目。
 */
export function EvidenceChain({ node, onLocate, onFeedback, locatedId }: EvidenceChainProps) {
  const meta = reviewMeta(node.review_status);
  const refs = node.evidence_refs.length > 0 ? node.evidence_refs : [node.id];
  const locator = node.locator ?? '自动推演切片（后端未给出定位串）';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-2)' }}>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}
      >
        <strong style={{ fontSize: 'var(--ui-fs-md)', color: 'var(--ui-ink-1)' }}>{node.label}</strong>
        <span className={`ui-badge ui-badge--${meta.tone}`}>
          <StatusIcon name={meta.icon} size={14} />
          {meta.label}
        </span>
        <span className="ui-spacer" />
        <span className="ui-badge ui-badge--neutral">
          置信度 {(node.confidence * 100).toFixed(0)}%
        </span>
      </div>

      <p style={{ margin: 0, fontSize: 'var(--ui-fs-sm)', color: 'var(--ui-ink-2)' }}>
        {node.description}
      </p>

      <ul
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--ui-s-1)',
        }}
      >
        {refs.map((ref) => (
          <li
            className="cabin-ni-evi"
            key={ref}
            data-testid={`profile-evi-${ref}`}
            style={
              locatedId === ref
                ? {
                    /* 薄荷高亮环走 color-mix 派生：原先写死 rgba(45,212,191,.55)
                       是裸色且不在令牌体系内（#2dd4bf 与 --ui-mint-400 #22d3ee
                       也不是同一个值）。alpha 由令牌派生后改薄荷深浅只需改一处。 */
                    borderColor: 'color-mix(in srgb, var(--ui-mint-400) 55%, transparent)',
                    boxShadow: '0 0 22px -8px var(--ui-glow-teal)',
                  }
                : undefined
            }
          >
            <span className="cabin-ni-evi-main">
              <LineIcon name="file" size={16} />
              <span className="cabin-ni-evi-locator">{locator}</span>
            </span>
            <span className="cabin-ni-evi-main">
              <LineIcon name="user" size={16} />
              发言主体 {node.speaker || '自述 / 语料'} · 类型 {node.claim_kind}
            </span>
            <span className="cabin-ni-evi-main">
              <LineIcon name="key" size={16} />
              切片证据 ID <code>{node.source_segment_id ?? ref}</code>
            </span>

            <span
              style={{
                display: 'flex',
                gap: 'var(--ui-s-2)',
                flexWrap: 'wrap',
                marginTop: 'var(--ui-s-1)',
              }}
            >
              <button
                type="button"
                className="ui-btn ui-btn--sm"
                data-testid={`profile-locate-${ref}`}
                onClick={() => onLocate(ref)}
              >
                <LineIcon name="target" size={16} />
                定位到切片
              </button>
              <button
                type="button"
                className="ui-btn ui-btn--sm"
                disabled
                title="后端尚未提供取回原文正文的接口（profilesApi 无对应端点），故本按钮保持禁用而不做假效果"
                onClick={() => undefined}
              >
                <LineIcon name="external" size={16} />
                打开原文件（待接线）
              </button>
              <button
                type="button"
                className="ui-btn ui-btn--sm ui-btn--primary"
                onClick={() => onFeedback(ref, 'accept')}
              >
                确认
              </button>
              <button
                type="button"
                className="ui-btn ui-btn--sm"
                onClick={() => onFeedback(ref, 'reject')}
              >
                否定
              </button>
              <button
                type="button"
                className="ui-btn ui-btn--sm"
                onClick={() => onFeedback(ref, 'uncertain')}
              >
                待验证
              </button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 「打开原文件」能力说明。
 *
 * 显式写在界面上而不是藏在 title 里：用户点了按钮发现没反应会以为是 bug，
 * 不如一开始就讲清楚「这条链路还没接线」。
 */
export function EvidenceCapabilityNote() {
  return (
    <p className="cabin-ni-evi" data-testid="profile-evi-capability-note">
      <span className="cabin-ni-evi-main">
        <LineIcon name="info" size={16} />
        证据原文取回尚未接线：后端 <code>/api/profiles</code> 目前只返回切片 id 与定位串，没有正文端点，因此「打开原文件」保持禁用。已可用的是「定位到切片」（在本页展开并高亮）与证据反馈（确认 / 否定 / 待验证）。
      </span>
    </p>
  );
}