// W9 资产库 · 拖拽上传区。
// 上传走裸字节（后端不引 multipart），拖入文件即按扩展名猜 kind 并逐个上传。
// 诚实：超限文件**不上传**，就地报错并说明上限（后端仍会二次校验）。
import { useRef, useState } from 'react';
import type { AssetKind } from '../../api/assets';
import { assetRawUrl } from '../../api/assets';
import { exceedsLimit, KIND_LABEL, kindFromFileName } from './assetFormat';

export interface AssetDropzoneProps {
  maxBytesByKind: Record<string, number>;
  busy?: boolean;
  onUpload: (file: File, kind: AssetKind) => Promise<void>;
  onError: (message: string) => void;
}

export function AssetDropzone({ maxBytesByKind, busy = false, onUpload, onError }: AssetDropzoneProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);

  async function handleFiles(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      const kind = kindFromFileName(file.name);
      if (exceedsLimit(file.size, maxBytesByKind, kind)) {
        const limit = Math.round((maxBytesByKind[kind] ?? 0) / (1024 * 1024));
        onError(`${file.name} 超过${KIND_LABEL[kind]}上限 ${limit}MB，未上传`);
        continue;
      }
      try {
        await onUpload(file, kind);
      } catch (err) {
        onError(err instanceof Error ? err.message : String(err));
      }
    }
  }

  return (
    <div
      className={over ? 'card asset-dropzone over' : 'card asset-dropzone'}
      data-testid="asset-dropzone"
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        void handleFiles(e.dataTransfer.files);
      }}
    >
      <strong>⬆️ 拖入本地文件入库</strong>
      <p className="muted">
        图片 10MB · 音乐/音频 20MB · 文档 50MB。文件只存在你自己的电脑上（FY_ASSETS_DIR），
        不上传到任何服务器。
      </p>
      <label className="asset-file-label">
        <span className="sr-only">选择文件上传</span>
        <input
          type="file"
          multiple
          ref={inputRef}
          data-testid="asset-file-input"
          disabled={busy}
          onChange={(e) => {
            void handleFiles(e.target.files);
            e.target.value = '';
          }}
        />
      </label>
      {busy && (
        <span className="muted" data-testid="asset-dropzone-busy" role="status">
          正在上传…
        </span>
      )}
    </div>
  );
}

/** 图片缩略图：走鉴权 raw 端点，加载失败如实显示而不是留空白框。 */
export function AssetThumbnail({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="asset-thumb-fallback" data-testid="asset-thumb-failed" role="img" aria-label={alt}>
        图片读取失败
      </div>
    );
  }
  return (
    <img
      className="asset-thumb"
      src={src}
      alt={alt}
      data-testid="asset-thumb"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

export { assetRawUrl };