import { request } from '../../../api/client';

/**
 * W11 · 室外家具移除黑名单（对应 api/routes/cabin.py 的 /exterior 端点）。
 *
 * 与室内 `cabinInteriorApi` 的差别：室内存整份 layout，室外只存「用户删掉了
 * 哪几件」。因为室外默认清单 `DEFAULT_PLACED_FURNITURE` 会随版本新增家具 ——
 * 若存整份清单，旧存档会把后来新增的家具永久藏掉，用户界面里也没有任何入口
 * 能把它找回来。黑名单只表达「明确不要这几件」，新增家具自动出现。
 *
 * 持久化策略与室内一致：后端为准，localStorage 双写做秒开；
 * 失败时返回 `persisted=false`，由 UI 如实显示「仅本次会话有效」。
 */

const BASE = '/api/cabin';

/** 与后端 `EXTERIOR_REMOVABLE` 一一对应（由 test_removable_set_is_subset_of_frontend_default_placement 守同步）。 */
export const EXTERIOR_STORAGE_PREFIX = 'fy.cabin.exterior.v1';

export interface ExteriorResponse {
  house_id: string;
  removed_ids: string[];
  version: number;
}

export function exteriorStorageKey(houseId: string): string {
  return `${EXTERIOR_STORAGE_PREFIX}.${houseId}`;
}

function safeStorage(): Storage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage;
  } catch {
    return null;
  }
}

/** 本地缓存的移除清单；解析失败返回 null（不假装是空清单以外的任何东西）。 */
export function loadLocalRemoved(houseId: string, storage: Storage | null = safeStorage()): string[] | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(exteriorStorageKey(houseId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((x): x is string => typeof x === 'string');
  } catch {
    return null;
  }
}

/** 返回 false = 存储不可用（隐私模式/配额满），调用方须如实提示。 */
export function saveLocalRemoved(houseId: string, removedIds: string[], storage: Storage | null = safeStorage()): boolean {
  if (!storage) return false;
  try {
    storage.setItem(exteriorStorageKey(houseId), JSON.stringify(removedIds));
    return true;
  } catch {
    return false;
  }
}

export function fetchExterior(houseId: string): Promise<ExteriorResponse> {
  return request<ExteriorResponse>(`${BASE}/exterior/${encodeURIComponent(houseId)}`);
}

/** 写入黑名单（乐观锁）。`expectedVersion=0` = 我确认后端还没有这条记录。 */
export function saveExterior(
  houseId: string,
  removedIds: string[],
  expectedVersion: number,
): Promise<ExteriorResponse> {
  return request<ExteriorResponse>(`${BASE}/exterior/${encodeURIComponent(houseId)}`, {
    method: 'PUT',
    body: { removed_ids: removedIds, expected_version: expectedVersion },
  });
}

/** 清空黑名单 → 全部室外家具回来。 */
export function deleteExterior(houseId: string, expectedVersion: number): Promise<ExteriorResponse> {
  return request<ExteriorResponse>(
    `${BASE}/exterior/${encodeURIComponent(houseId)}?expected_version=${expectedVersion}`,
    { method: 'DELETE' },
  );
}

/**
 * 读后端 → 落本地缓存。返回 `fromBackend=false` 表示后端还没有记录，
 * 调用方应回退到空黑名单（= 用完整默认清单）。
 */
export async function loadExteriorWithCache(
  houseId: string,
): Promise<{ removedIds: string[]; version: number; fromBackend: boolean }> {
  const res = await fetchExterior(houseId);
  saveLocalRemoved(houseId, res.removed_ids);
  return {
    removedIds: res.removed_ids,
    version: res.version,
    fromBackend: res.version > 0,
  };
}

/**
 * 写黑名单：先落本地（刷新不闪），再 PUT 后端。
 * 后端失败时抛 ApiError 由 UI 如实展示，不假装已保存。
 */
export async function persistRemoved(
  houseId: string,
  removedIds: string[],
  expectedVersion: number,
): Promise<{
  removedIds: string[];
  version: number;
  /** true = 后端未存住（UI须显示「仅本次会话有效」）。 */
  localOnly: boolean;
  /** true = localStorage 也没写成，改动完全无处留存，UI须给出更强提示。 */
  localFailed: boolean;
}> {
  // 先落本地，保证刷新不闪；写不进（隐私模式/配额满）时如实标记。
  const localOk = saveLocalRemoved(houseId, removedIds);
  try {
    const res = await saveExterior(houseId, removedIds, expectedVersion);
    // 后端为准：把服务端归一化后的结果（去重排序）写回本地。
    saveLocalRemoved(houseId, res.removed_ids);
    return {
      removedIds: res.removed_ids,
      version: res.version,
      localOnly: false,
      localFailed: !localOk,
    };
  } catch {
    // 后端没存住。无论本地是否写成功，都必须让 UI 知道 —— 不冒充已同步。
    return {
      removedIds,
      version: expectedVersion,
      localOnly: true,
      localFailed: !localOk,
    };
  }
}

/**
 * 从默认清单里剔除已移除项。
 * @param defaults 前端 DEFAULT_PLACED_FURNITURE 的可读副本
 * @param removedIds 已移除的 furniture_id 集合
 */
export function filterRemoved<T extends { furniture_id: string }>(
  defaults: readonly T[],
  removedIds: readonly string[],
): T[] {
  if (!removedIds.length) return [...defaults];
  const gone = new Set(removedIds);
  return defaults.filter((d) => !gone.has(d.furniture_id));
}
