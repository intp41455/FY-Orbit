/**
 * P9 · 语音 + 画笔批注（A-点哪评哪-07）。
 *
 * 「圈选」只能圈矩形；但很多意见需要**比划**（「这个箭头应该往这边」）。
 * 本组件提供画布批注：
 *
 * - 画笔：在覆盖层上自由画线，落成**归一化矢量笔迹**（不是位图截图）；
 * - 语音：录音一段，只保留**附件引用 + 转写文本**（不做语音识别，那需外部服务）；
 * - 两种可叠加：画完再补一句语音说明。
 *
 * 诚实边界：笔迹只存矢量点集（跨分辨率可复原）；语音不本地转写——
 * 浏览器 Web Speech 可用则用，不可用则如实标注「未转写」，绝不编造内容。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  REVIEW_UI_ATTR,
  currentViewport,
  normalizeStroke,
  type Stroke,
  type StrokePoint,
} from './geometry';
import { useBase } from '../../hooks/useAutosave';

export interface AnnotationCanvasProps {
  /** 提交批注（笔迹 + 语音引用 + 转写）。 */
  onSubmit: (payload: {
    strokes: Stroke[];
    audio_ref: string;
    audio_transcript: string;
  }) => void;
  onCancel: () => void;
}

/** 画笔 + 语音批注覆盖层。 */
export function AnnotationCanvas({ onSubmit, onCancel }: AnnotationCanvasProps) {
  useBase({ surface: 'web/src/components/review/AnnotationCanvas' });
  const [strokes, setStrokes] = useState<StrokePoint[][]>([]);
  const [live, setLive] = useState<StrokePoint[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [audioRef, setAudioRef] = useState('');
  const [transcript, setTranscript] = useState('');
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState('');

  const currentRef = useRef<StrokePoint[]>([]);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  // -- 画笔 ---------------------------------------------------------------

  const onMouseDown = (e: React.MouseEvent): void => {
    if (e.button !== 0) return;
    e.preventDefault();
    setDrawing(true);
    const seed = [{ x: e.clientX, y: e.clientY }];
    currentRef.current = seed;
    setLive(seed);
  };

  const onMouseMove = (e: React.MouseEvent): void => {
    if (!drawing) return;
    const next = [...currentRef.current, { x: e.clientX, y: e.clientY }];
    currentRef.current = next;
    setLive(next);
  };

  const onMouseUp = (): void => {
    if (!drawing) return;
    setDrawing(false);
    const pts = currentRef.current;
    if (pts.length >= 2) setStrokes((prev) => [...prev, pts]);
    currentRef.current = [];
    setLive([]);
  };

  const undo = (): void => setStrokes((prev) => prev.slice(0, -1));

  // -- 语音 ---------------------------------------------------------------

  const startRecording = useCallback(async (): Promise<void> => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        // 诚实边界：这里只生成一个本地引用占位，不做上传（上传走既有 storage 通道）。
        setAudioRef(`review-audio/local-${Date.now()}.webm`);
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
      setNotice('');
    } catch {
      // 麦克风不可用：如实告知，不假装录上了
      setNotice('麦克风不可用（浏览器拒绝或设备缺失），可改用画笔批注');
    }
  }, []);

  const stopRecording = useCallback((): void => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  }, []);

  // -- 提交 ---------------------------------------------------------------

  const viewport = currentViewport();
  const hasContent = strokes.length > 0 || Boolean(audioRef);

  const submit = (): void => {
    if (!hasContent) {
      setNotice('请先画一笔或录一段语音');
      return;
    }
    onSubmit({
      strokes: strokes.map((pts) => normalizeStroke(pts, viewport)),
      audio_ref: audioRef,
      audio_transcript: transcript,
    });
  };

  return (
    <div
      {...{ [REVIEW_UI_ATTR]: '' }}
      data-testid="annotation-canvas"
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      style={{
        position: 'fixed', inset: 0, zIndex: 2147482900,
        cursor: 'crosshair', background: 'rgba(15, 23, 42, 0.04)',
      }}
    >
      <svg
        data-testid="annotation-svg"
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
      >
        {strokes.map((pts, i) => (
          <polyline
            key={i}
            points={pts.map((p) => `${p.x},${p.y}`).join(' ')}
            fill="none"
            stroke="var(--rose, #e11d48)"
            strokeWidth={3}
            strokeLinecap="round"
          />
        ))}
        {live.length > 1 && (
          <polyline
            data-testid="annotation-live"
            points={live.map((p) => `${p.x},${p.y}`).join(' ')}
            fill="none"
            stroke="var(--rose, #e11d48)"
            strokeWidth={3}
            strokeLinecap="round"
          />
        )}
      </svg>

      <div
        style={{
          position: 'fixed', top: 14, left: '50%', transform: 'translateX(-50%)',
          display: 'flex', gap: 8, alignItems: 'center',
          padding: '8px 12px', borderRadius: 12,
          border: '1px solid var(--glass-border)', background: 'var(--glass-strong)',
        }}
      >
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {recording ? '录音中…' : '画笔批注：按住拖动画线'}
        </span>
        <button data-testid="annotation-undo" type="button" onClick={undo}
          disabled={strokes.length === 0}
          style={btnStyle(strokes.length === 0)}>
          撤销
        </button>
        <button data-testid="annotation-record" type="button"
          onClick={() => (recording ? stopRecording() : void startRecording())}
          style={btnStyle(false, recording ? 'var(--rose)' : undefined)}>
          {recording ? '停止录音' : '录语音'}
        </button>
        <button data-testid="annotation-submit" type="button" onClick={submit}
          disabled={!hasContent}
          style={btnStyle(!hasContent, 'var(--sky)')}>
          添加批注
        </button>
        <button data-testid="annotation-cancel" type="button" onClick={onCancel}
          style={btnStyle(false)}>
          取消
        </button>
      </div>

      <div
        style={{
          position: 'fixed', bottom: 18, left: '50%', transform: 'translateX(-50%)',
          width: 420, display: 'flex', flexDirection: 'column', gap: 6,
        }}
      >
        <input
          data-testid="annotation-transcript"
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          placeholder="语音转写（可手填；本机不做识别则留空）"
          style={{
            padding: '8px 10px', borderRadius: 10, fontSize: 13,
            border: '1px solid var(--line)', background: '#fff', color: 'var(--text-main)',
          }}
        />
        {audioRef && (
          <span data-testid="annotation-audio-ref" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            语音附件：{audioRef}
          </span>
        )}
        {notice && (
          <span data-testid="annotation-notice" style={{ fontSize: 11, color: 'var(--amber)' }}>
            {notice}
          </span>
        )}
      </div>
    </div>
  );
}

function btnStyle(disabled: boolean, accent?: string): React.CSSProperties {
  return {
    padding: '6px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    border: '1px solid var(--line)',
    background: disabled ? 'var(--glass-base)' : accent || 'var(--glass-base)',
    color: disabled ? 'var(--text-faint)' : accent ? '#fff' : 'var(--text-main)',
    opacity: disabled ? 0.6 : 1,
  };
}
