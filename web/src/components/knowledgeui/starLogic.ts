/**
 * 包 E 私有模块 · 知识星图的纯逻辑层（无 React / 无 canvas / 无网络）
 * ---------------------------------------------------------------------------
 * ⚠⚠ 诚实性声明（本文件是整个星图的地基，必须先读）⚠⚠
 *
 * 后端 `/api/kb` **当前只有**这些能力：
 *   - GET  /api/kb/documents                 → 文档列表（含 chunk_count）
 *   - GET  /api/kb/documents/{id}/chunks     → 该文档的切片列表（真实正文）
 *   - POST /api/kb/search                    → 关键词检索命中（带得分与命中词）
 *   - 适配器 CRUD / probe / sync
 *
 * 后端**没有**：向量、embedding、attention 权重、chunk 间语义相似度、中心度。
 * 因此本星图的节点与边**全部来自真实切片与真实检索命中**，推导方式逐项写明：
 *
 *   节点 = 真实 chunk（`GET /documents/{id}/chunks`）
 *   权重 = 1 + 该切片在最近一次检索中被共同命中的次数
 *          （无中心度接口，所以只用「被一起检索到」当权重代理；界面必须如实标注）
 *   边·顺序     = 同一文档内 seq 相邻的两个切片（由 chunk.seq 推导）→ 虚线
 *   边·共同命中 = 同一次检索结果里同时出现的两个切片（由 search 返回推导）→ 实线
 *   边·双向链接 = 后端无此接口 → 在高级配置里标「待接线」并禁用，**绝不画假线**
 *
 * 任何"看起来很像语义关联"的连线都是编的，本文件不生成。
 */

/* ------------------------------------------------------------------ */
/* 类型                                                                */
/* ------------------------------------------------------------------ */

export type StarEdgeKind = 'sequence' | 'cohit' | 'bidirectional';

export interface StarNode {
  id: string;
  /** 所属文档 id */
  docId: string;
  docName: string;
  seq: number;
  /** 真实切片正文（截断后用于摘要展示） */
  excerpt: string;
  /** 字数（真实统计） */
  chars: number;
  /** 权重代理值，≥0 */
  weight: number;
  /** 主题簇序号（按文档分配，0=主簇 1=次簇 2=第三簇 3+=循环） */
  cluster: number;
  /** 布局坐标（单位球坐标，由本文件确定性生成） */
  x: number;
  y: number;
  z: number;
  /** 是否被当前检索命中 */
  matched: boolean;
  /** 检索得分（仅命中时有） */
  score: number | null;
}

export interface StarEdge {
  id: string;
  source: string;
  target: string;
  kind: StarEdgeKind;
  /** 关联强度 0–1，用于线宽 */
  strength: number;
}

/* ------------------------------------------------------------------ */
/* 常量                                                                */
/* ------------------------------------------------------------------ */

/** 节点绘制上限（任务书 §3.5）。超限做视锥剔除并**显式告知用户被截断**。 */
export const NODE_DRAW_LIMIT = 2000;

/** 参与「错开生长」动画的节点上限（超过就同时出现）。 */
export const STAGGER_LIMIT = 24;

/** 呼吸节点数：只给权重最高的 5 个。 */
export const BREATH_COUNT = 5;

export const MIN_ZOOM = 0.4;
export const MAX_ZOOM = 3.0;

/* ------------------------------------------------------------------ */
/* 确定性伪随机（保证同一份数据每次布局一致，不会每次渲染都重排）        */
/* ------------------------------------------------------------------ */

/** FNV-1a 32 位散列 → 用于把 chunk id 映射成稳定的 [0,1) 随机数。 */
export function hash01(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return (h >>> 0) / 0x100000000;
}

/** 黄金角螺旋：把 n 个点均匀铺在球面上（斐波那契球），比随机撒点均匀得多。 */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function fibonacciSphere(i: number, n: number): [number, number, number] {
  if (n <= 1) return [0, 0, 0];
  const y = 1 - (i / (n - 1)) * 2;
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = GOLDEN_ANGLE * i;
  return [Math.cos(theta) * r, y, Math.sin(theta) * r];
}

