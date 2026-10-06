import { useState } from 'react';
import { DslCanvas } from '../components/dsl/DslCanvas';
import { FlowEditor } from '../components/workflow/FlowEditor';
import { GeneratePanel } from '../components/workflow/GeneratePanel';
import type { DslDocument } from '../api/dslCanvas';
import { LineIcon } from '../components/ui/LineIcon';
import '../styles/pages/canvas.css';

/**
 * 工作流工坊页面壳（包 B 视觉层）。
 *
 * 业务组件（FlowEditor / GeneratePanel / DslCanvas）保持原样，
 * 本壳只提供：压暗画布区、玻璃面板、九档状态色诊断的视觉氛围。
 */
export function DslCanvasPage() {
  const [tab, setTab] = useState<'workshop' | 'text'>('workshop');
  const [generated, setGenerated] = useState<DslDocument | null>(null);
  const [sourcePrompt, setSourcePrompt] = useState('');

  return (
    <div className="cv-dsl-shell">
      <div className="ui-panel ui-panel--flat ui-panel--pad" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <LineIcon name="flow" size={22} />
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--ui-fs-xl)', color: 'var(--ui-ink-1)' }}>工作流工坊</h2>
          <p className="ui-hint" style={{ margin: '2px 0 0' }}>
            一句话生成 → 拖拽调整 → 代码与脚本导出，数据互通。
            节点为受限动词，只能从面板拖入；诊断用九档状态色定位。
          </p>
        </div>
      </div>

      <div className="ui-tabs" role="tablist" aria-label="工作流工坊页签">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'workshop'}
          className="ui-tab"
          onClick={() => setTab('workshop')}
          data-testid="tab-workshop"
        >
          工坊模式 · 生成 + 拖拽 + 导出
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'text'}
          className="ui-tab"
          onClick={() => setTab('text')}
          data-testid="tab-text"
        >
          DSL 文本模式 · 原有画布
        </button>
      </div>

      <div className="cv-dsl-canvas-deck">
        {tab === 'workshop' ? (
          <div data-testid="workshop-panel">
            <GeneratePanel
              onGenerated={(doc, prompt) => { setGenerated(doc); setSourcePrompt(prompt); }}
            />
            <FlowEditor initialDoc={generated} sourcePrompt={sourcePrompt} />
          </div>
        ) : (
          <div data-testid="text-panel">
            <DslCanvas />
          </div>
        )}
      </div>
    </div>
  );
}
