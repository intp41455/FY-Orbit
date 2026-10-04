// W9 小屋联动 · 墙面挂画 + BGM 控制。
//
// **文件边界（任务书 §1.4 的明确要求）**：本组件只读资产库接口并把结果
// 渲染成 DOM 覆盖层，**完全不碰 cabinScene / cabinPixels / cabinInterior /
// gameplay**（W1/W2 产物）。像素风画框由 CSS + 图片像素化渲染实现，
// 挂画位与小屋舞台用百分比定位对齐，不侵入场景渲染代码。
//
// 诚实：资产库没挂东西时显示「还没挂画」，不塞默认图片；BGM 播放失败
// （文件丢失/被删）如实提示，不假装在播。
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { assetsApi, assetRawUrl, type AssetRecord } from '../../api/assets';
import { formatBytes } from './assetFormat';

export interface CabinMediaController {
  wall: AssetRecord | null;
  bgm: AssetRecord | null;
  reload: () => void;
  clearBgm: () => void;
  toggleBgm: () => void;
  bgmPlaying: boolean;
  bgmError: string;
  loading: boolean;
  /** 挂在 <audio> 元素上，供 toggleBgm 控制播放。 */
  audioRef: RefObject<HTMLAudioElement | null>;
  /** 由 <audio> 元素的 onPlay/onPause/onError 回写播放态与错误。 */
  syncPlayback: (playing: boolean) => void;
  reportBgmError: (message: string) => void;
}

export function useCabinMedia(): CabinMediaController {
  const [wall, setWall] = useState<AssetRecord | null>(null);
  const [bgm, setBgm] = useState<AssetRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [bgmPlaying, setBgmPlaying] = useState(false);
  const [bgmError, setBgmError] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setBgmError('');
    const [wallRes, bgmRes] = await Promise.allSettled([
      assetsApi.mounted('wall'),
      assetsApi.mounted('bgm'),
    ]);
    setWall(wallRes.status === 'fulfilled' ? (wallRes.value.assets[0] ?? null) : null);
    setBgm(bgmRes.status === 'fulfilled' ? (bgmRes.value.assets[0] ?? null) : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleBgm = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !bgm) return;
    if (audio.paused) {
      audio.play().catch((err: unknown) => {
        setBgmError(err instanceof Error ? err.message : 'BGM 播放失败');
        setBgmPlaying(false);
      });
    } else {
      audio.pause();
    }
  }, [bgm]);

  const clearBgm = useCallback(() => {
    audioRef.current?.pause();
    setBgmError('');
  }, []);

  return {
    wall,
    bgm,
    reload: load,
    clearBgm,
    toggleBgm,
    bgmPlaying,
    bgmError,
    loading,
    audioRef,
    syncPlayback: setBgmPlaying,
    reportBgmError: setBgmError,
  };
}

/** 墙面挂画覆盖层（DOM，不侵入 cabinScene）。 */
export function CabinWallArt({ asset }: { asset: AssetRecord | null }) {
  if (!asset) return null;
  return (
    <div className="cabin-wallart" data-testid="cabin-wallart">
      <div className="cabin-wallart-frame">
        <img src={assetRawUrl(asset)} alt={`小屋墙上的挂画：${asset.name}`} />
      </div>
    </div>
  );
}

/** 小屋 BGM 设置卡（任务书 §1.4：音乐 → 小屋 BGM 开关，HTMLAudio 播 raw 端点）。 */
export function CabinBgmCard({ controller }: { controller: CabinMediaController }) {
  const { bgm, toggleBgm, bgmPlaying, bgmError, loading, audioRef, syncPlayback, reportBgmError } =
    controller;
  const label = useMemo(() => (bgm ? bgm.name : ''), [bgm]);

  return (
    <section className="card asset-bgm-card" data-testid="w9-bgm-card" aria-label="小屋 BGM">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>🎵 小屋 BGM</strong>
        <span className="muted">来自个人资产库的音乐资产</span>
      </div>
      {loading && <span className="muted">正在读取已挂载的 BGM…</span>}
      {!loading && !bgm && (
        <p className="muted" data-testid="w9-bgm-empty">
          还没设置 BGM。到
          <a href="/private"> 私人空间 </a>
          的音乐模块点「设为小屋 BGM」。
        </p>
      )}
      {bgm && (
        <>
          <div className="muted" data-testid="w9-bgm-name">
            {label} · {formatBytes(bgm.size)}
          </div>
          <button
            type="button"
            className="cabin-btn"
            data-testid="w9-bgm-toggle"
            aria-pressed={bgmPlaying}
            onClick={toggleBgm}
          >
            {bgmPlaying ? '⏸ 暂停' : '▶️ 播放'}
          </button>
          {bgmError && (
            <p className="cabin-load-error" data-testid="w9-bgm-error" role="alert">
              BGM 播放失败：{bgmError}
            </p>
          )}
        </>
      )}
      {/* 单一音频出口：鉴权后的 raw 端点，循环播放，不预取。 */}
      {bgm && (
        <audio
          ref={audioRef}
          data-testid="w9-bgm-audio"
          src={assetRawUrl(bgm)}
          loop
          preload="none"
          onPlay={() => syncPlayback(true)}
          onPause={() => syncPlayback(false)}
          onError={() => reportBgmError('BGM 文件读取失败（可能已被删除）')}
          style={{ display: 'none' }}
        />
      )}
    </section>
  );
}

/** 墙面挂画设置卡：显示当前挂画与取消按钮。 */
export function CabinWallArtCard({
  controller,
  onUnmount,
}: {
  controller: CabinMediaController;
  onUnmount: (asset: AssetRecord) => void;
}) {
  const { wall, loading } = controller;
  return (
    <section className="card asset-wall-card" data-testid="w9-wall-card" aria-label="墙面挂画">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>🖼 墙面挂画</strong>
        <span className="muted">个人资产库的图片资产会渲染成像素风画框</span>
      </div>
      {loading && <span className="muted">正在读取已挂载的挂画…</span>}
      {!loading && !wall && (
        <p className="muted" data-testid="w9-wall-empty">
          墙上还是空的。到
          <a href="/private"> 私人空间 </a>
          的图片模块点「挂小屋墙」。
        </p>
      )}
      {wall && (
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className="muted" data-testid="w9-wall-name">
            {wall.name} · {formatBytes(wall.size)}
          </span>
          <button
            type="button"
            className="cabin-btn"
            data-testid="w9-wall-remove"
            onClick={() => onUnmount(wall)}
          >
            取下挂画
          </button>
        </div>
      )}
    </section>
  );
}