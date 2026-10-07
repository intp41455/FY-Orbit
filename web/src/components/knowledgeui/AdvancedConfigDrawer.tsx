/**
 * 包 E 私有组件 · 高级配置抽屉（⚙ 右上角常驻）
 * ---------------------------------------------------------------------------
 * ⚠⚠ 本抽屉最重要的一条：逐项标注「已接线 / 待接线」⚠⚠
 *
 * 任务书 §3.4 明确要求：当前 RAG 管线（W3 `knowledge-source-adapter-design`）
 * 的真实可调项以真实 API 为准；**没有真实 API 支持的项要标成"待接线"并禁用，
 * 不许写假的效果**（总纲红线二）。
 *
 * 所以每一行都带 `wired: boolean`：
 *   - wired=true  → 点击立即改变当前视图的真实行为（渲染系数、过滤、显示项）；
 *   - wired=false → disabled + 明写「后端无对应配置项」，并给出为什么。
 *
 * 抽屉浮在星图之上，不推挤布局：position: fixed（见 knowledge.css .fy-plugin-detail
 * 同款实现），因此切开关时星图不会重排。
 */
import { useEffect, useRef, useState } from 'react';
import { LineIcon, type LineIconName } from '../../components/ui/LineIcon';
import { KnowledgeNiIcon, type KnowledgeNiIconName } from './KnowledgeNiIcon';
import { useBase } from '../../hooks/useAutosave';

export type KnConfig = {
  // 渲染（真接线：直接改变 canvas 绘制）
  nodeSizeScale: number;
  edgeOpacity: number;
  glowStrength: number;
  showWeakEdges: boolean;
  // 关联（渲染层过滤；后端无阈值配置项 → 标待接线）
  relationThreshold: number;
  showSequenceEdges: boolean;
  showCohitEdges: boolean;
  bidirectionalLinks: boolean;
  // 切片（后端切片策略不可由前端改 → 待接线）
  chunkSize: number;
  chunkOverlap: number;
  chunkStrategy: 'paragraph' | 'semantic' | 'token';
  // 向量（后端无配置端点 → 待接线）
  embeddingModel: string;
  embeddingLocalOnly: boolean;
  embeddingDim: number;
  // 导入第三方双链笔记库（无端点 → 待接线）
  importThirdParty: boolean;
  webglRendering: boolean;
};

export const DEFAULT_KN_CONFIG: KnConfig = {
  nodeSizeScale: 1,
  edgeOpacity: 1,
  glowStrength: 1,
  showWeakEdges: true,
  relationThreshold: 0.3,
  showSequenceEdges: true,
  showCohitEdges: true,
  bidirectionalLinks: false,
  chunkSize: 800,
  chunkOverlap: 120,
  chunkStrategy: 'paragraph',
  embeddingModel: '',
  embeddingLocalOnly: true,
  embeddingDim: 0,
  importThirdParty: false,
  webglRendering: false,
};

export interface AdvancedConfigDrawerProps {
  open: boolean;
  onClose: () => void;
  config: KnConfig;
  onChange: (next: KnConfig) => void;
}

type GroupId = 'render' | 'relation' | 'chunk' | 'vector' | 'link' | 'import';

interface Section {
  id: GroupId;
  label: string;
  icon: LineIconName | KnowledgeNiIconName;
}

const SECTIONS: Section[] = [
  { id: 'render', label: '渲染', icon: 'sparkles' },
  { id: 'relation', label: '关联', icon: 'network' },
  { id: 'chunk', label: '切片', icon: 'file' },
  { id: 'vector', label: '向量', icon: 'database' },
  { id: 'link', label: '链接', icon: 'link' },
  { id: 'import', label: '导入', icon: 'upload' },
];

function SectionIcon({ name, size = 16 }: { name: LineIconName | KnowledgeNiIconName; size?: number }) {
  const local: KnowledgeNiIconName[] = ['snowflake', 'cube', 'compass'];
  if (local.includes(name as KnowledgeNiIconName)) {
    return <KnowledgeNiIcon name={name as KnowledgeNiIconName} size={size} />;
  }
  return <LineIcon name={name as LineIconName} size={size} />;
}

/**
 * 「待接线」徽标。
 *
 * 为什么单独做成醒目样式：用户最容易被假开关坑到。
 * 一个明确写着「待接线」的琥珀徽标，比一个灰掉的控件诚实得多。
 */
function PendingNote({ why }: { why: string }) {
  return (
    <span className="ui-badge ui-badge--waiting" data-testid="kn-cfg-pending">
      {/* ⚠ LineIcon 集里没有 clock（总纲 §3 要求用），取本地补录集 */}
      <KnowledgeNiIcon name="clock" size={14} />
      待接线
      <span className="ui-hint" style={{ marginLeft: 6 }}>
        {why}
      </span>
    </span>
  );
}


