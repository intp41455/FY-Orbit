import { useState } from 'react';
import { TeamDesigner } from '../components/agent-teams/TeamDesigner';
import { CanvasTeamView } from '../components/canvasui/CanvasTeamView';
import { LegacyCanvas } from './canvas/LegacyCanvas';
import '../styles/pages/canvas.css';

/**
 * 协作画布页壳（包 B 视觉层）。
 *
 * 默认渲染新视觉 CanvasTeamView：压暗画布 + 水平三栏拓扑（18%/50%/82%）+ 浮层抽屉。
 * 新视图保留 e2e/ui-team.spec.ts 全部关键选择器与文字（.tabs-container / .team-map /
 * complementary[name=节点设置] / 按钮文案 / label）。
 * 旧 TeamDesigner 与 05 号协作画布保留为页签，功能不删。
 *
 * 页签用 role="group" + aria-pressed，而非 role="tab"：这三个页签切换的是整个主区域，
 * 没有 aria-controls、没有对应 role="tabpanel"、也没有 roving tabindex，
 * 按 WAI-ARIA 属于「tab 角色误用」。改成按钮组既修掉这个 a11y 违规，
 * 也让 e2e/ui-team.spec.ts 的 selectTeam()（getByRole('button', {name}））成立。
 */
export function CanvasPage() {
  const [tab, setTab] = useState<'new' | 'designer' | 'legacy'>('new');

  return (
    <div>
      <div
        className="tabs-container"
        role="group"
        aria-label="画布页签"
        style={{ marginBottom: '12px' }}
      >
        <button
          type="button"
          aria-pressed={tab === 'new'}
          className={`btn btn-sm ${tab === 'new' ? 'active' : ''}`}
          onClick={() => setTab('new')}
        >
          团队设计器（新画布）
        </button>
        <button
          type="button"
          aria-pressed={tab === 'designer'}
          className={`btn btn-sm ${tab === 'designer' ? 'active' : ''}`}
          onClick={() => setTab('designer')}
        >
          旧设计器（保留）
        </button>
        <button
          type="button"
          aria-pressed={tab === 'legacy'}
          className={`btn btn-sm ${tab === 'legacy' ? 'active' : ''}`}
          onClick={() => setTab('legacy')}
        >
          协作拓扑与派发记录（05）
        </button>
      </div>

      {tab === 'new' && <CanvasTeamView />}
      {tab === 'designer' && <div className="cv-legacy-root"><TeamDesigner /></div>}
      {tab === 'legacy' && <div className="cv-legacy-root"><LegacyCanvas /></div>}
    </div>
  );
}
