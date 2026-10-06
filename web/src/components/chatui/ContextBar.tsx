/**
 * 包 C · 智能上下文条（chatui 私有）
 * ---------------------------------------------------------------------------
 * 只读事实，不可移除：模型目录（modelsApi.catalog(probe=false)）与技能目录
 * （catalogApi.skills()）。未配置必须写「未配置」并原样展示原因，绝不粉饰。
 * 引用 chip 一键移除；「全部清除」只清引用并本次会话折叠，
 * 仅 localStorage 记 collapsed 布尔，不写永久屏蔽标记。
 * 三项全无时整条不渲染（由调用方判断），不留空壳。
 */
import type { ModelCatalogSummary } from '../../api/models';
import type { KBDocument } from '../../api/knowledge';
import type { SkillInfo } from '../../api/types';
import { LineIcon } from '../ui/LineIcon';
import { StatusTag } from './StatusTag';
import { Skeleton } from './Skeleton';

export interface ContextBarProps {
  model: ModelCatalogSummary | null;
  modelState: 'loading' | 'ready' | 'failed';
  modelError?: string;
  skills: SkillInfo[] | null;
  skillsState: 'loading' | 'ready' | 'failed';
  skillsError?: string;
  refs: KBDocument[];
  onRemoveRef: (docId: string) => void;
  onClearRefs: () => void;
  collapsed: boolean;
  onSetCollapsed: (v: boolean) => void;
}

export function ContextBar({
  model,
  modelState,
  modelError,
  skills,
  skillsState,
  skillsError,
  refs,
  onRemoveRef,
  onClearRefs,
  collapsed,
  onSetCollapsed,
}: ContextBarProps) {
  const count = (model ? 1 : 0) + (skills ? 1 : 0) + refs.length;

  if (collapsed) {
    return (
      <div className="chatui-ctx-recall">
        <button type="button" className="ui-chip chatui-ctx-recall-btn" onClick={() => onSetCollapsed(false)}>
          <LineIcon name="sparkles" size={16} />
          上下文（{count}）
        </button>
        <span className="chatui-sr-only">上下文条已折叠，可展开查看模型、技能目录与引用。</span>
      </div>
    );
  }

  const modelReason = model
    ? [model.config_error, ...(model.route_errors ?? [])].filter((s) => Boolean(s && s.trim())).join('；')
    : '';

  return (
    <div className="chatui-ctxbar" role="group" aria-label="智能上下文">
      <div className="chatui-ctxitem">
        <span className="chatui-ctxlabel">
          <LineIcon name="cube" size={14} />
          模型
        </span>
        {modelState === 'loading' ? (
          <Skeleton rows={1} height={18} label="正在读取模型目录" className="chatui-ctx-skel" />
        ) : modelState === 'failed' ? (
          <StatusTag kind="failed" text="获取失败" detail={modelError} />
        ) : model?.configured ? (
          <StatusTag kind="complete" text="已配置" detail={model.model_name || undefined} />
        ) : (
          <StatusTag kind="waiting" text="未配置" detail={modelReason || '后端未返回配置原因'} />
        )}
      </div>

      <div className="chatui-ctxitem">
        <span className="chatui-ctxlabel">
          <LineIcon name="skills" size={14} />
          技能目录
        </span>
        {skillsState === 'loading' ? (
          <Skeleton rows={1} height={18} label="正在读取技能目录" className="chatui-ctx-skel" />
        ) : skillsState === 'failed' ? (
          <StatusTag kind="failed" text="获取失败" detail={skillsError} />
        ) : (
          <span className="chatui-ctxvalue">
            {skills?.length ?? 0} 个
            <a className="chatui-ctxlink" href="/skills">查看 →</a>
          </span>
        )}
      </div>

      <div className="chatui-ctxitem chatui-ctxitem--refs">
        <span className="chatui-ctxlabel">
          <LineIcon name="link" size={14} />
          引用（{refs.length}）
        </span>
        <div className="chatui-refchips">
          {refs.length === 0 ? (
            <span className="chatui-ctxvalue chatui-ctxvalue--muted">无（在输入框输入 @ 可引用知识库文档）</span>
          ) : (
            refs.map((d) => (
              <span key={d.id} className="chatui-refchip" title={d.name}>
                <span className="chatui-refchip-text">@《{d.name}》</span>
                <button
                  type="button"
                  className="chatui-refchip-x"
                  aria-label={`移除引用 ${d.name}`}
                  onClick={() => onRemoveRef(d.id)}
                >
                  ×
                </button>
              </span>
            ))
          )}
        </div>
      </div>

      <div className="chatui-ctxactions">
        {refs.length > 0 ? (
          <button type="button" className="ui-btn ui-btn--sm" onClick={onClearRefs}>
            <LineIcon name="close" size={16} /> 全部清除
          </button>
        ) : null}
        <button
          type="button"
          className="ui-btn ui-btn--ghost ui-btn--sm"
          onClick={() => onSetCollapsed(true)}
          title="折叠上下文条（本次会话）"
        >
          <LineIcon name="chevronDown" size={16} /> 折叠
        </button>
      </div>
    </div>
  );
}
