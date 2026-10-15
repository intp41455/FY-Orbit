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
  llm: 'LLM 调用 · 调用大语言模型',
  knowledge_retrieval: '知识检索 · 从知识库检索信息',
  question_classifier: '问题分类 · 对问题进行分类',
  parameter_extractor: '参数抽取 · 从文本中抽取结构化参数',
  iteration: '迭代 · 对列表中的每项执行子流程',
  loop: '循环 · 重复执行子流程直至条件满足',
  variable_aggregator: '变量汇聚 · 合并多个上游变量',
  template: '模板插值 · 独立的模板节点',
  http_request: 'HTTP 请求 · 发送 HTTP 请求',
  code: '代码执行 · 在沙箱中执行代码',
  tool: '工具调用 · 调用已注册的工具',
  human_input: '人工输入 · 等待人工提供输入',
  trigger: '触发器 · 工作流的入口节点',
  parallel: '并行分支 · 同时扇出多个下游分支',
  subgraph: '子图引用 · 引用一个已命名的子图',
  join: '会合 · 收多路上游为数组/首尾值',
  delay: '延时 · 等待 N 秒后原样透传',
};

const TYPE_HINTS: Record<DslNodeType, string> = {
  input: 'literal（任意 JSON）或 text_lines（按行拆文本）',
  transform: '逐条映射 / 条件过滤 / 模板插值',
  output: 'json 原样输出，或 text 按行拼成文本',
  llm: '需要模型名称和提示词',
  knowledge_retrieval: '需要查询语句，可指定返回数量和检索模式',
  question_classifier: '需要提供分类列表和模型名称',
  parameter_extractor: '需要定义要抽取的字段列表和模型名称',
  iteration: '需要提供一个子流程作为迭代体',
  loop: '需要提供一个子流程作为循环体及最大迭代次数',
  variable_aggregator: '可选择先非空或后非空的汇聚策略',
  template: '需要一个模板字符串，支持 {field} 插值',
  http_request: '需要 URL 地址，支持 GET/POST 等方法',
  code: '需要 Python 代码，会在沙箱中执行',
  tool: '需要工具名称，可选传入参数',
  human_input: '需要提示用户输入的文本',
  trigger: '需要指定触发类型：手动/对话/定时/Webhook',
  parallel: '需要提供分支列表，可选指定最大并发数',
  subgraph: '需要提供被引用子图的名字',
  join: '选择会合模式（list 收全部 / first|last 取值）',
  delay: '等待秒数（0..300，0 即直通）',
};

/**
 * 分组收纳（展示层分组，不另维护类型清单）。
 *
 * 20 类一屏放不下：按「基础 / Agent / 流程 / 外部 / 拓扑」收到原生
 * `<details>` 分组里（文档流内展开，把下面内容自然下推，**不遮挡**其它面板）。
 * 未登录分组的新类型自动落进「其他」（扩展位置，不丢类型）。
 * 默认只展开「基础」：首屏只见 3 个，其余点组名展开。
 */
const PALETTE_GROUPS: ReadonlyArray<{ title: string; types: DslNodeType[] }> = [
  { title: '基础', types: ['input', 'transform', 'output'] },
  { title: 'Agent 与知识', types: ['llm', 'knowledge_retrieval', 'question_classifier', 'parameter_extractor'] },
  { title: '流程控制', types: ['iteration', 'loop', 'variable_aggregator', 'template', 'join', 'delay'] },
  { title: '外部与人工', types: ['http_request', 'code', 'tool', 'human_input', 'trigger'] },
  { title: '拓扑', types: ['parallel', 'subgraph'] },
];

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

  const inTypes = new Set(types);
  const groups = PALETTE_GROUPS.map((g) => ({
    ...g,
    items: g.types.filter((t) => inTypes.has(t)),
  }));
  // 扩展位置：分组表里没写的新类型不丢，进「其他」。
  const grouped = new Set(groups.flatMap((g) => g.items));
  const others = types.filter((t) => !grouped.has(t));
  if (others.length > 0) groups.push({ title: '其他', types: [], items: others });

  const renderItem = (t: DslNodeType) => (
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
  );

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
      <div className="fy-flow-palette-list" data-testid="flow-list">
        {groups.filter((g) => g.items.length > 0).map((g, gi) => (
          <details
            key={g.title}
            className="fy-flow-palette-group"
            open={gi === 0}
            data-testid={`flow-group-${gi}`}
          >
            <summary className="fy-flow-palette-group-hd">
              {g.title}（{g.items.length}）
            </summary>
            {g.items.map(renderItem)}
          </details>
        ))}
      </div>
      <p className="muted fy-flow-palette-note">
        点选即添加到画布；也可以把卡片拖进画布。
      </p>
    </div>
  );
}