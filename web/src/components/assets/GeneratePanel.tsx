// W9 生成对话框：图片 prompt / 音乐情绪参数 / TTS 文本。
//
// 诚实性是硬约束（总纲铁律 3）：
//  - 图片通道未接入时，**不渲染**生成按钮的可用态，而是显示后端给的
//    「未接入生成服务」说明 + 凭证配置表单；
//  - 音乐通道永远可用（本地合成），并在按钮旁注明「本地合成，非 AI 作曲」；
//  - 上游失败时原样展示后端 code + message，不吞错、不重试成假成功。
import { useState } from 'react';
import type { ChannelOverview, MoodOption } from '../../api/assets';
import { channelBadge } from './assetFormat';

export interface GeneratePanelProps {
  overview: ChannelOverview | null;
  busy: boolean;
  error: string;
  onGenerateImage: (prompt: string, size: string) => void;
  onGenerateMusic: (mood: string, seconds: number) => void;
  onConfigureChannel: (channel: 'image' | 'tts', values: { base_url: string; api_key: string; model: string }) => void;
}

export function GeneratePanel({
  overview,
  busy,
  error,
  onGenerateImage,
  onGenerateMusic,
  onConfigureChannel,
}: GeneratePanelProps) {
  const image = overview?.channels.find((c) => c.channel === 'image') ?? null;
  const tts = overview?.channels.find((c) => c.channel === 'tts') ?? null;
  const moods: MoodOption[] = overview?.moods ?? [];
  const [prompt, setPrompt] = useState('');
  const [size, setSize] = useState('1024x1024');
  const [mood, setMood] = useState(moods[0]?.id ?? 'calm');
  const [seconds, setSeconds] = useState(12);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');

  const imageReady = image?.configured === true;

  return (
    <section className="card asset-generate" data-testid="asset-generate" aria-label="生成">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>✨ 生成</strong>
        <span className="muted">生成物自动入库，可在下方网格查看</span>
      </div>

      {/* ---- 图片 ---- */}
      <fieldset className="asset-group">
        <legend>图片</legend>
        {imageReady ? (
          <>
            <div className="row asset-inline">
              <input
                className="asset-input"
                data-testid="asset-image-prompt"
                aria-label="图片描述"
                placeholder="例如：像素风雪夜小屋，暖黄灯光"
                value={prompt}
                maxLength={2000}
                onChange={(e) => setPrompt(e.target.value)}
              />
              <select
                className="asset-select"
                data-testid="asset-image-size"
                aria-label="图片尺寸"
                value={size}
                onChange={(e) => setSize(e.target.value)}
              >
                {['256x256', '512x512', '1024x1024'].map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="asset-btn primary"
                data-testid="asset-image-generate"
                disabled={busy || prompt.trim().length === 0}
                onClick={() => onGenerateImage(prompt.trim(), size)}
              >
                生成图片
              </button>
            </div>
            <div className="muted">
              通道：{image?.detail} · 模型 {image?.model}
            </div>
          </>
        ) : (
          <div className="notice info" data-testid="asset-image-unconfigured">
            <strong>未接入生成服务</strong>
            <div className="muted">{image?.detail ?? '正在读取通道状态…'}</div>
            <div className="muted">
              未配置前这里不会出现任何「已生成」内容；本地音乐模块不依赖它，照常可用。
            </div>
          </div>
        )}
      </fieldset>

      {/* ---- 音乐（本地合成，永远可用）---- */}
      <fieldset className="asset-group">
        <legend>音乐（本地合成）</legend>
        <div className="row asset-inline">
          <select
            className="asset-select"
            data-testid="asset-music-mood"
            aria-label="音乐情绪"
            value={mood}
            onChange={(e) => setMood(e.target.value)}
          >
            {moods.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}（{m.tempo_bpm} BPM）
              </option>
            ))}
          </select>
          <input
            className="asset-input asset-input-narrow"
            data-testid="asset-music-seconds"
            aria-label="时长秒数"
            type="number"
            min={2}
            max={60}
            value={seconds}
            onChange={(e) => setSeconds(Number(e.target.value))}
          />
          <button
            type="button"
            className="asset-btn primary"
            data-testid="asset-music-generate"
            disabled={busy}
            onClick={() => onGenerateMusic(mood, seconds)}
          >
            合成曲子
          </button>
        </div>
        <div className="muted">{overview?.music?.detail ?? '本地芯片音乐合成器'}</div>
        <div className="muted">纯代码合成（WAV，≤60 秒），不是 AI 作曲；卡片上会如实标注来源。</div>
      </fieldset>

      {/* ---- 语音（TTS，未接入时同样诚实）---- */}
      <fieldset className="asset-group">
        <legend>语音（TTS）</legend>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className={`badge ${tts?.configured ? 'ok' : 'warn'}`} data-testid="asset-tts-badge">
            {tts ? channelBadge(tts).label : '读取中'}
          </span>
          <span className="muted">{tts?.detail ?? ''}</span>
        </div>
      </fieldset>

      {/* ---- 通道配置（凭证只存服务端内存，不回显）---- */}
      <fieldset className="asset-group">
        <legend>接入生成服务（图片 / 语音）</legend>
        <div className="row asset-inline">
          <input
            className="asset-input"
            data-testid="asset-channel-base-url"
            aria-label="Base URL"
            placeholder="Base URL，如 http://127.0.0.1:7860/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <input
            className="asset-input asset-input-narrow"
            data-testid="asset-channel-api-key"
            aria-label="API Key"
            placeholder="API Key（本机端点可留空）"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <input
            className="asset-input asset-input-narrow"
            data-testid="asset-channel-model"
            aria-label="模型名"
            placeholder="模型（可选）"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
          <button
            type="button"
            className="asset-btn"
            data-testid="asset-channel-save"
            disabled={busy || baseUrl.trim().length === 0}
            onClick={() => onConfigureChannel('image', { base_url: baseUrl.trim(), api_key: apiKey, model })}
          >
            保存并接入图片
          </button>
          <button
            type="button"
            className="asset-btn"
            data-testid="asset-channel-save-tts"
            disabled={busy || baseUrl.trim().length === 0}
            onClick={() => onConfigureChannel('tts', { base_url: baseUrl.trim(), api_key: apiKey, model })}
          >
            保存并接入语音
          </button>
        </div>
        <div className="muted">
          凭证只保存在服务端进程内存里（重启即失效、不入库、不落盘、不回显），
          与 W3 知识源同一策略。
        </div>
      </fieldset>

      {error && (
        <p className="notice warn" data-testid="asset-generate-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}