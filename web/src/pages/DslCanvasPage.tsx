import { useCallback, useState } from 'react';
import { DslCanvas } from '../components/dsl/DslCanvas';
import { FlowEditor } from '../components/workflow/FlowEditor';
import { GeneratePanel } from '../components/workflow/GeneratePanel';
import { ModeSwitcher, TemplateStarter, useDslModes } from '../components/workflow/ModeSwitcher';
import type { DslDocument, DslMode } from '../api/dslCanvas';
import { LineIcon } from '../components/ui/LineIcon';
import '../styles/pages/canvas.css';

/**
 * 工作流工坊页面壳（包 B 视觉层 + A-三重模式-01/04 模式壳）。
 *
 * 业务组件（FlowEditor / GeneratePanel / DslCanvas）保持原样，
 * 本壳提供三件事：
 *
 * 1. 压暗画布区、玻璃面板、九档状态色诊断的视觉氛围；
 * 2. **三重模式切换**（小白 / 技术 / 企业）——数据来自 `GET /api/dsl/modes`，
 *    三种模式共用同一张图与同一套 IR；
 * 3. **小白起手**：默认落在 `beginner`，一进来就有后端给的起手模板可点，
 *    拼完直接「运行」跑到底（模板起手 → 装配 → 运行）。
 *
 * 诚实边界：本壳自己不含任何编辑控件（模式与模板都是**导航按钮**，不是
 * 可编辑输入），编辑面一律在已接入基座的子组件里——这也是它能作为
 * 「纯展示页」走豁免的前提。
 */
export function DslCanvasPage() {
  const [tab, setTab] = useState<'workshop' | 'text'>('workshop');
  const [mode, setMode] = useState<DslMode>('beginner');
  const [generated, setGenerated] = useState<DslDocument | null>(null);
  const [sourcePrompt, setSourcePrompt] = useState('');
  const overview = useDslModes();

  const current = overview.modes.find((m) => m.mode === mode);

  // 每次起手都换一个**新的对象引用**，FlowEditor 才会真的把图画布换掉
  // （它按引用变化载入，避免拖拽被回滚）。
  const applyTemplate = useCallback((doc: DslDocument, templateId: string) => {
    setGenerated({ version: doc.version, nodes: [...doc.nodes], edges: [...doc.edges] });
    setSourcePrompt(`模板：${templateId}`);
  }, []);

  return (
    <div className="cv-dsl-shell">
      <div className="ui-panel ui-panel--flat ui-panel--pad" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <LineIcon name="flow" size={22} />
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 'var(--ui-fs-xl)', color: 'var(--ui-ink-1)' }}>工作流工坊</h2>
          <p className="ui-hint" style={{ margin: '2px 0 0' }}>
            模板起手 → 拖拽调整 → 运行，或导出脚本。三种创作模式是同一套图，
            节点为受限集，只能从面板拖入；诊断用九档状态色定位。
          </p>
        </div>
      </div>

      <ModeSwitcher mode={mode} onSelect={setMode} overview={overview} />

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
            {mode === 'beginner' && current && (
              <TemplateStarter
                templates={current.templates}
                defaultTemplate={current.default_template}
                onPick={applyTemplate}
              />
            )}
            <GeneratePanel
              onGenerated={(doc, prompt) => { setGenerated(doc); setSourcePrompt(prompt); }}
            />
            <FlowEditor initialDoc={generated} sourcePrompt={sourcePrompt} mode={mode} />
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