/* ------------------------------------------------------------------ */
/* 簇分配：文档 → 簇序号                                              */
/* ------------------------------------------------------------------ */

/**
 * 把文档按「稳定顺序」映射到簇序号。
 *
 * 为什么不是「最大文档=主簇」：那会让星图颜色随数据量抖动。
 * 这里按 docId 排序后取模分配，同一份数据永远同一套颜色。
 */
export function assignClusters(docIds: string[]): Map<string, number> {
  const sorted = [...new Set(docIds)].sort();
  const map = new Map<string, number>();
  sorted.forEach((id, i) => map.set(id, i % 3));
  return map;
}

/* ------------------------------------------------------------------ */
/* 图构建                                                              */
/* ------------------------------------------------------------------ */

export interface BuildGraphInput {
  /** docId → 切片序列（真实，来自 /documents/{id}/chunks） */
  chunksByDoc: Map<string, Array<{ id: string; seq: number; content: string }>>;
  /** 文档名 → docId */
  docNameToId: Map<string, string>;
  /** 最近一次检索命中的 chunk_id 集合（真实，来自 /search） */
  matchedIds: Set<string>;
  /** 命中得分（chunk_id → score） */
  scores: Map<string, number>;
  /** 节点上限（超出部分截断） */
  limit?: number;
}

export interface BuildGraphResult {
  nodes: StarNode[];
  edges: StarEdge[];
  /** 真实节点总数（截断前），用于诚实告知「共 M，已显示 N」 */
  totalNodes: number;
  /** 是否因上限被截断 */
  truncated: boolean;
}

/**
 * 由真实切片 + 真实命中构建星图。
 *
 * 布局：按文档分「花瓣」，文档在球心附近聚成一簇，
 * 该簇内的 chunk 用黄金角螺旋铺在半径随 chunk 数收缩的壳层上。
 * 这样「同一文档的切片天然相邻」，看图就能读出文档边界。
 */
