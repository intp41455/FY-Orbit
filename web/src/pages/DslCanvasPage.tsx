import { useState } from 'react';
import { DslCanvas } from '../components/dsl/DslCanvas';
import { FlowEditor } from '../components/workflow/FlowEditor';
import { GeneratePanel } from '../components/workflow/GeneratePanel';
import type { DslDocument } from '../api/dslCanvas';

/**
 * 工作流工坊页面壳（W5）。
 *
 * 两个页签**同源**（任务§2.4）：
 *  - 「工坊模式」= 一句话生成 + 拖拽画布 + 代码视图 + 导出脚本（三视图合一）；
 *  - 「DSL 文本模式」= 原有 P1-18 画布，功能**保留不删**。
 * 两页签编辑的是同一个 DSL 语义模型，切换不丢数据（各自持有组件状态）。
 */
export function DslCanvasPage() {
  const [tab, setTab] = useState<'workshop' | 'text'>('workshop');
  // 生成器产出的文档，作为工坊模式画布的初始输入。
  const [generated, setGenerated] = useState<DslDocument | null>(null);
  const [sourcePrompt, setSourcePrompt] = useState('');

  return (
    <>
      <div className="page-head"><h2>工作流工坊</h2></div>
      <div className="muted" style={{ marginBottom: 8 }}>
        三层是同一个 Agent 的不同视图：一句话生成 → 拖拽调整 → 代码与脚本导出，数据互通。
        布局坐标与 DSL 模型分离：DSL 只包含节点（受限动词集）与数据流边。
      </div>

      <div className="tabs-container" style={{ marginBottom: '1rem' }} role="tablist" aria-label="工作流工坊页签">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'workshop'}
          className={`btn btn-sm ${tab === 'workshop' ? 'active' : ''}`}
          onClick={() => setTab('workshop')}
          data-testid="tab-workshop"
        >
          工坊模式 · 生成 + 拖拽 + 导出
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'text'}
          className={`btn btn-sm ${tab === 'text' ? 'active' : ''}`}
          onClick={() => setTab('text')}
          data-testid="tab-text"
        >
          DSL 文本模式 · 原有画布
        </button>
      </div>

      {tab === 'workshop' ? (
        <div data-testid="workshop-panel">
          <GeneratePanel
            onGenerated={(doc, prompt) => {
              setGenerated(doc);
              setSourcePrompt(prompt);
            }}
          />
          <FlowEditor initialDoc={generated} sourcePrompt={sourcePrompt} />
        </div>
      ) : (
        <div data-testid="text-panel">
          <DslCanvas />
        </div>
      )}
    </>
  );
}