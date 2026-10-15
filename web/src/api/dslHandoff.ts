import type { DslDocument } from './dslCanvas';

/**
 * G4-k 开箱模板→工坊通道：sessionStorage 一次性交接。
 *
 * 模板页把 `import-code` 换回的 DSL 文档写进来并跳 `/dsl-canvas`；
 * 工坊页挂载时 `takeDslHandoff()` 读走即删（刷新不重复载入）。
 * 坏数据返回 null 且不抛错——交接损坏绝不能把工坊页炸掉。
 */
export const DSL_HANDOFF_KEY = 'fy-orbit.dsl-handoff';

export interface DslHandoff {
  doc: DslDocument;
  source: string;
  at: number;
}

function isDoc(v: unknown): v is DslDocument {
  if (typeof v !== 'object' || v === null) return false;
  const d = v as { nodes?: unknown; edges?: unknown };
  return Array.isArray(d.nodes) && Array.isArray(d.edges);
}

export function writeDslHandoff(doc: DslDocument, source: string): void {
  const payload: DslHandoff = { doc, source, at: Date.now() };
  sessionStorage.setItem(DSL_HANDOFF_KEY, JSON.stringify(payload));
}

export function takeDslHandoff(): DslHandoff | null {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(DSL_HANDOFF_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  // 消费一次：无论解析成败都先删，坏数据不留给下次再炸。
  try {
    sessionStorage.removeItem(DSL_HANDOFF_KEY);
  } catch {
    /* ignore */
  }
  try {
    const parsed = JSON.parse(raw) as { doc?: unknown; source?: unknown; at?: unknown };
    if (!isDoc(parsed.doc) || typeof parsed.source !== 'string') return null;
    return { doc: parsed.doc, source: parsed.source, at: typeof parsed.at === 'number' ? parsed.at : 0 };
  } catch {
    return null;
  }
}
