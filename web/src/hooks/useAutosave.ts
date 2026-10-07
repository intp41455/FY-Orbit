/**
 * 基座前端半边（P1 · A-基座质保-12 W2 / A-基座质保-14 W4 / A-基座质保-11 W1 的钩子面）。
 *
 * 三个导出各管一件事，互不假装：
 *
 * - `useAutosave` —— **跨页统一的保存状态机**（W2 四态）+ **IO 异常策略**（W4）。
 *   四态：`saving` / `saved`（已保存到 X 时刻）/ `dirty`（未保存改动）/ `error`。
 *   写前预检可用空间与可写性；不足或只读时走降级路径（备选目录 + 结构化回执），
 *   **改动留在队列里不丢**；恢复可用后立即回到主盘并补写积压改动。
 * - `useBase` —— W1 要求的**统一接入声明**（`useBase({ surface, capabilities })`）。
 *   「留痕 / 本地优先 / 统一错误回执」三个子面由它统一给出，各页不再各写一套。
 * - `BASE_CAPABILITIES` —— 四项能力的 id，与后端 `services/quality/base_contract.py`
 *   的同名常量一一对应（后端扫描源码里的 `useBase(` / `<BaseBound` 即认定已接入）。
 *
 * 诚实边界（写进注释，免得后人误读）：
 *
 * 1. 浏览器**拿不到真实磁盘剩余空间**。`preflight` 由调用方注入（桌面壳可用 Node/Tauri
 *    的 `statvfs`；Web 环境可用 `navigator.storage.estimate()`）。未注入时预检**如实
 *    返回 undefined**，本 hook 不编造一个假的剩余空间。
 * 2. 留痕的落地通道（审计哈希链）在服务端。`useBase().audit.record` 把帧交给调用方注入的
 *    sink；没注入时只在本地账本排队并标记 `pending-sink`，**不假装已入链**。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

// --------------------------------------------------------------------------- //
// 四态与结构化回执
// --------------------------------------------------------------------------- //
export type SaveState = 'saving' | 'saved' | 'dirty' | 'error';
export type StorageState = 'ok' | 'degraded';

export type StorageErrorCode =
  | 'disk_full'
  | 'read_only'
  | 'permission_denied'
  | 'quota_exceeded'
  | 'write_failed';

/** 统一错误回执（A-基座质保-16 的字段形态：code / message / hint / retryable）。 */
export interface StorageErrorReceipt {
  code: StorageErrorCode;
  message: string;
  hint: string;
  directory?: string;
  fallbackUsed: boolean;
  retryable: boolean;
  at: number;
}

export interface SaveContext {
  key: string;
  directory: string;
  /** true = 正在写备选目录（降级路径）。 */
  fallback: boolean;
}

export interface SaveOutcome {
  /** 可读的保存时刻（调用方给的，例如服务端返回的时间串）。 */
  at?: string;
  /** 存储位置（给人看的路径 / 云盘名）。 */
  location?: string;
  /** 占用空间（字节）。 */
  bytes?: number;
}

export interface PreflightResult {
  writable: boolean;
  /** 目标盘可用空间（字节）。拿不到就**别填**，不要猜。 */
  freeBytes?: number;
  /** 本次要写的数据量（字节）。 */
  requiredBytes?: number;
  /** 实际要写的目录（例如主盘不可写时由调用方决定）。 */
  directory: string;
  /** 不可写时的原因（给人看的）。 */
  reason?: string;
}

export interface SaveRecord {
  state: SaveState;
  at: number;
  label: string;
}

/** 四态文案（全界面统一，不得各页自拟 —— W2 验收 ①）。 */
export const SAVE_STATE_LABELS: Record<SaveState, string> = {
  saving: '保存中…',
  saved: '已保存',
  dirty: '未保存改动',
  error: '保存失败',
};

/** 每类 IO 故障的**可照做**提示（W4 验收 ②③：明确提示而不是静默丢失）。 */
export const STORAGE_ERROR_HINTS: Record<StorageErrorCode, string> = {
  disk_full: '磁盘已写满：请清理空间或换一个盘。改动仍在队列里排队，不会丢。',
  read_only: '目标盘已变为只读：请恢复写权限或换到备用目录。改动仍在排队。',
  permission_denied: '该目录没有写权限：请授予写权限，或改存到备用目录。改动仍在排队。',
  quota_exceeded: '存储配额已满：请清理空间（或换盘）。改动仍在排队，不会丢。',
  write_failed: '写入失败：请点「重试」；改动仍在队列里，不会丢。',
};

