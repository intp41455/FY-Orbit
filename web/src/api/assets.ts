// W9 个人资产库 API 客户端（对应 api/routes/assets.py）。
//
// 诚实契约（总纲铁律 3 / 冻结契约 §11）：
//  - 图片通道未配置时后端返回 503 + code=image_provider_not_configured，页面必须
//    **原样展示**「未接入生成服务」，绝不用占位图冒充「已生成」；
//  - 音乐通道（本地合成器）永远 configured:true，meta.provider='local_synth'
//    要如实标注，**不得**标成「模型生成」；
//  - 字节一律走裸字节上传（不引 multipart），会话 Cookie 同源自动带，CSRF 头必发；
//  - raw_url 是唯一字节出口（鉴权后端点代理），前端拿不到磁盘绝对路径。
import { buildApiUrl, getCsrfToken } from './client';

const BASE = '/api/assets';

export type AssetKind = 'image' | 'audio' | 'music' | 'doc';

/** 小屋挂载位：图片挂墙（wall），音乐/音频当 BGM（bgm），空串为不挂。 */
export type MountRole = '' | 'wall' | 'bgm';

export interface AssetRecord {
  id: string;
  owner_id: string;
  kind: AssetKind;
  name: string;
  mime: string;
  size: number;
  meta: Record<string, unknown>;
  version: number;
  created_at: string | null;
  /** 鉴权后的字节出口（相对路径，前端据此拼绝对 URL）。 */
  raw_url: string;
  /** 相对 FY_ASSETS_DIR 的路径，仅供显示，不是可直连地址。 */
  storage_rel: string;
}

export interface AssetListResponse {
  assets: AssetRecord[];
  count: number;
  kinds: AssetKind[];
  max_bytes_by_kind: Record<string, number>;
}

export interface ChannelStatus {
  channel: string;
  configured: boolean;
  source: string;
  model: string;
  base_url: string;
  has_api_key: boolean;
  credential_fields: string[];
  credentials_present: Record<string, boolean>;
  storage: string;
  persist_restart: boolean;
  detail: string;
}

export interface MusicChannelStatus {
  channel: 'music';
  configured: boolean;
  provider: string;
  detail: string;
}

export interface MoodOption {
  id: string;
  label: string;
  tempo_bpm: number;
  waveform: string;
}

export interface ChannelOverview {
  channels: ChannelStatus[];
  music: MusicChannelStatus;
  moods: MoodOption[];
}

export interface MountedResponse {
  role: string;
  assets: AssetRecord[];
  count: number;
}

export interface GenerateImageResult {
  asset: AssetRecord;
}

/** 统一的错误信封错误（与 client.ts 的 ApiError 语义一致，便于页面展示 code）。 */
export class AssetApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!res.ok) {
    const envelope = parsed as { error?: { code?: string; message?: string } } | null;
    throw new AssetApiError(
      res.status,
      envelope?.error?.code ?? `http_${res.status}`,
      envelope?.error?.message ?? `请求失败（HTTP ${res.status}）`,
    );
  }
  return parsed as T;
}

function csrfHeaders(): Record<string, string> {
  const csrf = getCsrfToken();
  return csrf ? { 'X-CSRF-Token': csrf } : {};
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(buildApiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  return jsonOrThrow<T>(res);
}

/** 资产字节的绝对 URL（供 <img src> / <audio src> 用；仍走鉴权 Cookie）。 */
export function assetRawUrl(asset: Pick<AssetRecord, 'raw_url'>): string {
  return buildApiUrl(asset.raw_url);
}

export const assetsApi = {
  list: (kind?: AssetKind) =>
    fetch(buildApiUrl(BASE, kind ? { kind } : undefined), { credentials: 'same-origin' }).then((r) =>
      jsonOrThrow<AssetListResponse>(r),
    ),

  /** 裸字节上传：后端按 query 的 name/kind 判定类型与上限。 */
  upload: (file: File, kind: AssetKind) => {
    const url = buildApiUrl(BASE, { name: file.name, kind });
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', ...csrfHeaders() },
      credentials: 'same-origin',
      body: file,
    })
      .then((r) => jsonOrThrow<{ asset: AssetRecord }>(r))
      .then((r) => r.asset);
  },

  remove: (assetId: string) =>
    fetch(buildApiUrl(`${BASE}/${encodeURIComponent(assetId)}`), {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: csrfHeaders(),
    }).then((r) => jsonOrThrow<AssetRecord & { file_removed: boolean; deleted: boolean }>(r)),

  setMount: (assetId: string, role: MountRole) =>
    fetch(buildApiUrl(`${BASE}/${encodeURIComponent(assetId)}/mount`), {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify({ role }),
    }).then((r) => jsonOrThrow<{ asset: AssetRecord }>(r)),

  channels: () =>
    fetch(buildApiUrl(`${BASE}/channels`), { credentials: 'same-origin' }).then((r) =>
      jsonOrThrow<ChannelOverview>(r),
    ),

  configureChannel: (channel: 'image' | 'tts', values: { base_url: string; api_key: string; model?: string }) =>
    postJson<ChannelStatus>(`${BASE}/channels/${channel}/configure`, values),

  forgetChannel: (channel: 'image' | 'tts') =>
    fetch(buildApiUrl(`${BASE}/channels/${channel}`), {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: csrfHeaders(),
    }).then((r) => jsonOrThrow<{ channel: string; forgotten: boolean }>(r)),

  generateImage: (body: { prompt: string; size?: string; model?: string }) =>
    postJson<GenerateImageResult>(`${BASE}/generate/image`, body),

  generateMusic: (body: { mood: string; seconds?: number; name?: string; seed?: number }) =>
    postJson<GenerateImageResult>(`${BASE}/generate/music`, body),

  generateSpeech: (body: { text: string; voice?: string; name?: string }) =>
    postJson<GenerateImageResult>(`${BASE}/generate/speech`, body),

  mounted: (role: Exclude<MountRole, ''>) =>
    fetch(buildApiUrl(`${BASE}/cabin/mounted`, { role }), { credentials: 'same-origin' }).then((r) =>
      jsonOrThrow<MountedResponse>(r),
    ),
};