/**
 * 包 A 全局键位判定工具。
 *
 * 两条硬约束：
 *  1. **IME 必须放行**：中文输入法组字期间 keydown 的 keyCode 是 229、
 *     isComposing 为 true，此时本包一律不拦截、不 preventDefault。
 *  2. **可编辑区放行单键**：在 input/textarea/select/富文本编辑区 里，
 *     只有带修饰键的组合（以及 Esc / Ctrl+`）才允许接管，
 *     否则「1/2/3」「0」这些聚焦段位的单键会把用户输入吃掉。
 */

export function isComposingLike(e: KeyboardEvent | React.KeyboardEvent): boolean {
  return Boolean((e as KeyboardEvent).isComposing) || e.keyCode === 229;
}

export function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return t.isContentEditable;
}

export interface KeyGuardOptions {
  /** 是否允许在可编辑区里接管（例如终端输入区的 Ctrl+R 历史检索）。 */
  allowInEditable?: boolean;
}

/**
 * 返回 true 表示「本 handler 可以继续处理」。
 * allowInEditable=false 时在可编辑区直接拒绝（除非带了 Ctrl/Meta/Alt）。
 */
export function mayTake(e: KeyboardEvent | React.KeyboardEvent, opts: KeyGuardOptions = {}): boolean {
  if (isComposingLike(e)) return false;
  if (!isEditableTarget(e.target)) return true;
  if (opts.allowInEditable) return true;
  // 可编辑区：只放行带修饰键的组合 + Esc。
  return e.ctrlKey || e.metaKey || e.altKey || e.key === 'Escape';
}

/** 平台无关的「主修饰键」判定：mac 用 meta，其余用 ctrl。 */
function primaryDown(e: KeyboardEvent | React.KeyboardEvent): boolean {
  const mac = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || '');
  return mac ? e.metaKey : e.ctrlKey;
}

export interface ParsedKey {
  /** ctrl(cmd) 按下 */
  mod: boolean;
  shift: boolean;
  alt: boolean;
  key: string;
}

export function parseKey(e: KeyboardEvent | React.KeyboardEvent): ParsedKey {
  return {
    mod: primaryDown(e),
    shift: e.shiftKey,
    alt: e.altKey,
    key: e.key.length === 1 ? e.key.toLowerCase() : e.key,
  };
}

/**
 * 是否命中「修饰 + 数字/反引号」：同时接受 Ctrl+Shift+N 与 Alt+N。
 * 理由：Ctrl+Shift+T / Ctrl+W 在主流浏览器里是「重开标签页 / 关闭标签页」的
 * 保留键，preventDefault 拦不住（浏览器层先吃掉）。所以每一个这类键都必须有
 * Alt 兜底 + 完全等价的指针入口（坞标签上的 [+] 与 ×、右键菜单）。
 */
export function matchAccessKey(e: KeyboardEvent | React.KeyboardEvent): number | 'all' | null {
  if (!mayTake(e)) return null;
  const { mod, shift, alt, key } = parseKey(e);
  if (!(mod || alt)) return null;
  if (!/^[0-9]$/.test(key)) return null;
  // Ctrl+Shift+N / Ctrl+Alt+N / Alt+N / Ctrl+Shift+Alt+N 都算数；
  // 但裸 Ctrl+N（新窗口）不带 shift/alt 时不接管，避免误伤浏览器。
  if (mod && !shift && !alt) return null;
  const n = Number(key);
  return n === 0 ? 'all' : n;
}

export function matchBacktick(e: KeyboardEvent | React.KeyboardEvent): boolean {
  if (!mayTake(e)) return false;
  const { mod, alt, key } = parseKey(e);
  return (key === '`' || key === '~') && (mod || alt);
}

export function matchCommandPalette(e: KeyboardEvent | React.KeyboardEvent): boolean {
  if (!mayTake(e)) return false;
  const { mod, alt, key } = parseKey(e);
  return (mod || alt) && key === 'k';
}

export function matchEscape(e: KeyboardEvent | React.KeyboardEvent): boolean {
  return !isComposingLike(e) && e.key === 'Escape';
}
