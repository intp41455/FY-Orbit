/**
 * P1-10 实时预览：轻量 Markdown 渲染器（零依赖）。
 *
 * 安全模型（XSS 防护）：
 * 1. 所有原始文本先整体 HTML 转义（& < > " '），脚本/事件属性无法以标签形态存活；
 * 2. 之后仅注入渲染器自身生成的白名单标签（h1-h6/p/strong/em/code/pre/
 *    blockquote/ul/ol/li/hr/a/br）；
 * 3. 链接 URL 仅放行 http(s)/mailto/站内相对(#、/) 前缀，javascript: 等
 *    危险协议一律退化为纯文本；外链补 rel="noopener noreferrer"。
 * 因此可以安全地配合 dangerouslySetInnerHTML 使用。
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

interface InlineCtx {
  codes: string[];
}

/** 行内转换：输入必须已转义。code 片段用占位符保护，避免二次格式化。 */
function renderInline(escaped: string, ctx: InlineCtx): string {
  let s = escaped;
  s = s.replace(/`([^`\n]+)`/g, (_m, code: string) => {
    ctx.codes.push(code);
    return `\u0000${ctx.codes.length - 1}\u0000`;
  });
  // 链接：URL 白名单协议
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_m, text: string, url: string) => {
    const u = url.trim();
    if (!/^(https?:\/\/|mailto:|\/|#)/i.test(u)) return text;
    return `<a href="${u}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  s = s.replace(/\u0000(\d+)\u0000/g, (_m, idx: string) => `<code>${ctx.codes[Number(idx)]}</code>`);
  return s;
}

const BLOCK_START = /^(?:#{1,6}\s|\s*>|\s*[-*+]\s|\s*\d+[.)]\s|```)/;

function renderParagraph(lines: string[], ctx: InlineCtx): string {
  const body = lines.map((l) => renderInline(escapeHtml(l), ctx)).join('<br>');
  return `<p>${body}</p>`;
}

export function renderMarkdown(src: string): string {
  const lines = src.replace(/\r\n?/g, '\n').split('\n');
  const out: string[] = [];
  const ctx: InlineCtx = { codes: [] };
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块
    if (/^```/.test(line)) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1; // 跳过收尾围栏（或到达 EOF）
      out.push(`<pre><code>${escapeHtml(buf.join('\n'))}</code></pre>`);
      continue;
    }

    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level}>${renderInline(escapeHtml(h[2]), ctx)}</h${level}>`);
      i += 1;
      continue;
    }

    // 水平线
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push('<hr>');
      i += 1;
      continue;
    }

    // 引用块
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote>${renderParagraph(buf, ctx).slice(3, -4)}</blockquote>`);
      continue;
    }

    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
        i += 1;
      }
      out.push(`<ul>${items.map((it) => `<li>${renderInline(escapeHtml(it), ctx)}</li>`).join('')}</ul>`);
      continue;
    }

    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
        i += 1;
      }
      out.push(`<ol>${items.map((it) => `<li>${renderInline(escapeHtml(it), ctx)}</li>`).join('')}</ol>`);
      continue;
    }

    // 空行
    if (/^\s*$/.test(line)) {
      i += 1;
      continue;
    }

    // 段落：聚合到下一个块级结构或空行
    const buf: string[] = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !BLOCK_START.test(lines[i])) {
      buf.push(lines[i]);
      i += 1;
    }
    out.push(renderParagraph(buf, ctx));
  }

  return out.join('\n');
}
