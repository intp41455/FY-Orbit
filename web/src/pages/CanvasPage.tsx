import { useState } from 'react';
import { TeamDesigner } from '../components/agent-teams/TeamDesigner';
import { LegacyCanvas } from './canvas/LegacyCanvas';

/**
 * 协作画布（21 号布局）。
 *
 * 新增功能收在画布内：团队设计器是默认页签，05 号只读协作画布降为第二页签，
 * 其派发 / 交接 / 探针 / 事件能力全部保留，不因简洁而删功能。
 */
export function CanvasPage() {
  const [tab, setTab] = useState<'team' | 'legacy'>('team');

  return (
    <div>
      <div className="tabs-container" style={{ marginBottom: '1rem' }} role="tablist" aria-label="画布页签">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'team'}
          className={`btn btn-sm ${tab === 'team' ? 'active' : ''}`}
          onClick={() => setTab('team')}
        >
          内部团队 · 逐成员选模型
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'legacy'}
          className={`btn btn-sm ${tab === 'legacy' ? 'active' : ''}`}
          onClick={() => setTab('legacy')}
        >
          协作拓扑与派发记录（05）
        </button>
      </div>

      {tab === 'team' ? <TeamDesigner /> : <LegacyCanvas />}
    </div>
  );
}