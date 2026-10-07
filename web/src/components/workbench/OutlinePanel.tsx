import { useEffect, useMemo, useRef, useState } from 'react';
import { workbenchApi } from '../../api/workbench';
import { errorMessage } from '../ui';
import type { EditorJumpApi } from './CodeEditor';
import { useBase } from '../../hooks/useAutosave';

/**
 * P1-13 侧边定位：Markdown 大纲 + 当前文件内搜索。
 *
 * - 大纲：解析 #/##/###… 标题层级，点击大纲项 → 编辑器滚动至对应行并高亮闪烁；
 * - 搜索：输入关键词 → 列出当前文件所有命中行，点击命中 → 跳转并选中该关键词。
 *
 * 与 CodeEditor 的联动通过注入的 editorApiRef（命令式 jumpToLine）完成，
 * 本组件不直接操作编辑器内部状态；文件内容由本组件自行读取（仅用于解析）。
 */

export interface OutlineHeading {
  level: number; // 1..6
  text: string;
  line: number; // 1-based
}

export interface SearchHit {
  line: number; // 1-based
  col: number; // 0-based column of the first occurrence in that line
  text: string; // full line text
}

const MAX_SEARCH_HITS = 200;

/** 解析 Markdown 标题（跳过代码围栏内的 # 行）。 */
export function parseMarkdownOutline(content: string): OutlineHeading[] {
  const out: OutlineHeading[] = [];
  let inFence = false;
  content.split('\n').forEach((raw, i) => {
    const t = raw.trimStart();
    if (t.startsWith('```') || t.startsWith('~~~')) {
      inFence = !inFence;
      return;
    }
    if (inFence) return;
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(raw);
    if (m) out.push({ level: m[1].length, text: m[2].trim(), line: i + 1 });
  });
  return out;
}

/** 当前文件内搜索：每个命中行取首个出现位置（大小写不敏感），上限 MAX_SEARCH_HITS。 */
export function searchInContent(content: string, keyword: string): SearchHit[] {
  const kw = keyword.trim().toLowerCase();
  if (!kw) return [];
  const hits: SearchHit[] = [];
  content.split('\n').forEach((text, i) => {
    if (hits.length >= MAX_SEARCH_HITS) return;
    const col = text.toLowerCase().indexOf(kw);
    if (col >= 0) hits.push({ line: i + 1, col, text });
  });
  return hits;
}

interface Props {
  workspaceId: string;
  path: string | null;
  /** 注入 CodeEditor 的命令式跳转句柄（与编辑器联动，见 CodeEditor 的 EditorJumpApi）。 */
  editorApiRef: React.MutableRefObject<EditorJumpApi | null>;
}

export function OutlinePanel({ workspaceId, path, editorApiRef }: Props) {
  useBase({ surface: 'web/src/components/workbench/OutlinePanel' });
  const [content, setContent] = useState<string>('');
  const [notParseable, setNotParseable] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState('');
  const [lastJump, setLastJump] = useState<number | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!path) {
      setContent('');
      setNotParseable(null);
      setError(null);
      return;
    }
    const my = ++seq.current;
    setError(null);
    workbenchApi
      .readFile(workspaceId, path)
      .then((fc) => {
        if (seq.current !== my) return;
        if (fc.binary) {
          setNotParseable('二进制文件无法生成大纲。');
          setContent('');
        } else {
          setNotParseable(null);
          setContent(fc.content);
        }
      })
      .catch((e) => {
        if (seq.current !== my) return;
        setContent('');
        setError(errorMessage(e));
      });
  }, [workspaceId, path]);

  const headings = useMemo(() => parseMarkdownOutline(content), [content]);
  const hits = useMemo(() => searchInContent(content, keyword), [content, keyword]);

  function jump(line: number, highlight?: string) {
    editorApiRef.current?.jumpToLine(line, highlight);
    setLastJump(line);
  }

  if (!path) {
    return (
      <div className="outline-panel card" data-testid="outline-panel">
        <strong>大纲 / 定位</strong>
        <div className="muted small">选择文件后可在此查看标题大纲并搜索跳转。</div>
      </div>
    );
  }

  return (
    <div className="outline-panel card" data-testid="outline-panel" data-last-jump={lastJump ?? undefined}>
      <strong>大纲 / 定位</strong>
      {error && <div className="error-text" role="alert">{error}</div>}
      {notParseable && <div className="muted small">{notParseable}</div>}

      {!error && !notParseable && (
        <>
          <ul className="outline-list" data-testid="outline-list">
            {headings.length === 0 && <li className="muted small">（无 Markdown 标题）</li>}
            {headings.map((h) => (
              <li key={`${h.line}-${h.text}`}>
                <button
                  type="button"
                  className="outline-item"
                  data-testid="outline-item"
                  data-line={h.line}
                  style={{ paddingLeft: `${(h.level - 1) * 12 + 6}px` }}
                  title={`跳转到第 ${h.line} 行`}
                  onClick={() => jump(h.line, h.text)}
                >
                  <span className="outline-hash muted">{'#'.repeat(h.level)} </span>
                  {h.text}
                </button>
              </li>
            ))}
          </ul>

          <input
            type="search"
            className="outline-search"
            data-testid="outline-search"
            placeholder="当前文件内搜索…"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          {keyword.trim() && (
            <div className="outline-hits" data-testid="outline-hits">
              <div className="muted small">
                {hits.length} 个命中{hits.length >= MAX_SEARCH_HITS ? `（仅列出前 ${MAX_SEARCH_HITS} 条）` : ''}
              </div>
              <ul className="outline-hit-list">
                {hits.map((hit) => (
                  <li key={`${hit.line}-${hit.col}`}>
                    <button
                      type="button"
                      className="outline-hit"
                      data-testid="outline-hit"
                      data-line={hit.line}
                      title={`跳转到第 ${hit.line} 行`}
                      onClick={() => jump(hit.line, keyword.trim())}
                    >
                      <span className="hit-line muted">L{hit.line}</span>{' '}
                      <span className="hit-text">
                        {hit.col > 0 && hit.text.slice(0, hit.col)}
                        <mark data-testid="hit-mark">{hit.text.slice(hit.col, hit.col + keyword.trim().length)}</mark>
                        {hit.text.slice(hit.col + keyword.trim().length)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
