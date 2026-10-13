/**
 * N3 状态同步 · 切片 3（前端 G4）：画布草稿持久化 + 乐观锁。
 *
 * 这一层是**页面级**的（不是每个画布各接一份）：
 * `DslCanvasPage` 持有唯一状态源 `doc`，两个画布（FlowEditor / DslCanvas）
 * 都通过 `onDocChange` 回传到它 —— 所以草稿落盘只需要一条通道，
 * 两个画布同时挂载时也**不会**把同一份文档各存一次（那会在乐观锁上
 * 自己把自己顶成 409）。
 *
 * 三个关键约定：
 *
 * 1. **不另造定时器**：debounce 800ms / 最长间隔 5000ms 直接沿用
 *    `useAutosave` 的默认值（本文件显式写出，只是为了说明「没另造」）。
 * 2. **乐观锁**：保存一律带 `expected_version`（最近一次读取或保存返回的
 *    `draft_version`）。服务端版本不一致 → HTTP 409 → 本层**不静默重试、
 *    不覆盖**，而是置 `conflict` 并停掉自动保存（`enabled: false`），
 *    冲突横幅给出「重新读取」/「覆盖保存」两个出口。
 * 3. **空画布不落盘**：后端 `validate_dsl` 要求 `nodes` 非空，空图保存只会
 *    换来 422。所以 `nodes.length === 0` 时 `enabled: false`，并且状态如实
 *    报 `idle`（无可保存内容），绝不报「已保存」。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  dslCanvasApi,
  isDslDraftConflict,
  type DslDocument,
} from '../api/dslCanvas';
import { useAutosave, type AutosaveHandle, type SaveOutcome, type SaveState, type StorageState } from './useAutosave';

/** 同一标签页内复用同一个 flow_id（刷新不换草稿）。 */
const FLOW_ID_STORAGE_KEY = 'fy-orbit.dsl-draft.flow-id';

/** 空图占位：引用恒定，避免 `doc === null` 时被误判成「有改动」。 */
const EMPTY_DOC: DslDocument = { version: '1', nodes: [], edges: [] };

function readStoredFlowId(): string | null {
  try {
    return sessionStorage.getItem(FLOW_ID_STORAGE_KEY);
  } catch {
    // 隐私模式 / 禁用存储：只是丢「刷新后续用同一个草稿」，不影响本次保存。
    return null;
  }
}

function writeStoredFlowId(id: string): void {
  try {
    sessionStorage.setItem(FLOW_ID_STORAGE_KEY, id);
  } catch {
    /* 同上：存不进去不影响本次保存链路。 */
  }
}

export interface UseDslDraftOptions {
  /** 页面持有的唯一文档（`null` = 还没有任何内容）。 */
  doc: DslDocument | null;
  /** 显式指定 flow_id；省略时从 sessionStorage 续用，没有则在首次保存时创建。 */
  flowId?: string | null;
  /** 新建流程时的名字。 */
  flowName?: string;
  /** 从服务端读到草稿后交给页面（走页面自己的载入通道，不在这里改图）。 */
  onLoad?: (doc: DslDocument) => void;
}

