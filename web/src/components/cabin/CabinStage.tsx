import { useEffect, useRef, useState } from 'react';
import { createCabinScene, type CabinScene } from './cabinScene';
import { observeViewport, resolveViewport } from './cabinViewport';
import type { CabinConfig, DialogueSpeaker } from './cabinConfig';
import type { PixelPalette } from './cabinPixels';

export interface CabinSpeechRequest {
  speaker: DialogueSpeaker;
  text: string;
  seq: number;
}

interface CabinStageProps {
  config: CabinConfig;
  /** 非空时在对应角色头顶弹出气泡（seq 变化即触发，即便内容相同）。 */
  speech: CabinSpeechRequest | null;
  onSpeak?: (speaker: DialogueSpeaker) => void;
  /**
   * W11 衔接（个性化像素角色）：自定义小人行走矩阵（两帧）+ 调色板。
   *
   * 纯追加可选参数：两个都不传时行为与 W1 完全一致（回退内置默认小人）。
   * 帧数 <2 时cabinScene 会明确回退默认小人，本层不拦截（真相在渲染层）。
   */
  personWalkFrames?: readonly (readonly string[])[];
  personIdleFrames?: readonly (readonly string[])[];
  personPalette?: PixelPalette;
}

/**
 * PixiJS 画布的 React 薄壳：只负责生命周期（创建 / 窗口自适应 / destroy 防泄漏）
 * 与 config、speech 请求的同步。jsdom 下 PixiJS 不可用，测试里整个 mock 本组件，
 *逻辑层的可测性由 cabinConfig.ts 保证。
 *
 * W11 追加说明：personWalkFrames / personPalette 只在 createCabinScene 时消费，
 * 因此它们变化时必须重建场景。重建放在一个**由调用方 key 控制的子组件**里，
 * 本组件自身既有行为（config / speech 同步、resize、防泄漏）保持不变。
 */
export function CabinStage({ config, speech, onSpeak, personWalkFrames, personIdleFrames, personPalette }: CabinStageProps) {
  // 自定义小人身份串：帧矩阵 + 调色板任一变化即视为换了角色，需要重建纹理。
  // 用矩阵首帧做身份代表（内容变即身份变），避免每次渲染都重建场景。
  const [personIdentity, setPersonIdentity] = useState(() => personKey(personWalkFrames, personPalette));
  useEffect(() => {
    const next = personKey(personWalkFrames, personPalette);
    setPersonIdentity((cur) => (cur === next ? cur : next));
  }, [personWalkFrames, personPalette]);

  return (
    <CabinStageCanvas
      key={personIdentity}
      config={config}
      speech={speech}
      onSpeak={onSpeak}
      personWalkFrames={personWalkFrames}
      personIdleFrames={personIdleFrames}
      personPalette={personPalette}
    />
  );
}

/**
 * 角色身份串。
 * 只看「是否用自定义角色」+ 首帧内容 + 调色板键序：足以在角色真的换了时触发重建，
 * 又不会因无关的引用变化导致每次渲染都重建 Pixi 场景。
 */
function personKey(
  frames: readonly (readonly string[])[] | undefined,
  palette: PixelPalette | undefined,
): string {
  const usable = Array.isArray(frames) && frames.length >= 2;
  if (!usable) return 'default';
  const head = frames[0]?.join('|') ?? '';
  const colors = palette ? Object.entries(palette).map(([k, v]) => `${k}${v}`).join(',') : '';
  return `custom:${head}:${colors}`;
}

function CabinStageCanvas({
  config,
  speech,
  onSpeak,
  personWalkFrames,
  personIdleFrames,
  personPalette,
}: {
  config: CabinConfig;
  speech: CabinSpeechRequest | null;
  onSpeak?: (speaker: DialogueSpeaker) => void;
  personWalkFrames?: readonly (readonly string[])[];
  personIdleFrames?: readonly (readonly string[])[];
  personPalette?: PixelPalette;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<CabinScene | null>(null);
  const onSpeakRef = useRef(onSpeak);
  onSpeakRef.current = onSpeak;
  const configRef = useRef(config);
  configRef.current = config;
  const [initError, setInitError] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    let disposed = false;
    let cleanup: (() => void) | null = null;
    const canvas = document.createElement('canvas');
    canvas.className = 'cabin-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    host.appendChild(canvas);
    void createCabinScene({
      canvas,
      host,
      config: configRef.current,
      callbacks: { onSpeak: (s) => onSpeakRef.current?.(s) },
      personWalkFrames,
      personIdleFrames,
      personPalette,
    })
      .then((scene) => {
        if (disposed) {
          scene.destroy();
          return;
        }
        sceneRef.current = scene;
        scene.setConfig(configRef.current);
        const stopObserve = observeViewport(host, (size) => scene.resize(size.width, size.height));
        // 首帧立刻定标一次（ResizeObserver 首次回调异步，不能等）。
        const initial = resolveViewport(host);
        scene.resize(initial.width, initial.height);
        cleanup = () => {
          stopObserve();
          scene.destroy();
          sceneRef.current = null;
        };
      })
      .catch((err) => {
        // WebGL 不可用等初始化失败：不白屏，工具条仍可用，画布区给出明确提示。
        // 诚实：把真实错误打到控制台便于排障（过去静默吞掉，线上问题无从定位）。
        console.error('[cabin] 场景初始化失败：', err);
        setInitError(true);
      });
    return () => {
      disposed = true;
      cleanup?.();
      // 本 effect 运行自建的 canvas 由本清理负责移除（React 不再持有它）
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };
  }, []);

  useEffect(() => {
    sceneRef.current?.setConfig(config);
  }, [config]);

  useEffect(() => {
    if (speech) sceneRef.current?.speak(speech.speaker, speech.text);
  }, [speech]);

  return (
    <>
      <div ref={hostRef} className="cabin-canvas-host" data-testid="cabin-canvas" aria-hidden="true" />
      <div className="cabin-keyboard-hint" data-testid="cabin-keyboard-hint" aria-label="键盘操作提示">
        <span className="cabin-hint-item">
          <kbd className="cabin-key-badge">WASD / 方向键</kbd> 移动·纵深
        </span>
        <span className="cabin-hint-item">
          <kbd className="cabin-key-badge">Space / W</kbd> 跳跃
        </span>
        <span className="cabin-hint-item">
          <kbd className="cabin-key-badge">S 长按</kbd> 下蹲
        </span>
      </div>
      {initError && (
        <div className="cabin-fallback" role="alert">
          图形引擎初始化失败：当前环境可能不支持 WebGL。装扮设置仍可正常使用与保存。
        </div>
      )}
    </>
  );
}
