/**
 * LSP 代码智能 client（P14 · A-代码智能-01）。
 *
 * 后端端点：/api/lsp/{servers,definition,references,symbols,diagnostics}。
 * 位置契约：line / character 均 0-based（LSP 原生语义）。
 * 诚实降级：语言服务端未安装时后端返回 503 lsp_server_not_installed——
 * 调用方应显式提示「语义智能不可用」，不要静默吞错。
 */

export interface LspLocation {
  path: string;
  line: number | null;
  character: number | null;
}

export interface LspServerError {
  error: { code: string; message: string };
}

async function lspFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as LspServerError | null;
    throw new Error(body?.error?.message ?? `LSP request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export function listLspServers(): Promise<{
  servers: Record<string, { installed: boolean; command: string | null; running: boolean }>;
}> {
  return lspFetch("/api/lsp/servers");
}

export function gotoDefinition(path: string, line: number, character: number): Promise<{
  count: number;
  locations: LspLocation[];
}> {
  return lspFetch("/api/lsp/definition", {
    method: "POST",
    body: JSON.stringify({ path, line, character }),
  });
}

export function findReferences(
  path: string,
  line: number,
  character: number,
  includeDeclaration = false,
): Promise<{ count: number; locations: LspLocation[] }> {
  return lspFetch("/api/lsp/references", {
    method: "POST",
    body: JSON.stringify({ path, line, character, include_declaration: includeDeclaration }),
  });
}

export function documentSymbols(path: string): Promise<{
  count: number;
  symbols: { name: string; kind: number | null; line: number | null }[];
}> {
  return lspFetch(`/api/lsp/symbols?path=${encodeURIComponent(path)}`);
}

export function diagnostics(path: string): Promise<{
  count: number;
  diagnostics: { severity: number | null; message: string | null; line: number | null }[];
}> {
  return lspFetch(`/api/lsp/diagnostics?path=${encodeURIComponent(path)}`);
}