export interface DslDraftHandle {
  flowId: string | null;
  /** 最近一次已知的服务端草稿版本（乐观锁的来源）。 */
  draftVersion: number | null;
  /**
   * 对外的保存状态。**从未成功落盘过时强制报 `idle`**（无可保存内容），
   * 绝不因为 `useAutosave` 的初值就显示「已保存」。
   */
  state: SaveState;
  storage: { state: StorageState; location: string | null; bytes: number | null };
  auto: AutosaveHandle;
  /** 草稿版本冲突（HTTP 409）：已停自动保存，等用户选「重新读取」或「覆盖保存」。 */
  conflict: boolean;
  /** 重新读取 / 覆盖保存 失败时的可见消息。 */
  error: string | null;
  /** 出口 ①：重新读取服务端草稿（放弃本地未保存改动，由服务端版本为准）。 */
  reload: () => Promise<void>;
  /** 出口 ②：**显式**覆盖（不带 `expected_version`，无条件覆盖服务端草稿）。 */
  overwrite: () => Promise<void>;
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function useDslDraft(options: UseDslDraftOptions): DslDraftHandle {
  const { doc, flowId: flowIdProp = null, flowName = '未命名流程', onLoad } = options;

  const [flowId, setFlowId] = useState<string | null>(flowIdProp);
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const flowIdRef = useRef<string | null>(flowIdProp);
  const versionRef = useRef<number | null>(null);
  const docRef = useRef<DslDocument | null>(doc);
  docRef.current = doc;
  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;

  const rememberVersion = useCallback((v: number) => {
    versionRef.current = v;
    setDraftVersion(v);
  }, []);

  const rememberFlow = useCallback((id: string) => {
    flowIdRef.current = id;
    setFlowId(id);
    writeStoredFlowId(id);
  }, []);

  /** flow_id 的唯一来源：显式 prop → sessionStorage → 首次保存时创建。 */
  const ensureFlow = useCallback(async (): Promise<string> => {
    if (flowIdRef.current) return flowIdRef.current;
    // 不带 doc 创建：后端 `create_flow` 只在给了 doc 时才校验，空画布不该
    // 在「还没想好画什么」的时候就卡住整条保存链路。
    const view = await dslCanvasApi.createFlow({ name: flowName });
    rememberFlow(view.id);
    rememberVersion(view.draft_version);
    return view.id;
  }, [flowName, rememberFlow, rememberVersion]);

  /** 落盘动作：一律带 `expected_version`；409 → 记冲突并**原样抛出**（不重试）。 */
  const save = useCallback(async (next: DslDocument): Promise<SaveOutcome> => {
    const id = await ensureFlow();
    const expected = versionRef.current;
    try {
      const view = await dslCanvasApi.saveDraft(
        id, next, expected == null ? {} : { expected_version: expected },
      );
      rememberVersion(view.draft_version);
      return {
        location: `draft:${view.id}@v${view.draft_version}`,
        bytes: JSON.stringify(next).length,
      };
    } catch (e) {
      if (isDslDraftConflict(e)) setConflict(true);
      throw e;
    }
  }, [ensureFlow, rememberVersion]);

  /** 后端要求 `nodes` 非空；空图没有可落盘的草稿，如实停掉而不是换来 422。 */
  const savable = !!doc && doc.nodes.length > 0;

  const auto = useAutosave<DslDocument>({
    key: `dsl-draft:${flowId ?? 'pending'}`,
    value: doc ?? EMPTY_DOC,
    save,
    // 冲突未解决时停掉自动保存：宁可把改动留在本地等用户决策，也不自动重试覆盖。
    enabled: savable && !conflict,
    delayMs: 800,
    intervalMs: 5000,
  });

  // 首屏载入：已知的 flow_id 读一次草稿（拿到 draft_version 与远端文档）。
  useEffect(() => {
    const known = flowIdProp ?? readStoredFlowId();
    if (!known) return undefined;
    let cancelled = false;
    dslCanvasApi.getFlow(known)
      .then((view) => {
        if (cancelled) return;
        rememberFlow(view.id);
        rememberVersion(view.draft_version);
        if (view.draft_doc) onLoadRef.current?.(view.draft_doc);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(`读取草稿失败：${errorText(e)}`);
      });
    return () => { cancelled = true; };
  }, [flowIdProp, rememberFlow, rememberVersion]);

  const reload = useCallback(async () => {
    const id = flowIdRef.current;
    if (!id) return;
    setError(null);
    try {
      const view = await dslCanvasApi.getFlow(id);
      rememberVersion(view.draft_version);
      setConflict(false);
      if (view.draft_doc) onLoadRef.current?.(view.draft_doc);
    } catch (e) {
      setError(`重新读取失败：${errorText(e)}`);
    }
  }, [rememberVersion]);

  const overwrite = useCallback(async () => {
    const id = flowIdRef.current;
    const next = docRef.current;
    if (!id || !next) return;
    setError(null);
    try {
      // 刻意**不带** `expected_version`：这就是「显式覆盖」的全部含义。
      const view = await dslCanvasApi.saveDraft(id, next);
      rememberVersion(view.draft_version);
      setConflict(false);
    } catch (e) {
      setError(`覆盖保存失败：${errorText(e)}`);
    }
  }, [rememberVersion]);

  return {
    flowId,
    draftVersion,
    // 从未成功落盘过（savedAt 为 null）且当前不是 error 时，不能说「已保存」。
    state: auto.savedAt == null && auto.state !== 'error' ? 'idle' : auto.state,
    storage: { state: auto.storage, location: auto.location, bytes: auto.bytes },
    auto,
    conflict,
    error,
    reload,
    overwrite,
  };
}