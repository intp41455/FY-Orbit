// W9 资产库 · 单个资产卡。
// 图片走真缩略图（鉴权 raw 端点）；音频/音乐走真实 <audio> 播放钮 + 波形占位条
// （波形是**样式占位**，不是伪造的音频波形数据，卡上明确标注）。
import { assetRawUrl, type AssetRecord, type MountRole } from '../../api/assets';
import { AssetThumbnail } from './AssetDropzone';
import { assetOriginLabel, formatBytes, isMountedAt, KIND_LABEL, mountOptionsFor } from './assetFormat';

export interface AssetCardProps {
  asset: AssetRecord;
  busy?: boolean;
  onDelete: (asset: AssetRecord) => void;
  onMount: (asset: AssetRecord, role: MountRole) => void;
}

export function AssetCard({ asset, busy = false, onDelete, onMount }: AssetCardProps) {
  const raw = assetRawUrl(asset);
  const playable = asset.kind === 'audio' || asset.kind === 'music';
  const mounts = mountOptionsFor(asset.kind);
  const mounted = (['wall', 'bgm'] as const).find((role) => isMountedAt(asset, role));

  return (
    <article className="card asset-card" data-testid={`asset-${asset.id}`}>
      <div className="asset-card-media">
        {asset.kind === 'image' ? (
          <AssetThumbnail src={raw} alt={asset.name} />
        ) : playable ? (
          <div className="asset-wave" data-testid="asset-wave" aria-hidden="true">
            <span className="asset-wave-bar" />
            <span className="asset-wave-bar" />
            <span className="asset-wave-bar" />
            <span className="asset-wave-bar" />
            <span className="asset-wave-bar" />
          </div>
        ) : (
          <div className="asset-doc" aria-hidden="true">
            📄
          </div>
        )}
      </div>

      <strong className="asset-name" title={asset.name}>
        {asset.name}
      </strong>
      <div className="muted asset-meta">
        {KIND_LABEL[asset.kind]} · {formatBytes(asset.size)} · {assetOriginLabel(asset)}
      </div>
      {playable && (
        <audio controls preload="none" src={raw} data-testid={`asset-audio-${asset.id}`}>
          你的浏览器不支持音频播放。
        </audio>
      )}
      {playable && <span className="muted asset-hint">波形条为示意样式，非真实波形数据</span>}

      <div className="asset-card-actions">
        {mounts.wall && (
          <button
            type="button"
            className={mounted === 'wall' ? 'asset-btn active' : 'asset-btn'}
            data-testid={`asset-wall-${asset.id}`}
            disabled={busy}
            aria-pressed={mounted === 'wall'}
            onClick={() => onMount(asset, mounted === 'wall' ? '' : 'wall')}
          >
            {mounted === 'wall' ? '✓ 已挂小屋墙' : '🖼 挂小屋墙'}
          </button>
        )}
        {mounts.bgm && (
          <button
            type="button"
            className={mounted === 'bgm' ? 'asset-btn active' : 'asset-btn'}
            data-testid={`asset-bgm-${asset.id}`}
            disabled={busy}
            aria-pressed={mounted === 'bgm'}
            onClick={() => onMount(asset, mounted === 'bgm' ? '' : 'bgm')}
          >
            {mounted === 'bgm' ? '✓ 小屋 BGM' : '🎵 设为小屋 BGM'}
          </button>
        )}
        <button
          type="button"
          className="asset-btn danger"
          data-testid={`asset-delete-${asset.id}`}
          disabled={busy}
          onClick={() => onDelete(asset)}
        >
          删除
        </button>
      </div>
    </article>
  );
}