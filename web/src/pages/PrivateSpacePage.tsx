// 私人空间（W9）：图片 / 音频 / 音乐三模块实装。
//
// 相对 W9 之前的变化：这三个模块不再是「供应商未配置 · 不可用」的占位，而是
// 真实对接 /api/assets（列表 / 拖拽上传 / 生成 / 删除 / 挂小屋）。
//
// 诚实性（总纲铁律 3 / 冻结契约 §11）：
//  - 图片通道未接入时显示「未接入生成服务」，**不**出现占位图或假缩略图；
//  - 音乐模块本地合成器永远真实可用，卡片标注「本地合成（非 AI 作曲）」；
//  - 后端 4xx/5xx 原样展示 code + message，失败绝不静默变成成功。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AssetApiError,
  assetsApi,
  type AssetKind,
  type AssetRecord,
  type ChannelOverview,
  type MountRole,
} from '../api/assets';
import { AssetCard } from '../components/assets/AssetCard';
import { AssetDropzone } from '../components/assets/AssetDropzone';
import { GeneratePanel } from '../components/assets/GeneratePanel';

const KINDS: AssetKind[] = ['image', 'audio', 'music'];
// 拖入的文档类文件也会入库；单列一个区展示（无挂载按钮），避免「存了但看不见」。
const OTHER_KINDS: AssetKind[] = ['doc'];

const SECTION_TITLE: Record<AssetKind, string> = {
  image: '🖼 图片',
  audio: '🔊 音频',
  music: '🎵 音乐',
  doc: '📄 文档与其他',
};

function describeError(err: unknown): string {
  if (err instanceof AssetApiError) return `${err.code}：${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function PrivateSpacePage() {
  const navigate = useNavigate();
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [maxBytes, setMaxBytes] = useState<Record<string, number>>({});
  const [overview, setOverview] = useState<ChannelOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const body = await assetsApi.list();
      setAssets(body.assets);
      setMaxBytes(body.max_bytes_by_kind);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const body = await assetsApi.channels();
        if (!cancelled) setOverview(body);
      } catch (err) {
        if (!cancelled) setError(describeError(err));
      }
      if (!cancelled) {
        await refresh();
        setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const run = useCallback(
    async (action: () => Promise<void>) => {
      setBusy(true);
      setError('');
      try {
        await action();
      } catch (err) {
        setError(describeError(err));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const handleUpload = useCallback(
    (file: File, kind: AssetKind) =>
      run(async () => {
        const asset = await assetsApi.upload(file, kind);
        await refresh();
        setNotice(`已入库：${asset.name}`);
      }),
    [refresh, run],
  );

  const handleDelete = useCallback(
    (asset: AssetRecord) =>
      run(async () => {
        const result = await assetsApi.remove(asset.id);
        await refresh();
        setNotice(
          result.file_removed
            ? `已删除 ${asset.name}（记录 + 磁盘文件）`
            : `已删除 ${asset.name} 的记录；磁盘文件本就不存在，如实告知未做删除`,
        );
      }),
    [refresh, run],
  );

  const handleMount = useCallback(
    (asset: AssetRecord, role: MountRole) =>
      run(async () => {
        await assetsApi.setMount(asset.id, role);
        await refresh();
        setNotice(role === 'wall' ? `已把「${asset.name}」挂到小屋墙上` : role === 'bgm' ? `已把「${asset.name}」设为小屋 BGM` : `已取消「${asset.name}」的小屋挂载`);
      }),
    [refresh, run],
  );

  const handleGenerateImage = useCallback(
    (prompt: string, size: string) =>
      run(async () => {
        const body = await assetsApi.generateImage({ prompt, size });
        await refresh();
        setNotice(`图片已生成并入库：${body.asset.name}`);
      }),
    [refresh, run],
  );

  const handleGenerateMusic = useCallback(
    (mood: string, seconds: number) =>
      run(async () => {
        const body = await assetsApi.generateMusic({ mood, seconds });
        await refresh();
        setNotice(`曲子已合成并入库：${body.asset.name}（本地合成，非 AI 作曲）`);
      }),
    [refresh, run],
  );

  const handleConfigureChannel = useCallback(
    (channel: 'image' | 'tts', values: { base_url: string; api_key: string; model: string }) =>
      run(async () => {
        const status = await assetsApi.configureChannel(channel, values);
        const body = await assetsApi.channels();
        setOverview(body);
        setNotice(`通道 ${status.channel}：${status.detail}`);
      }),
    [run],
  );

  const byKind = (kind: AssetKind) => assets.filter((a) => a.kind === kind);

  return (
    <>
      <div className="page-head cabin-page-head">
        <h2>私人空间</h2>
        <div className="row" style={{ gap: '0.5rem' }}>
          {/* W11：角色工坊入口 —— 个人空间是任务的两个入口之一（另一个是小屋更衣镜） */}
          <button type="button" className="cabin-entry-btn" onClick={() => navigate('/avatar')}>
            <span aria-hidden="true">🧑‍🎨</span> 角色工坊
          </button>
          <button type="button" className="cabin-entry-btn" onClick={() => navigate('/cabin')}>
            <span aria-hidden="true">🏠</span> 我的小屋
          </button>
        </div>
      </div>
      <p className="muted">
        图片、音频、音乐与创作任务的产物引用。文件只存在你自己的电脑（FY_ASSETS_DIR），
        字节一律经鉴权后的 /api/assets 读取，浏览器拿不到磁盘路径。
      </p>

      {/* W11：角色工坊入口卡 */}
      <button type="button" className="card avatar-entry-card" onClick={() => navigate('/avatar')}>
        <strong>🧑‍🎨 角色工坊 · 生成我的专属像素小人</strong>
        <span className="muted">
          用 MBTI、八字五行、星盘、姓名等画像，在本地生成独属于自己的 24×32 像素角色。
          全程不调用大模型，同画像必得同一角色；画像数据不离开你的电脑。
        </span>
      </button>

      <GeneratePanel
        overview={overview}
        busy={busy}
        error={error}
        onGenerateImage={handleGenerateImage}
        onGenerateMusic={handleGenerateMusic}
        onConfigureChannel={handleConfigureChannel}
      />

      <AssetDropzone
        maxBytesByKind={maxBytes}
        busy={busy}
        onUpload={handleUpload}
        onError={setError}
      />

      {notice && (
        <p className="notice info" data-testid="assets-notice" role="status">
          {notice}
        </p>
      )}

      {[...KINDS, ...OTHER_KINDS].map((kind) => (
        <section key={kind} className="assets-section" data-testid={`assets-section-${kind}`}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <strong>{SECTION_TITLE[kind]}</strong>
            <span className="muted">{byKind(kind).length} 项</span>
          </div>
          {byKind(kind).length === 0 ? (
            <p className="muted" data-testid={`assets-empty-${kind}`}>
              {loaded ? '还没有内容：拖入文件或用上面的生成按钮创建。' : '正在读取资产库…'}
            </p>
          ) : (
            <div className="grid asset-grid">
              {byKind(kind).map((asset) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  busy={busy}
                  onDelete={handleDelete}
                  onMount={handleMount}
                />
              ))}
            </div>
          )}
        </section>
      ))}
    </>
  );
}