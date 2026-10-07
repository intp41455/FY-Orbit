/**
 * P9 · 点哪评哪共享工具（A-点哪评哪-05/06/07/08/09/10）。
 *
 * 纯函数集，**不碰 React、不碰网络**——便于单测，也保证几何/坐标逻辑
 * 只有一处实现（前端采集用、后端再校验）。
 */

/** 归一化区域（0~1，相对视口）。 */
export interface NormalizedRegion {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface StrokePoint {
  x: number;
  y: number;
}

export interface Stroke {
  points: StrokePoint[];
  color: string;
  width: number;
}

/** 评审 UI 自身标记——避免「点评审面板时误采背景元素」。 */
export const REVIEW_UI_ATTR = 'data-review-ui';

/**
 * A-点哪评哪-06：把屏幕像素矩形换算成**归一化矩形**（0~1）。
 *
 * 归一化是硬要求——像素坐标换个窗口大小就失效，归一化后跨分辨率/设备可复原。
 * 同时做边界裁剪，保证结果始终落在 [0,1]。
 */
export function toNormalizedRegion(
  rect: { left: number; top: number; width: number; height: number },
  viewport: { w: number; h: number },
): NormalizedRegion {
  const vw = viewport.w || 1;
  const vh = viewport.h || 1;
  const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));
  const x = clamp01(rect.left / vw);
  const y = clamp01(rect.top / vh);
  const w = clamp01(rect.width / vw);
  const h = clamp01(rect.height / vh);
  return { x, y, w: Math.min(w, 1 - x), h: Math.min(h, 1 - y) };
}

/** 由两个拖拽端点算出归一化矩形（支持任意方向拖拽）。 */
export function dragToNormalizedRegion(
  start: { x: number; y: number },
  end: { x: number; y: number },
  viewport: { w: number; h: number },
): NormalizedRegion {
  const left = Math.min(start.x, end.x);
  const top = Math.min(start.y, end.y);
  const width = Math.abs(end.x - start.x);
  const height = Math.abs(end.y - start.y);
  return toNormalizedRegion({ left, top, width, height }, viewport);
}

/** 归一化矩形是否有效（宽高均 > 0）。 */
export function isUsableRegion(region: Partial<NormalizedRegion> | null | undefined): boolean {
  if (!region) return false;
  const { x, y, w, h } = region;
  return (
    typeof x === 'number' && typeof y === 'number' &&
    typeof w === 'number' && typeof h === 'number' &&
    w > 0 && h > 0
  );
}

/** A-点哪评哪-07：把画布笔迹点换算成归一化点集（跨分辨率可复原）。 */
export function normalizeStroke(
  points: StrokePoint[],
  viewport: { w: number; h: number },
  color = '#e11d48',
  width = 3,
): Stroke {
  const vw = viewport.w || 1;
  const vh = viewport.h || 1;
  return {
    color,
    width,
    points: points.map((p) => ({
      x: Math.min(1, Math.max(0, p.x / vw)),
      y: Math.min(1, Math.max(0, p.y / vh)),
    })),
  };
}

/**
 * A-点哪评哪-10：DOM 结构路径（自 body 起的有序段）。
 *
 * 只收**结构**（标签 + 同标签序号），不收文本/class——文本一变就换路径会让
 * 「改文案」被误判成「结构变了」。与后端 `domain_digest` 的输入约定一致。
 */
export function computeDomPath(el: Element): string[] {
  const parts: string[] = [];
  let node: Element | null = el;
  let depth = 0;
  while (node && node !== document.body && depth < 8) {
    let seg = node.tagName.toLowerCase();
    const parent = node.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter((c) => c.tagName === node?.tagName);
      if (same.length > 1) seg += `:nth-of-type(${same.indexOf(node) + 1})`;
    }
    parts.unshift(seg);
    node = node.parentElement;
    depth += 1;
  }
  return parts;
}

/**
 * A-点哪评哪-08：真写标记——**二十来字符**的短标记。
 *
 * 与后端 `build_marker` 规则一致：`<tag#id.c1.c2>`，class 最多取两个。
 * 给 Agent 看这个比看整条 CSS 选择器省 token。
 */
export function buildMarker(tag: string, elementId = '', cls = ''): string {
  let seg = `<${(tag || 'el').trim().toLowerCase()}`;
  if (elementId) seg += `#${elementId.trim()}`;
  const classes = cls.split(/\s+/).filter(Boolean).slice(0, 2);
  if (classes.length) seg += `.${classes.join('.')}`;
  seg += '>';
  return seg.slice(0, 60);
}

/** A-点哪评哪-09：短码（与服务端分配一致，仅用于本地预览序号）。 */
export function previewShortCode(seq: number): string {
  return `r${Math.max(1, seq)}`;
}

/**
 * 判断某事件目标是否落在评审 UI 内（评审面板自身交互不应被拦）。
 */
export function isInsideReviewUi(target: EventTarget | null): boolean {
  return (
    target instanceof Element && target.closest(`[${REVIEW_UI_ATTR}]`) !== null
  );
}

/** 采集元素的定位描述（dom 模式），供提交 NoteDraft 使用。 */
export interface DomTargetCapture {
  tag: string;
  element_id: string;
  element_class: string;
  text: string;
  selector: string;
  dom_path: string[];
  marker: string;
}

/** 为一个 DOM 元素生成完整定位描述（点选用）。 */
export function captureDomTarget(el: HTMLElement): DomTargetCapture {
  const tag = el.tagName.toLowerCase();
  const cls = typeof el.className === 'string' ? el.className : '';
  const classes = cls.split(/\s+/).filter(Boolean);
  return {
    tag,
    element_id: el.id || '',
    element_class: classes.slice(0, 3).join(' '),
    text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120),
    selector: buildSelector(el),
    dom_path: computeDomPath(el),
    marker: buildMarker(tag, el.id || '', classes.join(' ')),
  };
}

/** 生成稳定的 CSS 选择器（自 body 起，带 nth-of-type 消歧）。 */
export function buildSelector(el: HTMLElement): string {
  const parts: string[] = [];
  let node: HTMLElement | null = el;
  let depth = 0;
  while (node && node !== document.body && depth < 6) {
    let seg = node.tagName.toLowerCase();
    if (node.id) {
      seg += `#${node.id}`;
    } else {
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node?.tagName);
        if (same.length > 1) seg += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
    }
    parts.unshift(seg);
    node = node.parentElement;
    depth += 1;
  }
  return parts.join(' > ');
}

/** 视口尺寸（每次采集时取，保证归一化基准正确）。 */
export function currentViewport(): { w: number; h: number } {
  return {
    w: window.innerWidth || document.documentElement.clientWidth || 1,
    h: window.innerHeight || document.documentElement.clientHeight || 1,
  };
}
