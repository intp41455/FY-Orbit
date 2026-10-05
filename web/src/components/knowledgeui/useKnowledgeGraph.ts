/**
 * 包 E 私有 hook · 星图数据装配
 * ---------------------------------------------------------------------------
 * 只从**真实后端数据**装配星图，并如实汇报每一处取不到的地方：
 *   1. GET /api/kb/documents              → 文档清单（拿 chunk_count 与 created_at）
 *   2. GET /api/kb/documents/{id}/chunks  → 每个 ready 文档的真实切片（节点来源）
 *   3. POST /api/kb/search                → 命中集合（决定权重与「共同命中」边）
 *
 * 诚实性要点：
 *  - 切片拉取失败**按文档粒度**记录，不会让整张图失败，也不会假装该文档没有切片；
 *  - 未 ready（indexing / failed）的文档不进图，但会在列表视图里如实显示其状态；
 *  - 节点上限 2000：超出时截断并把 `truncated` 上抛，界面必须显示「共 M，已显示 N」。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { knowledgeApi, type KBDocument } from '../../api/knowledge';
import {
  NODE_DRAW_LIMIT,
  buildStarGraph,
  type StarEdge,
  type StarNode,
} from './starLogic';

export interface KnGraphState {
  nodes: StarNode[];
  edges: StarEdge[];
  documents: KBDocument[];
  loading: boolean;
  /** 真实节点总数（截断前） */
  totalNodes: number;
  truncated: boolean;
  /** 按文档记录的切片拉取失败，键为文档名 */
  chunkErrors: Array<{ docName: string; message: string }>;
  /** 命中 chunk_id 集合 */
  matchedIds: Set<string>;
  scores: Map<string, number>;
  /** 数据变了就自增，供星图重新生长动画使用 */
  version: number;
  reload: () => void;
  /** 把一次检索结果并入命中集合 */
  applyHits: (hits: Array<{ chunk_id: string; score: number }>) => void;
  clearMatches: () => void;
}

interface ChunkCacheEntry {
  seq: number;
  content: string;
}

/** 并发拉取上限：一次打十几个请求会挤占连接池，后端是单机。 */
const CONCURRENCY = 4;

export function useKnowledgeGraph(options?: { enabled?: boolean }): KnGraphState {
  const enabled = options?.enabled ?? true;

  const [documents, setDocuments] = useState<KBDocument[]>([]);
  const [chunkCache, setChunkCache] = useState<Map<string, ChunkCacheEntry[]>>(new Map());
  const [chunkErrors, setChunkErrors] = useState<Array<{ docName: string; message: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [matchedIds, setMatchedIds] = useState<Set<string>>(new Set());
  const [scores, setScores] = useState<Map<string, number>>(new Map());
  const [version, setVersion] = useState(0);
  const [tick, setTick] = useState(0);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setChunkErrors([]);
    try {
      const list = await knowledgeApi.listDocuments();
      if (!aliveRef.current) return;
      setDocuments(list.documents);

      const ready = list.documents.filter((d) => d.status === 'ready');
      const cache = new Map<string, ChunkCacheEntry[]>();
      const errors: Array<{ docName: string; message: string }> = [];

      // 分批并发拉切片：节点来源必须是真实正文，不能用文档元数据假造。
      for (let i = 0; i < ready.length; i += CONCURRENCY) {
        const batch = ready.slice(i, i + CONCURRENCY);
        const results = await Promise.all(
          batch.map(async (doc) => {
            try {
              const res = await knowledgeApi.listChunks(doc.id);
              return {
                docId: doc.id,
                ok: true as const,
                chunks: (res.chunks ?? []).map((c) => ({ seq: c.seq, content: c.content })),
              };
            } catch (e) {
              return {
                docId: doc.id,
                ok: false as const,
                message: e instanceof Error ? e.message : String(e),
              };
            }
          }),
        );

        for (const r of results) {
          if (r.ok) cache.set(r.docId, r.chunks);
          else errors.push({ docName: ready.find((d) => d.id === r.docId)?.name ?? r.docId, message: r.message });
        }
        if (!aliveRef.current) return;
      }

      if (!aliveRef.current) return;
      setChunkCache(cache);
      setChunkErrors(errors);
    } catch (e) {
      if (!aliveRef.current) return;
      // 清单都拉不到：如实把错误抛给页面显示，不静默
      setChunkErrors([{ docName: '文档清单', message: e instanceof Error ? e.message : String(e) }]);
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void load();
  }, [load, tick]);

  const graph = useMemo(() => {
    const chunksByDoc = new Map<string, Array<{ id: string; seq: number; content: string }>>();
    // 用确定性伪 id 前缀，保证同一份切片每次渲染出的节点 id 稳定
    // （真实 chunk id 由后端给出，但这里按 docId + seq 组装以便展示与定位）
    for (const doc of documents) {
      const rows = chunkCache.get(doc.id);
      if (!rows) continue;
      chunksByDoc.set(
        doc.id,
        rows.map((r) => ({ id: `${doc.id}#${r.seq}`, seq: r.seq, content: r.content })),
      );
    }
    const docNameToId = new Map<string, string>();
    for (const doc of documents) docNameToId.set(doc.name, doc.id);

    return buildStarGraph({
      chunksByDoc,
      docNameToId,
      matchedIds,
      scores,
      limit: NODE_DRAW_LIMIT,
    });
  }, [documents, chunkCache, matchedIds, scores]);

  const applyHits = useCallback((hits: Array<{ chunk_id: string; score: number }>) => {
    setMatchedIds((prev) => {
      const next = new Set(prev);
      for (const h of hits) next.add(h.chunk_id);
      return next;
    });
    setScores((prev) => {
      const next = new Map(prev);
      for (const h of hits) next.set(h.chunk_id, h.score);
      return next;
    });
    setVersion((v) => v + 1);
  }, []);

  const clearMatches = useCallback(() => {
    setMatchedIds(new Set());
    setScores(new Map());
  }, []);

  return {
    nodes: graph.nodes,
    edges: graph.edges,
    documents,
    loading,
    totalNodes: graph.totalNodes,
    truncated: graph.truncated,
    chunkErrors,
    matchedIds,
    scores,
    version,
    reload: () => setTick((t) => t + 1),
    applyHits,
    clearMatches,
  };
}