function Row({
  label,
  hint,
  wired,
  why,
  children,
}: {
  label: string;
  hint: string;
  wired: boolean;
  why?: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset
      className="kn-cfg-row"
      data-wired={wired ? 'true' : 'false'}
      data-testid={`kn-cfg-${label}`}
      disabled={!wired}
    >
      <legend className="kn-cfg-label">
        <SectionIcon name={wired ? 'check' : 'alert'} size={14} />
        {label}
      </legend>
      <p className="ui-hint">{hint}</p>
      {children}
      {!wired && why && <PendingNote why={why} />}
    </fieldset>
  );
}

export function AdvancedConfigDrawer({ open, onClose, config, onChange }: AdvancedConfigDrawerProps) {
  useBase({ surface: 'web/src/components/knowledgeui/AdvancedConfigDrawer' });
  const [section, setSection] = useState<GroupId>('render');
  const panelRef = useRef<HTMLDivElement | null>(null);
  const firstRef = useRef<HTMLButtonElement | null>(null);

  /** Esc 关闭 + 焦点移入面板（抽屉必须键盘可达）。 */
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    firstRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const set = <K extends keyof KnConfig>(key: K, value: KnConfig[K]) =>
    onChange({ ...config, [key]: value });

  return (
    <>
      {/*
        遮罩用 `<button>` 而不是 `<div onClick>`：
        div 承载点击对键盘与读屏用户完全不可达（Web Interface Guidelines 反模式）。
        button 天然可聚焦、可 Enter/Space 触发、语义也正确（"关闭弹窗"）。
      */}
      <button
        type="button"
        className="ui-overlay"
        aria-label="关闭高级配置"
        onClick={onClose}
        data-testid="kn-cfg-overlay"
      />
      <div
        ref={panelRef}
        className="fy-plugin-detail kn-cfg"
        role="dialog"
        aria-modal="true"
        aria-label="知识星图高级配置"
        data-testid="kn-cfg-drawer"
      >
        <header className="ui-drawer-hd" style={{ padding: 0, borderBottom: '1px solid var(--ui-line-2)' }}>
          <LineIcon name="settings" size={20} />
          <h2 className="ui-panel-title" style={{ fontSize: 'var(--ui-fs-lg)' }}>
            高级配置
          </h2>
          <span className="ui-spacer" />
          <button
            ref={firstRef}
            type="button"
            className="ui-btn ui-btn--icon"
            aria-label="关闭高级配置"
            onClick={onClose}
            data-testid="kn-cfg-close"
          >
            <LineIcon name="close" size={18} />
          </button>
        </header>

        <p className="ui-hint" style={{ margin: 0 }}>
          小白看不见也不影响主流程。下表逐项标注<strong>已接线 / 待接线</strong>：
          「待接线」表示后端当前没有对应配置端点，按钮保持禁用而不做假效果。
        </p>

        <nav className="kn-cfg-nav" aria-label="配置分组">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className="ui-chip"
              aria-pressed={section === s.id}
              data-testid={`kn-cfg-tab-${s.id}`}
              onClick={() => setSection(s.id)}
            >
              <SectionIcon name={s.icon} size={16} />
              {s.label}
            </button>
          ))}
        </nav>

        <div className="ui-drawer-bd" style={{ padding: 0, display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-3)' }}>
          {section === 'render' && (
            <>
              <Row label="节点大小系数" hint="直接改变星图节点绘制半径倍率（真实生效）" wired>
                <input
                  type="range"
                  min={0.5}
                  max={2}
                  step={0.1}
                  value={config.nodeSizeScale}
                  aria-label="节点大小系数"
                  data-testid="kn-cfg-node-size"
                  onChange={(e) => set('nodeSizeScale', Number(e.target.value))}
                />
                <output className="kn-cfg-out">{config.nodeSizeScale.toFixed(1)}×</output>
              </Row>
              <Row label="连线透明度" hint="直接改变连线绘制 alpha（真实生效）" wired>
                <input
                  type="range"
                  min={0.1}
                  max={1}
                  step={0.05}
                  value={config.edgeOpacity}
                  aria-label="连线透明度"
                  data-testid="kn-cfg-edge-opacity"
                  onChange={(e) => set('edgeOpacity', Number(e.target.value))}
                />
                <output className="kn-cfg-out">{config.edgeOpacity.toFixed(2)}</output>
              </Row>
              <Row label="发光强度" hint="只影响选中节点的外发光半径（真实生效）" wired>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={config.glowStrength}
                  aria-label="发光强度"
                  data-testid="kn-cfg-glow"
                  onChange={(e) => set('glowStrength', Number(e.target.value))}
                />
                <output className="kn-cfg-out">{config.glowStrength.toFixed(1)}×</output>
              </Row>
              <Row label="显示弱关联" hint="关闭后只画被检索命中过的切片之间的连线（真实生效）" wired>
                <input
                  type="checkbox"
                  checked={config.showWeakEdges}
                  aria-label="显示弱关联"
                  data-testid="kn-cfg-weak"
                  onChange={(e) => set('showWeakEdges', e.target.checked)}
                />
              </Row>
              <Row
                label="WebGL 渲染"
                hint="节点上限再高时理论上更快"
                wired={false}
                why="本包按任务书 §3.5 用 canvas 2D + 手写透视实现；/cabin 的 pixi 已占用 WebGL 上下文，再开一个会互相拖帧。"
              >
                <input type="checkbox" checked={false} readOnly aria-label="WebGL 渲染（待接线）" />
              </Row>
            </>
          )}

          {section === 'relation' && (
            <>
              <Row
                label="关联阈值"
                hint="低于该强度的连线不画"
                wired={false}
                why="后端没有返回关联强度字段（仅顺序边与共同命中边是推导出来的），阈值目前只能作用于前端推导结果。"
              >
                <input type="range" min={0} max={1} step={0.05} value={config.relationThreshold} readOnly aria-label="关联阈值（待接线）" />
              </Row>
              <Row label="层级从属（顺序）连线" hint="同一文档内按 seq 相邻的切片连线" wired>
                <input
                  type="checkbox"
                  checked={config.showSequenceEdges}
                  aria-label="层级从属连线"
                  data-testid="kn-cfg-seq"
                  onChange={(e) => set('showSequenceEdges', e.target.checked)}
                />
              </Row>
              <Row label="引用关联（共同命中）连线" hint="同一次检索里一起出现的切片连线" wired>
                <input
                  type="checkbox"
                  checked={config.showCohitEdges}
                  aria-label="共同命中连线"
                  data-testid="kn-cfg-cohit"
                  onChange={(e) => set('showCohitEdges', e.target.checked)}
                />
              </Row>
              <Row
                label="语义双向链接"
                hint="像 Obsidian 那样手动维护的双向链接"
                wired={false}
                why="后端 /api/kb 没有双向链接的读写端点；画出来就是编的。"
              >
                <input type="checkbox" checked={false} readOnly aria-label="语义双向链接（待接线）" />
              </Row>
            </>
          )}

          {section === 'chunk' && (
            <>
              <Row label="切片大小" hint="重新导入时生效" wired={false} why="切片策略由后端 importer 决定，前端改它不会生效；需后端暴露配置项。">
                <input type="number" min={128} max={4000} step={64} value={config.chunkSize} readOnly aria-label="切片大小（待接线）" />
              </Row>
              <Row label="重叠长度" hint="相邻切片重叠字数" wired={false} why="同上，属后端 importer 配置。">
                <input type="number" min={0} max={1000} step={20} value={config.chunkOverlap} readOnly aria-label="重叠长度（待接线）" />
              </Row>
              <Row label="切片方式" hint="段落 / 语义 / Token" wired={false} why="同上，属后端 importer 配置。">
                <select
                  value={config.chunkStrategy}
                  disabled
                  aria-label="切片方式（待接线）"
                  data-testid="kn-cfg-chunk-strategy"
                >
                  <option value="paragraph">段落</option>
                  <option value="semantic">语义</option>
                  <option value="token">Token</option>
                </select>
              </Row>
            </>
          )}

          {section === 'vector' && (
            <>
              <Row label="Embedding 模型" hint="决定向量空间" wired={false} why="后端 /api/kb 未暴露 embedding 配置端点，检索在后端内部完成。">
                <input type="text" placeholder="由后端配置" value="" readOnly aria-label="Embedding 模型（待接线）" />
              </Row>
              <Row label="仅本地 / 外部" hint="数据不出本机" wired={false} why="同上，无前端可改项。">
                <input type="checkbox" checked readOnly aria-label="仅本地（待接线）" />
              </Row>
              <Row label="向量维度" hint="展示用" wired={false} why="后端不回传维度。">
                <input type="number" value={0} readOnly aria-label="向量维度（待接线）" />
              </Row>
            </>
          )}

          {section === 'link' && (
            <>
              <Row label="手动添加双向链接" hint="在两个切片之间建立链接" wired={false} why="后端无写入端点。">
                <input type="text" placeholder="选两个切片后建立链接" readOnly aria-label="手动添加双向链接（待接线）" />
              </Row>
              <Row label="删除双向链接" hint="解除已有链接" wired={false} why="后端无删除端点。">
                <input type="text" placeholder="选择要解除的链接" readOnly aria-label="删除双向链接（待接线）" />
              </Row>
              <Row label="自定义关联类型标签" hint="如「竞品」「参考资料」" wired={false} why="后端没有关系类型字段。">
                <input type="text" placeholder="新增标签" readOnly aria-label="自定义关联类型标签（待接线）" />
              </Row>
            </>
          )}

          {section === 'import' && (
            <>
              <Row
                label="导入第三方双链笔记库"
                hint="如 Obsidian / Logseq 仓库"
                wired={false}
                why="后端只支持上传单个文件（.md/.txt/.pdf/.docx），没有仓库级导入端点。"
              >
                <input type="checkbox" checked={false} readOnly aria-label="导入第三方双链笔记库（待接线）" />
              </Row>
              <p className="ui-hint">
                现在可用的导入方式：拖入 .md / .txt / .pdf / .docx 文件，或在「文档源」标签页用适配器同步。
              </p>
            </>
          )}
        </div>
      </div>
    </>
  );
}