export function buildStarGraph(input: BuildGraphInput): BuildGraphResult {
  const limit = input.limit ?? NODE_DRAW_LIMIT;
  const clusterOf = assignClusters([...input.chunksByDoc.keys()]);

  const nodes: StarNode[] = [];
  const cohitCounter = new Map<string, number>();
  // 共同命中边：同一次检索里两两配对。为避免 O(n²) 爆炸，只按同文档分组再连。
  const byDocHits = new Map<string, string[]>();

  for (const [docId, chunks] of input.chunksByDoc) {
    const hitsInDoc: string[] = [];
    for (const c of chunks) {
      const matched = input.matchedIds.has(c.id);
      if (matched) {
        hitsInDoc.push(c.id);
        cohitCounter.set(c.id, (cohitCounter.get(c.id) ?? 0) + 1);
      }
      nodes.push(makeNode({
        chunkId: c.id,
        docId,
        docName: input.docNameToId.has(docId) ? findDocName(input, docId) : docId,
        seq: c.seq,
        content: c.content,
        weight: 1 + (cohitCounter.get(c.id) ?? 0),
        cluster: clusterOf.get(docId) ?? 0,
        matched,
        score: input.scores.get(c.id) ?? null,
      }));
    }
    byDocHits.set(docId, hitsInDoc);
  }

  const totalNodes = nodes.length;
  const truncated = totalNodes > limit;
  const visible = truncated ? nodes.slice(0, limit) : nodes;
  const visibleIds = new Set(visible.map((n) => n.id));

  // ---- 布局（只对可见节点做，省开销）----
  const perDoc = new Map<string, StarNode[]>();
  for (const n of visible) {
    const arr = perDoc.get(n.docId);
    if (arr) arr.push(n);
    else perDoc.set(n.docId, [n]);
  }
  const docList = [...perDoc.keys()];
  const docCount = Math.max(1, docList.length);
  for (const [docId, arr] of perDoc) {
    const dIndex = docList.indexOf(docId);
    // 文档簇中心：在球面上按黄金角分布，簇半径随文档数收缩
    const [dx, dy, dz] = fibonacciSphere(dIndex, docCount);
    const clusterRadius = 0.42 + 0.22 * hash01(docId);
    const cx = dx * clusterRadius;
    const cy = dy * clusterRadius;
    const cz = dz * clusterRadius;
    const shell = 0.10 + 0.05 * Math.min(6, arr.length / 8);

    arr.forEach((n, i) => {
      const [ux, uy, uz] = fibonacciSphere(i, arr.length);
      // 每个 chunk 用 id 派生的微扰，避免同文档内完全对称
      const jx = (hash01(`${n.id}:x`) - 0.5) * 0.05;
      const jy = (hash01(`${n.id}:y`) - 0.5) * 0.05;
      const jz = (hash01(`${n.id}:z`) - 0.5) * 0.05;
      n.x = cx + ux * shell + jx;
      n.y = cy + uy * shell + jy;
      n.z = cz + uz * shell + jz;
    });
  }

  // ---- 边 ----
  const edges: StarEdge[] = [];
  // 顺序边：同文档 seq 相邻（真实 seq 推导，虚线）
  for (const arr of perDoc.values()) {
    const sorted = [...arr].sort((a, b) => a.seq - b.seq);
    for (let i = 1; i < sorted.length; i += 1) {
      const a = sorted[i - 1];
      const b = sorted[i];
      edges.push({
        id: `seq:${a.id}->${b.id}`,
        source: a.id,
        target: b.id,
        kind: 'sequence',
        strength: 0.35,
      });
    }
  }
  // 共同命中边：同文档内被同一次检索命中的切片两两相连（真实 search 结果推导，实线）
  for (const hits of byDocHits.values()) {
    const shown = hits.filter((id) => visibleIds.has(id));
    // 单文档命中数可能很大，做星型连接（与第一个命中连）而不是全连接，避免边爆炸
    if (shown.length > 1) {
      const hub = shown[0];
      for (let i = 1; i < shown.length; i += 1) {
        edges.push({
          id: `cohit:${hub}:${shown[i]}`,
          source: hub,
          target: shown[i],
          kind: 'cohit',
          strength: 0.85,
        });
      }
    }
  }

  return { nodes: visible, edges, totalNodes, truncated };
}

function findDocName(input: BuildGraphInput, docId: string): string {
  for (const [name, id] of input.docNameToId) {
    if (id === docId) return name;
  }
  return docId;
}

function makeNode(args: {
  chunkId: string;
  docId: string;
  docName: string;
  seq: number;
  content: string;
  weight: number;
  cluster: number;
  matched: boolean;
  score: number | null;
}): StarNode {
  return {
    id: args.chunkId,
    docId: args.docId,
    docName: args.docName,
    seq: args.seq,
    excerpt: args.content.replace(/\s+/g, ' ').trim().slice(0, 220),
    chars: args.content.length,
    weight: args.weight,
    cluster: args.cluster,
    x: 0,
    y: 0,
    z: 0,
    matched: args.matched,
    score: args.score,
  };
}

/* ------------------------------------------------------------------ */
/* 手写透视投影                                                        */
/* ------------------------------------------------------------------ */

export interface ViewTransform {
  /** 偏航角（弧度） */
  yaw: number;
  /** 俯仰角（弧度），钳制在 ±80° 避免翻转 */
  pitch: number;
  /** 缩放 */
  zoom: number;
  /** 画布中心平移（像素） */
  panX: number;
  panY: number;
  /** 视口宽高（像素） */
  width: number;
  height: number;
  /** 透视深度（越大透视越强） */
  perspective: number;
}

export function defaultView(width: number, height: number): ViewTransform {
  return {
    yaw: 0.6,
    pitch: 0.35,
    zoom: 1,
    panX: 0,
    panY: 0,
    width,
    height,
    perspective: 2.6,
  };
}

