import { request } from '../../../api/client';
import {
  sanitizeLayout,
  type InteriorLayout,
} from './interiorLayout';

/**
 * W1 · 室内布置后端通道（对应 api/routes/cabin.py）
 *
 * 持久化策略（任务书第 4 条）：**后端为准，localStorage 双写做秒开**。
 * 读：先取后端；后端 `defaulted=true` 时用本地默认布局并回写后端。
 * 写：先落 localStorage（保证刷新不闪），再 PUT 后端；失败时**明确报错**，
 *     绝不假装已保存 —— 调用方把 `persisted=false` 展示成「仅本次会话有效」。
 *
 * 诚实原则：网络失败 / 409 冲突 / 401 未登录都通过 ApiError 抛出，
 * 由 UI 显示真实原因，不静默吞掉。
 */

const BASE = '/api/cabin';

export interface FurnitureCatalogResponse {
  items: {
    id: string;
    label: string;
    size_cells: { w: number; h: number };
    mount: 'floor' | 'wall' | 'ceiling';
  }[];
  count: number;
  max_items: number;
  max_layout_bytes: number;
  house_ids: string[];
}

export interface InteriorResponse {
  house_id: string;
  layout: { houseId: string; items: unknown[]; version: number };
  version: number;
  /** true = 后端尚无记录，客户端应渲染自己的默认布局。 */
  defaulted: boolean;
}

/** 拉取家具注册表（后端是契约真源，用于自检与展示）。 */
export function fetchFurnitureCatalog(): Promise<FurnitureCatalogResponse> {
  return request<FurnitureCatalogResponse>(`${BASE}/furniture`);
}

/** 读取某房屋模板的布置；`defaulted=true` 表示后端还没有存档。 */
export function fetchInterior(houseId: string): Promise<InteriorResponse> {
  return request<InteriorResponse>(`${BASE}/interiors/${encodeURIComponent(houseId)}`);
}

/**
 * 写入布置（乐观锁）。
 * `expectedVersion=0` 表示「我确认后端还没有这条记录」。
 * 版本不匹配时后端返回 409，这里原样抛出由 UI 提示「另一处已修改」。
 */
export function saveInterior(
  houseId: string,
  layout: InteriorLayout,
  expectedVersion: number,
): Promise<InteriorResponse> {
  return request<InteriorResponse>(`${BASE}/interiors/${encodeURIComponent(houseId)}`, {
    method: 'PUT',
    body: { layout: sanitizeLayout(layout, houseId), expected_version: expectedVersion },
  });
}

/** 删除布置 → 回到默认布局。 */
export function deleteInterior(houseId: string, expectedVersion: number): Promise<{ house_id: string; deleted: boolean }> {
  return request<{ house_id: string; deleted: boolean }>(
    `${BASE}/interiors/${encodeURIComponent(houseId)}?expected_version=${expectedVersion}`,
    { method: 'DELETE' },
  );
}

/**
 * 同步入口：读后端 → 清洗 → 落本地缓存。
 * 返回 `{ layout, fromBackend }`；`fromBackend=false` 表示后端还没有，
 * 调用方应使用 `defaultLayout()` 并（可选）立刻写回后端。
 */
export async function loadInteriorWithCache(
  houseId: string,
): Promise<{ layout: InteriorLayout | null; fromBackend: boolean }> {
  const res = await fetchInterior(houseId);
  if (res.defaulted) return { layout: null, fromBackend: false };
  return { layout: sanitizeLayout(res.layout, houseId), fromBackend: true };
}
