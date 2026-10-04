/**
 * W5 工作流工坊 · 左侧节点面板。
 *
 * 节点类型**动态取自后端** `GET /api/dsl-canvas/schema`（受限动词集是唯一真源），
 * 拉取失败时如实回落到本地常量并保持可用——不静默展示一个「看起来能动」的
 * 假面板。
 *
 * 交互：点选即添加（键盘可达），也支持 HTML5 拖拽到画布（``text/fy-flow-node``）。
 */
import { useCallback, useState } from 'react';
import type { DslNodeType } from '../../api/dslCanvas';

const TYPE_LABELS: Record<DslNodeType, string> = {
  input: '输入 input · 读数据',
  transform: '变换 transform · map/filter/template',
  output: '输出 output · json/text',
};

const TYPE_HINTS: Record<DslNodeType, string> = {
  input: 'literal（任意 JSON）或 text_lines（按行拆文本）',
  transform: '逐条映射 / 条件过滤 / 模板插值',
  output: 'json 原样输出，或 text 按行拼成文本',
};

export interface NodePaletteProps {
  /** 来自后端 schema 的节点类型（已过滤到受限集内）。 */
  types: DslNodeType[];
  onAdd: (type: DslNodeType) => void;
  /** 拖拽落到画布时的落点回调（type + 画布坐标）。 */
  onDropNode: (type: DslNodeType, at: { x: number; y: number }) => void;
}

export function NodePalette({ types, onAdd, onDropNode }: NodePaletteProps) {
  const [over, setOver] = useState(false);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const type = e.dataTransfer.getData('text/fy-flow-node') as DslNodeType;
    if (!type) return;
    onDropNode(type, { x: e.clientX, y: e.clientY });
  }, [onDropNode]);

  return (
    <div
      className={`fy-flow-palette${over ? ' is-drop' : ''}`}
      data-testid="flow-palette"
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <strong className="fy-flow-palette-title">节点面板</strong>
      <p className="muted fy-flow-palette-note">
        受限动词集——这是平台的**全部**能力，超出集合的需求会被诚实拒绝。
      </p>
      {types.map((t) => (
        <button
          key={t}
          type="button"
          className={`fy-flow-palette-item type-${t}`}
          draggable
          onDragStart={(e) => e.dataTransfer.setData('text/fy-flow-node', t)}
          onClick={() => onAdd(t)}
          data-testid={`flow-palette-${t}`}
          title={TYPE_HINTS[t]}
        >
          <span className="fy-flow-palette-label">{TYPE_LABELS[t]}</span>
          <span className="fy-flow-palette-hint">{TYPE_HINTS[t]}</span>
        </button>
      ))}
      <p className="muted fy-flow-palette-note">
        点选即添加到画布；也可以把卡片拖进画布。
      </p>
    </div>
  );
}