/** 球面坐标 → 相机空间（先偏航再俯仰）。 */
export function rotate(p: { x: number; y: number; z: number }, v: ViewTransform): { x: number; y: number; z: number } {
  const cy = Math.cos(v.yaw);
  const sy = Math.sin(v.yaw);
  const x1 = p.x * cy - p.z * sy;
  const z1 = p.x * sy + p.z * cy;

  const cp = Math.cos(v.pitch);
  const sp = Math.sin(v.pitch);
  const y2 = p.y * cp - z1 * sp;
  const z2 = p.y * sp + z1 * cp;

  return { x: x1, y: y2, z: z2 };
}

export interface Projected {
  /** 屏幕像素坐标 */
  sx: number;
  sy: number;
  /** 相机空间 z（>0 表示在观察者前方；越小越远） */
  depth: number;
  /** 透视缩放系数 */
  scale: number;
  /** 是否落在相机前方（背面剔除用） */
  visible: boolean;
}

/** 手写透视：除以 (perspective - z) 得到近大远小。 */
export function project(p: { x: number; y: number; z: number }, v: ViewTransform): Projected {
  const r = rotate(p, v);
  const depth = v.perspective - r.z;
  if (depth <= 0.05) {
    // 在相机后面或极端贴脸 → 不画（避免除以极小数导致坐标爆炸）
    return { sx: 0, sy: 0, depth, scale: 0, visible: false };
  }
  const scale = (v.zoom * 1) / depth;
  return {
    sx: v.width / 2 + v.panX + r.x * v.width * 0.42 * scale,
    sy: v.height / 2 + v.panY + r.y * v.height * 0.42 * scale,
    depth,
    scale,
    visible: true,
  };
}

/** 节点半径（屏幕像素）：权重越大越大，夹在 4–18px（任务书 §3.1）。 */
export function nodeRadius(weight: number): number {
  const w = Math.max(1, weight);
  return Math.min(18, 4 + (w - 1) * 2.4);
}

/** 连线粗细 1.2–2.6px（任务书 §3.1）。 */
export function edgeWidth(strength: number): number {
  return 1.2 + Math.min(1, Math.max(0, strength)) * 1.4;
}

/* ------------------------------------------------------------------ */
/* 视口裁剪                                                            */
/* ------------------------------------------------------------------ */

export function inViewport(p: Projected, v: ViewTransform, margin = 48): boolean {
  if (!p.visible) return false;
  return (
    p.sx >= -margin &&
    p.sx <= v.width + margin &&
    p.sy >= -margin &&
    p.sy <= v.height + margin
  );
}

/**
 * 可见性裁剪：只保留视口内 + 边距的节点。
 * 任务书 §3.5 要求超限时做视锥剔除——这里返回索引集合供渲染层遍历。
 */
export function cull(
  nodes: StarNode[],
  v: ViewTransform,
  margin = 48,
): { visible: StarNode[]; hidden: number } {
  const visible: StarNode[] = [];
  let hidden = 0;
  for (const n of nodes) {
    if (inViewport(project(n, v), v, margin)) visible.push(n);
    else hidden += 1;
  }
  return { visible, hidden };
}

/* ------------------------------------------------------------------ */
/* 命中测试                                                            */
/* ------------------------------------------------------------------ */

/** 屏幕坐标 → 最近节点（半径内命中）。用「投影后屏幕距离」，与用户直觉一致。 */
export function hitTest(
  nodes: StarNode[],
  v: ViewTransform,
  sx: number,
  sy: number,
): StarNode | null {
  let best: StarNode | null = null;
  let bestD = Infinity;
  for (const n of nodes) {
    const p = project(n, v);
    if (!p.visible) continue;
    const dx = p.sx - sx;
    const dy = p.sy - sy;
    const d = Math.hypot(dx, dy);
    // 命中半径放宽到视觉半径的 1.6 倍（小节点也好点中）
    const hit = nodeRadius(n.weight) * p.scale * 1.6 + 6;
    if (d <= hit && d < bestD) {
      bestD = d;
      best = n;
    }
  }
  return best;
}

/* ------------------------------------------------------------------ */
/* 交互：缩放 / 适配全图 / 居中到某节点                                 */
/* ------------------------------------------------------------------ */

export function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

export function clampPitch(p: number): number {
  const lim = (80 * Math.PI) / 180;
  return Math.min(lim, Math.max(-lim, p));
}