const STORAGE_FAULT_CODES: readonly StorageErrorCode[] = [
  'disk_full', 'read_only', 'permission_denied', 'quota_exceeded',
];

export function isStorageFault(code: StorageErrorCode): boolean {
  return STORAGE_FAULT_CODES.includes(code);
}

// --------------------------------------------------------------------------- //
// 纯函数：格式化与错误归一
// --------------------------------------------------------------------------- //
export function serialize(value: unknown): string {
  try {
    return JSON.stringify(value) ?? 'undefined';
  } catch {
    // 循环引用等：退化成「每次都不一样」，宁可多存一次，不可漏存。
    return `unserializable:${String(value)}`;
  }
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** 「已保存到 14:03」（W2 验收 ① 的文案形态）。 */
export function formatSavedAt(at: number | null | undefined, now?: number): string {
  if (at == null) return SAVE_STATE_LABELS.saved;
  const d = new Date(at);
  const clock = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const sameDay = now == null || new Date(now).toDateString() === d.toDateString();
  return sameDay ? `已保存到 ${clock}` : `已保存到 ${d.toLocaleDateString()} ${clock}`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return '未知';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function formatRelative(at: number | null | undefined, now: number): string {
  if (at == null) return '尚无记录';
  const secs = Math.max(0, Math.round((now - at) / 1000));
  if (secs < 5) return '刚刚';
  if (secs < 60) return `${secs} 秒前`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} 分钟前`;
  return `${Math.round(mins / 60)} 小时前`;
}

/** 把任意抛出的错误归一成结构化回执（不许把 DOMException 原文丢给用户）。 */
export function receiptFromError(
  error: unknown,
  directory: string,
  fallbackUsed: boolean,
  at: number,
  fallbackReason?: StorageErrorReceipt,
): StorageErrorReceipt {
  const name = (error as { name?: string } | null)?.name ?? '';
  const rawCode = (error as { code?: string } | null)?.code ?? '';
  const message = (error as { message?: string } | null)?.message ?? String(error);
  let code: StorageErrorCode = 'write_failed';
  if (rawCode && (Object.keys(STORAGE_ERROR_HINTS) as string[]).includes(rawCode)) {
    code = rawCode as StorageErrorCode;
  } else if (name === 'QuotaExceededError' || name === 'NS_ERROR_DOM_QUOTA_REACHED') {
    code = 'quota_exceeded';
  } else if (name === 'ReadOnlyError' || name === 'NotReadableError') {
    code = 'read_only';
  } else if (name === 'NotAllowedError' || name === 'SecurityError') {
    code = 'permission_denied';
  } else if (name === 'NoSpaceError' || /no space left|disk full|ENOSPC/i.test(message)) {
    code = 'disk_full';
  }
  return {
    code,
    message: fallbackReason ? `${fallbackReason.message}；备份写入也失败：${message}` : message,
    hint: fallbackUsed
      ? `主盘与备用目录都不可写：${STORAGE_ERROR_HINTS[code]}`
      : STORAGE_ERROR_HINTS[code],
    directory,
    fallbackUsed,
    retryable: true,
    at,
  };
}

/** 预检不通过 → 回执（「空间不足」与「只读」分开报，别混成一句「保存失败」）。 */
export function receiptFromPreflight(
  pre: PreflightResult,
  at: number,
): StorageErrorReceipt {
  const short =
    pre.freeBytes != null && pre.requiredBytes != null && pre.freeBytes < pre.requiredBytes;
  const code: StorageErrorCode = pre.writable === false ? 'read_only' : 'disk_full';
  const detail = short
    ? `可用空间 ${formatBytes(pre.freeBytes)} < 需要 ${formatBytes(pre.requiredBytes)}`
    : (pre.reason ?? '目标盘当前不可写');
  return {
    code,
    message: `无法保存：${detail}`,
    hint: STORAGE_ERROR_HINTS[code],
    directory: pre.directory,
    fallbackUsed: false,
    retryable: true,
    at,
  };
}

// --------------------------------------------------------------------------- //
// W2 + W4 状态机
// --------------------------------------------------------------------------- //
export interface AutosaveOptions<T> {
  /** 稳定键（同一份草稿的不同界面共用同一个 key 才能续上）。 */
  key: string;
  value: T;
  /** 真正落盘的动作；目录由 hook 决定（主盘或备选盘）。 */
  save: (value: T, ctx: SaveContext) => Promise<SaveOutcome | void> | SaveOutcome | void;
  /** 写前预检：可用空间与可写性。拿不到信息就返回 undefined，不要编。 */
  preflight?: () => Promise<PreflightResult | void> | PreflightResult | void;
  /** 停止输入后多久落盘（默认 800ms）。 */
  delayMs?: number;
  /** 最长不落盘间隔（默认 5000ms：既「实时」又不至于每键一次）。 */
  intervalMs?: number;
  /** 降级后多久重试一次（默认 3000ms）。 */
  retryDelayMs?: number;
  /** 备选目录（W4：空间不足 / 只读时的降级去处）。 */
  fallbackDirectory?: string;
  /** 关掉自动保存（例如只读态）。 */
  enabled?: boolean;
  /** 注入时钟，便于测试。 */
  now?: () => number;
}

export interface AutosaveSummary {
  lastSavedAt: number | null;
  location: string | null;
  bytes: number | null;
  freeBytes: number | null;
  directory: string | null;
  pendingChanges: number;
  stateLabel: string;
}

export interface AutosaveHandle {
  state: SaveState;
  savedAt: number | null;
  /** 存储位置（给人看的路径）。 */
  location: string | null;
  /** 占用空间（字节）。 */
  bytes: number | null;
  /** 最近一次预检看到的可用空间。 */
  freeBytes: number | null;
  storage: StorageState;
  error: StorageErrorReceipt | null;
  /** 排队中（尚未落盘）的改动条数 —— 降级期间也只会增长不会被清零。 */
  pendingChanges: number;
  /** 保存时间轴（最近 20 条）。 */
  history: SaveRecord[];
  /** 立即落盘（不等 debounce）。 */
  saveNow: () => Promise<void>;
  /** 失败后重试（降级恢复也走它）。 */
  retry: () => Promise<void>;
  /** 供指示器一次性取值。 */
  summary: AutosaveSummary;
}

const HISTORY_LIMIT = 20;

export function useAutosave<T>(options: AutosaveOptions<T>): AutosaveHandle {
  const {
    key, value, save, preflight,
    delayMs = 800, intervalMs = 5000, retryDelayMs = 3000,
    fallbackDirectory, enabled = true, now = Date.now,
  } = options;

  const [state, setState] = useState<SaveState>('saved');
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [location, setLocation] = useState<string | null>(null);
  const [bytes, setBytes] = useState<number | null>(null);
  const [freeBytes, setFreeBytes] = useState<number | null>(null);
  const [storage, setStorage] = useState<StorageState>('ok');
  const [error, setError] = useState<StorageErrorReceipt | null>(null);
  const [pendingChanges, setPendingChanges] = useState(0);
  const [history, setHistory] = useState<SaveRecord[]>([]);

  const valueRef = useRef(value);
  valueRef.current = value;
  const savedSerializedRef = useRef<string>(serialize(value));
  const busyRef = useRef(false);
  const primaryDirectory = useMemo(() => `local:${key}`, [key]);

  const pushHistory = useCallback((next: SaveState, at: number) => {
    setHistory((prev) => [
      ...prev.slice(-(HISTORY_LIMIT - 1)),
      { state: next, at, label: next === 'saved' ? formatSavedAt(at, at) : SAVE_STATE_LABELS[next] },
    ]);
  }, []);

  /** 一次落盘尝试。返回 true = 已落盘（可能落在备选目录，storage 会是 degraded）。 */
  const attempt = useCallback(async (target: T, at: number): Promise<boolean> => {
    setState('saving');
    const pre = await Promise.resolve(preflight ? preflight() : undefined);
    const directory = pre?.directory ?? primaryDirectory;
    if (pre) setFreeBytes(pre.freeBytes ?? null);

    const blocked = !!pre && (
      pre.writable === false
      || (pre.freeBytes != null && pre.requiredBytes != null && pre.freeBytes < pre.requiredBytes)
    );

    if (blocked) {
      const base = receiptFromPreflight(pre as PreflightResult, at);
      if (fallbackDirectory) {
        try {
          const out = (await Promise.resolve(
            save(target, { key, directory: fallbackDirectory, fallback: true }),
          )) ?? {};
          setLocation(out.location ?? fallbackDirectory);
          setBytes(out.bytes ?? null);
          setStorage('degraded');
          setError({
            ...base,
            fallbackUsed: true,
            hint: `${base.hint}已先改存到备用目录：${fallbackDirectory}；`
              + '恢复可用后会自动回到主盘并补写全部积压改动。',
          });
          return true;
        } catch (writeError) {
          setStorage('degraded');
          setError(receiptFromError(writeError, fallbackDirectory, true, at, base));
          return false;
        }
      }
      setStorage('degraded');
      setError(base);
      return false;
    }

    try {
      const out = (await Promise.resolve(save(target, { key, directory, fallback: false }))) ?? {};
      setLocation(out.location ?? directory);
      setBytes(out.bytes ?? null);
      setStorage('ok');
      setError(null);
      return true;
    } catch (writeError) {
      const receipt = receiptFromError(writeError, directory, false, at);
      if (isStorageFault(receipt.code) && fallbackDirectory) {
        try {
          const out = (await Promise.resolve(
            save(target, { key, directory: fallbackDirectory, fallback: true }),
          )) ?? {};
          setLocation(out.location ?? fallbackDirectory);
          setBytes(out.bytes ?? null);
          setStorage('degraded');
          setError({
            ...receipt,
            fallbackUsed: true,
            hint: `${receipt.hint}已先改存到备用目录：${fallbackDirectory}。`,
          });
          return true;
        } catch (retryError) {
          setStorage('degraded');
          setError(receiptFromError(retryError, fallbackDirectory, true, at, receipt));
          return false;
        }
      }
      if (isStorageFault(receipt.code)) setStorage('degraded');
      setError(receipt);
      return false;
    }
  }, [fallbackDirectory, key, preflight, primaryDirectory, save]);

  /**
   * 落盘循环：写成功后再看有没有「写期间又改了」——有就继续写，
   * 这样降级期间积压的改动在恢复后会被**一次性补写干净**（W4 验收 ④）。
   */
  const flush = useCallback(async (): Promise<void> => {
    if (!enabled || busyRef.current) return;
    busyRef.current = true;
    try {
      for (let round = 0; round < 8; round += 1) {
        const target = valueRef.current;
        const targetSerialized = serialize(target);
        if (targetSerialized === savedSerializedRef.current) {
          setState('saved');
          break;
        }
        const at = now();
        const ok = await attempt(target, at);
        if (!ok) {
          setState('error');
          pushHistory('error', at);
          return;   // 保留积压改动，等 retry / 自动重试
        }
        savedSerializedRef.current = targetSerialized;
        setSavedAt(at);
        setPendingChanges(0);
        setState('saved');
        pushHistory('saved', at);
        if (serialize(valueRef.current) === targetSerialized) break;
      }
    } finally {
      busyRef.current = false;
    }
  }, [attempt, enabled, now, pushHistory]);

  const saveNow = useCallback(async () => { await flush(); }, [flush]);
  const retry = useCallback(async () => { await flush(); }, [flush]);

  // 值变化 → 进 dirty（并累计排队条数；降级期间只增不减，绝不静默丢弃）
  const serialized = useMemo(() => serialize(value), [value]);
  useEffect(() => {
    if (!enabled) return;
    if (serialized === savedSerializedRef.current) return;
    setState((prev) => (prev === 'saving' ? prev : 'dirty'));
    setPendingChanges((n) => n + 1);
  }, [serialized, enabled]);

  // debounce：停止输入 delayMs 后落盘
  useEffect(() => {
    if (!enabled || state !== 'dirty') return undefined;
    const timer = setTimeout(() => { void flush(); }, delayMs);
    return () => clearTimeout(timer);
  }, [state, serialized, delayMs, enabled, flush]);

  // 最长不落盘间隔：连续输入时也每隔 intervalMs 落一次
  useEffect(() => {
    if (!enabled || state !== 'dirty') return undefined;
    const timer = setInterval(() => { void flush(); }, intervalMs);
    return () => clearInterval(timer);
  }, [state, intervalMs, enabled, flush]);

  // 降级后自动重试：盘一恢复就自动回到主盘，不需要用户再点
  useEffect(() => {
    if (!enabled || storage !== 'degraded') return undefined;
    const timer = setInterval(() => { void flush(); }, retryDelayMs);
    return () => clearInterval(timer);
  }, [storage, retryDelayMs, enabled, flush]);

  const summary = useMemo<AutosaveSummary>(() => ({
    lastSavedAt: savedAt,
    location,
    bytes,
    freeBytes,
    directory: location,
    pendingChanges,
    stateLabel: state === 'saved' ? formatSavedAt(savedAt, now()) : SAVE_STATE_LABELS[state],
  }), [savedAt, location, bytes, freeBytes, pendingChanges, state, now]);

  return {
    state, savedAt, location, bytes, freeBytes, storage, error,
    pendingChanges, history, saveNow, retry, summary,
  };
}

// --------------------------------------------------------------------------- //
// W1 接入声明
// --------------------------------------------------------------------------- //
export const BASE_CAPABILITIES = [
  'realtime_save', 'audit_trail', 'local_first', 'error_receipt',
] as const;

export type BaseCapability = (typeof BASE_CAPABILITIES)[number];

export interface BaseDeclaration {
  /** 界面标识（工作台 / 个人空间 / 标签页名），用于接入清单与留痕。 */
  surface: string;
  /** 声明的能力子集；省略 = 声明全部四项（契约里的 shorthand）。 */
  capabilities?: readonly string[];
}

export interface AuditFrame {
  action: string;
  details: Record<string, unknown>;
  at: number;
  status: 'sent' | 'pending-sink';
}

export interface BaseHandle {
  surface: string;
  /** 实际声明的能力（已过滤掉未知 id）。 */
  capabilities: BaseCapability[];
  /** 声明里出现但契约里没有的 id（Source 里会有，运行期也如实报告）。 */
  unknownCapabilities: string[];
  /** 留痕：交给调用方注入的 sink 入链；未注入时只在本地账本排队。 */
  audit: {
    record: (action: string, details?: Record<string, unknown>) => AuditFrame;
    frames: AuditFrame[];
  };
  /** 本地优先：本页当前落盘位置与占用（来自 useAutosave，不是另查一份）。 */
  storage: { state: StorageState; location: string | null; bytes: number | null };
  /** 统一错误回执：任何失败都经它归一，各页不要自己拼提示。 */
  errors: {
    receipt: (error: unknown, options?: { directory?: string; fallbackUsed?: boolean }) => StorageErrorReceipt;
    last: StorageErrorReceipt | null;
  };
}

export interface UseBaseOptions {
  /** 留痕落地通道（服务端审计链）。不注入 = 只在本地排队，标 pending-sink。 */
  record?: (frame: AuditFrame) => void | Promise<void>;
  /** 本地优先：把占位信息从 useAutosave 递进来，避免两处各查一份。 */
  storage?: { state: StorageState; location: string | null; bytes: number | null };
  /** 存下的错误回执（通常来自 useAutosave.error）。 */
  error?: StorageErrorReceipt | null;
  now?: () => number;
}

/**
 * 统一接入声明。页面里写 `useBase({ surface: 'workbench' })` 即视为「已接入基座」——
 * 后端 `services/quality/base_contract.py` 扫的就是这个标记。
 */
export function useBase(declaration: BaseDeclaration, options: UseBaseOptions = {}): BaseHandle {
  const { surface } = declaration;
  const { record, storage, error: externalError, now = Date.now } = options;
  const framesRef = useRef<AuditFrame[]>([]);
  const [lastError, setLastError] = useState<StorageErrorReceipt | null>(externalError ?? null);

  const declared = useMemo(() => {
    const requested = declaration.capabilities ?? BASE_CAPABILITIES;
    const known = requested.filter((c): c is BaseCapability =>
      (BASE_CAPABILITIES as readonly string[]).includes(c));
    const unknown = requested.filter(
      (c) => !(BASE_CAPABILITIES as readonly string[]).includes(c));
    // 去重但保持契约顺序，便于与后端 capabilities 比对
    const ordered = BASE_CAPABILITIES.filter((c) => known.includes(c));
    return { capabilities: [...ordered], unknown };
  }, [declaration.capabilities]);

  const record2 = useCallback((action: string, details: Record<string, unknown> = {}): AuditFrame => {
    const frame: AuditFrame = {
      action, details, at: now(), status: record ? 'sent' : 'pending-sink',
    };
    framesRef.current = [...framesRef.current, frame];
    if (record) {
      void Promise.resolve(record(frame)).catch(() => {
        // 入链失败不阻断编辑：把状态退回 pending-sink，留痕欠账可见，绝不假装成功。
        frame.status = 'pending-sink';
      });
    }
    return frame;
  }, [now, record]);

  const receipt = useCallback(
    (err: unknown, opts: { directory?: string; fallbackUsed?: boolean } = {}) => {
      const made = receiptFromError(err, opts.directory ?? 'local', opts.fallbackUsed ?? false, now());
      setLastError(made);
      return made;
    },
    [now],
  );

  useEffect(() => {
    if (externalError) setLastError(externalError);
  }, [externalError]);

  return {
    surface,
    capabilities: declared.capabilities,
    unknownCapabilities: declared.unknown,
    audit: { record: record2, frames: framesRef.current },
    storage: storage ?? { state: 'ok', location: null, bytes: null },
    errors: { receipt, last: lastError },
  };
}
