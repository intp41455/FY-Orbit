import { useCallback, useEffect, useRef, useState } from 'react';
import { DslCanvas } from '../components/dsl/DslCanvas';
import { FlowEditor } from '../components/workflow/FlowEditor';
import { GeneratePanel } from '../components/workflow/GeneratePanel';
import { ModeSwitcher, TemplateStarter, useDslModes } from '../components/workflow/ModeSwitcher';
import type { DslDocument, DslMode } from '../api/dslCanvas';
import { takeDslHandoff } from '../api/dslHandoff';
import { useDslDraft } from '../hooks/useDslDraft';
import { LineIcon } from '../components/ui/LineIcon';
import { SaveStatusIndicator } from '../components/ui/SaveStatusIndicator';
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
/** 两份 DSL 文档是否**逐字段等价**（回传防循环的唯一判据）。 */
function sameDoc(a: DslDocument | null, b: DslDocument): boolean {
  return !!a && JSON.stringify(a) === JSON.stringify(b);
}

export function DslCanvasPage() {
  const [tab, setTab] = useState<'workshop' | 'text'>('workshop');
  const [mode, setMode] = useState<DslMode>('beginner');
  /**
   * **唯一状态源**：两个画布（FlowEditor / DslCanvas）看的是同一份文档。
   * 子组件不各自留一份——它们只在本地图变化时通过 `onDocChange` 回传。
   */
  const [doc, setDoc] = useState<DslDocument | null>(null);
  const [sourcePrompt, setSourcePrompt] = useState('');
  const overview = useDslModes();

  const current = overview.modes.find((m) => m.mode === mode);

  // 回传用的 ref：不在依赖里变化（否则每轮都造新回调，子组件 effect 会被
  // 无意义地重新触发），但永远读到最新的 doc。
  const docRef = useRef<DslDocument | null>(null);
  docRef.current = doc;

  /**
   * 画布回传的统一入口。
   *
   * 防回滚循环就在这道闸：子组件回传的文档若与当前状态源**内容等价**
   * （子组件被我们换过去的文档载入后，序列化结果与传进去的逐字段相同），
   * 就**不换引用**——两个画布因此不会「载入 → 回传 → 页面换引用 → 再载入」
   * 绕圈；只有真正的编辑（增删节点、改参数）才会换引用往下传。
   */
  const acceptDocChange = useCallback((next: DslDocument) => {
    if (sameDoc(docRef.current, next)) return;
    setDoc(next);
  }, []);

  /**
   * 远端草稿载入（首屏读回 / 冲突后「重新读取」）。
   *
   * 与 {@link acceptDocChange} 刻意分开：那条是「本机编辑回传」，靠内容
   * 等价闸防回环；这条是「用服务端版本覆盖本地」，**必须换引用**让两个画布
   * 真的把图换掉，且**不能**过 `sameDoc` 闸（本地与远端本来就不同）。
   */
  const acceptRemoteDoc = useCallback((next: DslDocument) => {
    setDoc({ version: next.version, nodes: [...next.nodes], edges: [...next.edges] });
  }, []);

  // 每次起手都换一个**新的对象引用**，两个画布才会真的把图换掉
  // （它们按引用变化载入，避免拖拽被回滚）。
  const applyTemplate = useCallback((next: DslDocument, templateId: string) => {
    setDoc({ version: next.version, nodes: [...next.nodes], edges: [...next.edges] });
    setSourcePrompt(`模板：${templateId}`);
  }, []);

  /**
   * G4-k 开箱模板→工坊通道的读端：挂载时消费一次性交接（读后即删）。
   * 有交接才换文档；无/坏交接保持原状（takeDslHandoff 内部已保证不抛错）。
   */
  useEffect(() => {
    const handoff = takeDslHandoff();
    if (handoff) {
      const d = handoff.doc;
      setDoc({ version: d.version, nodes: [...d.nodes], edges: [...d.edges] });
      setSourcePrompt(`模板：${handoff.source}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 挂载消费一次
  }, []);

  /**
   * N3 · 草稿持久化（唯一一条落盘通道）。
   *
   * 为什么挂在页面而不是两个画布各自挂：两个画布看的是同一份 `doc`，
   * 各自落盘会在乐观锁上自己把自己顶成 409。页面是唯一状态源，天然只存一次。
   * 定时器沿用 `useAutosave` 的 800ms / 5000ms，本层不另造。
   */
  const draft = useDslDraft({ doc, onLoad: acceptRemoteDoc });

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

      {/*
       * N3 冲突闸门：服务端草稿版本已被别的会话推进（HTTP 409）。
       * 此刻**自动保存已停**（`useDslDraft` 里 `enabled: false`），本地改动
       * 原样留在画布上，一条都没有被静默覆盖。给两个显式出口由用户决定。
       */}
      {draft.conflict ? (
        <div className="ui-panel ui-panel--flat ui-panel--pad" role="alert" data-testid="draft-conflict">
          <strong>草稿已被其他会话修改，需重新读取后再保存</strong>
          <p className="ui-hint" style={{ margin: '4px 0 8px' }}>
            本地改动仍在画布上，自动保存已暂停；直接继续保存会被服务端拒绝。
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" data-testid="draft-reload" onClick={() => { void draft.reload(); }}>
              重新读取服务端草稿
            </button>
            <button type="button" data-testid="draft-overwrite" onClick={() => { void draft.overwrite(); }}>
              以本地内容覆盖保存
            </button>
          </div>
        </div>
      ) : null}

      {draft.error ? (
        <div className="ui-panel ui-panel--flat ui-panel--pad" role="alert" data-testid="draft-error">
          {draft.error}
        </div>
      ) : null}

      {/*
       * 落盘状态如实透出：`state` 与 `storage` 都来自 `useAutosave`，
       * 没落过盘时是 `unlinked`（显示「未接入持久化」），不报假绿灯。
       */}
      <SaveStatusIndicator
        state={draft.state}
        savedAt={draft.auto.savedAt}
        location={draft.storage.location}
        bytes={draft.storage.bytes}
        freeBytes={draft.auto.freeBytes}
        storage={draft.storage.state}
        pendingChanges={draft.auto.pendingChanges}
        history={draft.auto.history}
        error={draft.auto.error}
        onRetry={() => { void draft.auto.retry(); }}
      />

      {/*
       * 两个画布**同时挂载**（N3 切片 2 / 修 G1）：页签只切 `hidden`，不再
       * 条件渲染卸载——否则切一次页签就把另一个画布的图丢掉。两边接的是同一个
       * `doc` 与同一条回传通道。
       */}
      <div className="cv-dsl-canvas-deck">
        <div data-testid="workshop-panel" hidden={tab !== 'workshop'}>
          {mode === 'beginner' && current && (
            <TemplateStarter
              templates={current.templates}
              defaultTemplate={current.default_template}
              onPick={applyTemplate}
            />
          )}
          <GeneratePanel
            onGenerated={(next, prompt) => { setDoc(next); setSourcePrompt(prompt); }}
          />
          <FlowEditor
            initialDoc={doc}
            sourcePrompt={sourcePrompt}
            mode={mode}
            onDocChange={acceptDocChange}
            visible={tab === 'workshop'}
          />
        </div>
        <div data-testid="text-panel" hidden={tab !== 'text'}>
          <DslCanvas initialDoc={doc} onDocChange={acceptDocChange} visible={tab === 'text'} mode={mode} />
        </div>
      </div>
    </div>
  );
}