/** `F` 键适配全图：算出把所有节点都框进视口所需的缩放与平移。 */
export function fitAll(nodes: StarNode[], v: ViewTransform): ViewTransform {
  if (nodes.length === 0) return v;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    const p = project(n, { ...v, zoom: 1, panX: 0, panY: 0 });
    if (!p.visible) continue;
    minX = Math.min(minX, p.sx);
    maxX = Math.max(maxX, p.sx);
    minY = Math.min(minY, p.sy);
    maxY = Math.max(maxY, p.sy);
  }
  if (!Number.isFinite(minX)) return v;

  const w = Math.max(1, maxX - minX);
  const h = Math.max(1, maxY - minY);
  const pad = 56;
  const zoom = clampZoom(Math.min((v.width - pad * 2) / w, (v.height - pad * 2) / h));
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  return {
    ...v,
    zoom,
    // 让几何中心落在画布中心
    panX: (v.width / 2 - cx * zoom) / zoom,
    panY: (v.height / 2 - cy * zoom) / zoom,
  };
}

/**
 * 把某个节点平移到视口中心（搜索过滤后「自动平移进视口」用）。
 * 只改 pan，不动 zoom/角度，避免打断用户的观察姿态。
 */
export function centerOn(node: StarNode, v: ViewTransform): ViewTransform {
  const p = project(node, { ...v, panX: 0, panY: 0 });
  if (!p.visible) return v;
  return {
    ...v,
    panX: v.panX + (v.width / 2 - p.sx) / v.zoom,
    panY: v.panY + (v.height / 2 - p.sy) / v.zoom,
  };
}

/* ------------------------------------------------------------------ */
/* 节点卡定位：跟随节点，且不遮视口中心                                */
/* ------------------------------------------------------------------ */

export interface CardPos {
  left: number;
  top: number;
}

/**
 * 把节点卡放在节点旁边，并在靠近视口边缘时翻边；
 * 同时保证卡片不会盖住视口中心（小白最先看的地方）。
 */
export function placeCard(p: Projected, v: ViewTransform, w = 300, h = 220): CardPos {
  const gap = 14;
  let left = p.sx + gap;
  let top = p.sy - h / 2;

  if (left + w > v.width - 8) left = p.sx - gap - w;
  if (left < 8) left = 8;
  if (top + h > v.height - 8) top = v.height - h - 8;
  if (top < 8) top = 8;

  // 不遮视口中心：卡片若横跨中心，垂直方向让开
  const cx = v.width / 2;
  if (left < cx && left + w > cx) {
    top = p.sy + gap;
    if (top + h > v.height - 8) top = Math.max(8, p.sy - gap - h);
  }
  return { left, top };
}

/* ------------------------------------------------------------------ */
/* 图例：簇色（天蓝主簇 / 薄荷青次级簇 / 紫第三簇 —— 薄荷青仅装饰）    */
/* ------------------------------------------------------------------ */

/** 返回 CSS 变量名而非字面色值，保证零裸色。 */
export const CLUSTER_VAR: readonly string[] = [
  'var(--ui-sky-500)',
  'var(--ui-teal-300)',
  'var(--ui-violet-400)',
  'var(--ui-sky-700)',
  'var(--ui-mint-400)',
  'var(--ui-violet-500)',
];

export function clusterColor(cluster: number): string {
  return CLUSTER_VAR[((cluster % CLUSTER_VAR.length) + CLUSTER_VAR.length) % CLUSTER_VAR.length];
}

/** 簇的中文名（图例与节点卡共用）。 */
export const CLUSTER_LABEL: readonly string[] = ['主簇', '次级簇', '第三簇'];

export function clusterLabel(cluster: number): string {
  return CLUSTER_LABEL[cluster] ?? `簇 ${cluster + 1}`;
}

/** 权重归一化：用于节点亮度（sky-200 → sky-600）。 */
export function weightNorm(weight: number, maxWeight: number): number {
  if (maxWeight <= 1) return 1;
  return Math.min(1, Math.max(0, (weight - 1) / (maxWeight - 1)));
}