/**
 * P1-10 实时预览：编辑器 → 预览窗的微型发布/订阅总线。
 *
 * 目的：让 PreviewPane 拿到 CodeEditor 中「未保存的实时草稿内容」，
 * 而不把编辑器内容 state 上提到 WorkbenchPage（避免打扰既有结构，
 * CodeEditor 侧只需在加载/变更/清空三处追加一行发布调用）。
 *
 * 单监听者即可满足工作台右栏唯一预览窗的场景；重复订阅时后者覆盖前者。
 */

export interface PreviewDraft {
  /** 当前编辑文件相对路径；null 表示未选择文件（预览应回到空态）。 */
  path: string | null;
  /** 编辑器中的实时文本内容（可能尚未保存到磁盘）。 */
  content: string;
}

type PreviewDraftListener = (draft: PreviewDraft) => void;

let listener: PreviewDraftListener | null = null;

export function publishPreviewDraft(draft: PreviewDraft): void {
  listener?.(draft);
}

export function subscribePreviewDraft(fn: PreviewDraftListener): () => void {
  listener = fn;
  return () => {
    if (listener === fn) listener = null;
  };
}
