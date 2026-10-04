// W9 资产库纯函数：字节格式化、来源诚实标注、生成/挂载动作的可用性判定。
// 全部无副作用、可单测（assetsFormat.test.ts 覆盖）。
import type { AssetKind, AssetRecord, ChannelStatus } from '../../api/assets';

export const KIND_LABEL: Record<AssetKind, string> = {
  image: '图片',
  audio: '音频',
  music: '音乐',
  doc: '文档',
};

export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 资产来源的诚实标注。
 * 本地合成 = 「本地合成」；调用外部生成服务 = 「外部服务生成」并附模型名。
 * 绝不使用「模型生成」这种会让人误以为是本地大模型的说法（除非确有 provider 标注）。
 */
export function assetOriginLabel(asset: AssetRecord): string {
  const provider = typeof asset.meta.provider === 'string' ? asset.meta.provider : '';
  const model = typeof asset.meta.model === 'string' ? asset.meta.model : '';
  if (provider === 'local_synth') {
    const mood = typeof asset.meta.mood_label === 'string' ? asset.meta.mood_label : '';
    return mood ? `本地合成 · ${mood}` : '本地合成（代码合成，非 AI 作曲）';
  }
  if (provider === 'openai_compatible_images' || provider === 'openai_compatible_speech') {
    return model ? `外部服务生成 · ${model}` : '外部服务生成';
  }
  return '本地上传';
}

/** meta.cabin_mount 是否等于该角色（未挂载返回 false，不是 undefined）。 */
export function isMountedAt(asset: AssetRecord, role: 'wall' | 'bgm'): boolean {
  return asset.meta.cabin_mount === role;
}

/**
 * 通道状态 → 徽标文案。**未接入时必须显示「未接入」**，不允许出现
 * 「已就绪 / 可用」这类会让用户以为能用的假状态。
 */
export function channelBadge(status: ChannelStatus): { label: string; tone: 'ok' | 'warn' | 'idle' } {
  if (!status.configured) return { label: '未接入', tone: 'warn' };
  if (!status.has_api_key && !/127\.0\.0\.1|localhost/.test(status.base_url)) {
    return { label: '已配置缺 Key', tone: 'warn' };
  }
  return { label: '已接入', tone: 'ok' };
}

/** 该资产能否挂到墙面（图片才成画）与能否当 BGM（音频/音乐）。 */
export function mountOptionsFor(kind: AssetKind): { wall: boolean; bgm: boolean } {
  return { wall: kind === 'image', bgm: kind === 'audio' || kind === 'music' };
}

/** 上传前的前端预检（后端仍会再校验一次，这里只为给出即时提示）。 */
export function exceedsLimit(size: number, maxBytesByKind: Record<string, number>, kind: AssetKind): boolean {
  const limit = maxBytesByKind[kind];
  return typeof limit === 'number' ? size > limit : false;
}

/** 从文件扩展名猜 kind（用户拖入文件时直接给出正确分类，避免用户手选）。 */
export function kindFromFileName(name: string): AssetKind {
  const lower = name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp)$/.test(lower)) return 'image';
  if (/\.(wav|mp3|ogg|m4a|flac|aac)$/.test(lower)) return 'music';
  if (/\.(txt|md|pdf|docx)$/.test(lower)) return 'doc';
  return 'doc';
}