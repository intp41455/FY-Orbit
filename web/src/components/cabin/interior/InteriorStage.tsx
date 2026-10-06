import { useEffect, useRef, useState } from 'react';
import { createCabinInteriorScene, type InteriorScene } from './cabinInteriorScene';
import { observeViewport, resolveViewport, type ViewportSize } from '../cabinViewport';
import type { CabinHouseId } from '../cabinConfig';
import type { InteriorCallbacks } from './cabinInteriorScene';
import type { PixelPalette } from '../cabinPixels';
import type { InteriorItem, InteriorLayout } from './interiorLayout';

interface InteriorStageProps {
  houseId: CabinHouseId;
  layout: InteriorLayout;
  editMode: boolean;
  selectedId: string | null;
  callbacks?: InteriorCallbacks;
  /** 递增计数：递增触发一次「落位点亮」演出（用于新放置的家具）。 */
  placeGlowToken?: { itemId: string; token: number } | null;
  /** 递增计数：触发睡觉演出。 */
  sleepToken?: number;
  /** 递增计数：触发一句气泡台词。 */
  sayToken?: { text: string; token: number } | null;
  /** 递增计数：驱动小人走到指定格坐标（点击地板时由页面回传）。 */
  personToken?: { gx: number; gy: number; token: number } | null;
  /**
   * W11 衔接：自定义小人矩阵 / 调色板。
   * 角色由 `components/avatar/` 生成，本组件只透传给渲染层；不传则用默认小人。
   */
  personWalkFrames?: readonly (readonly string[])[];
  personIdleFrames?: readonly (readonly string[])[];
  personPalette?: PixelPalette;
  personName?: string;
}

/**
 * 室内 PixiJS 画布的 React 薄壳：只做生命周期（创建 / 自适应 / destroy 防泄漏）
 * 与 props → 场景的单向同步。jsdom 下 PixiJS 不可用，测试整体 mock 本组件，
 * 逻辑层可测性由 interiorLayout.ts + CabinPage 保证（与 CabinStage 同策略）。
 */
export function InteriorStage({
  houseId,
  layout,
  editMode,
  selectedId,
  callbacks,
  placeGlowToken,
  sleepToken,
  sayToken,
  personToken,
  personWalkFrames,
  personIdleFrames,
  personPalette,
  personName,
}: InteriorStageProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<InteriorScene | null>(null);
  const [initError, setInitError] = useState(false);

  // 用 ref 承接回调，避免因回调身份变化重建整个 Pixi 场景。
  const cbRef = useRef(callbacks);
  cbRef.current = callbacks;
  const initialRef = useRef({ houseId, layout, editMode });
  const lastPlaceRef = useRef(0);
  const lastSleepRef = useRef(0);
  const lastSayRef = useRef(0);
  const lastPersonRef = useRef(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    let disposed = false;
    let cleanup: (() => void) | null = null;
    // 每次 effect 运行新建独立 canvas（与 CabinStage 同修）：避免 React StrictMode(dev) 双挂载
    // 下两个场景共用同一 React <canvas>，先弃场景 destroy() 会销毁共享 WebGL 上下文并移除 canvas。
    const canvas = document.createElement('canvas');
    canvas.className = 'cabin-canvas cabin-canvas-interior';
    canvas.setAttribute('aria-hidden', 'true');
    host.appendChild(canvas);
    void createCabinInteriorScene({
      canvas,
      host,
      houseId: initialRef.current.houseId,
      layout: initialRef.current.layout,
      editMode: initialRef.current.editMode,
      personWalkFrames,
      personIdleFrames,
      personPalette,
      personName,
      callbacks: {
        onFurnitureTap: (i) => cbRef.current?.onFurnitureTap?.(i),
        onFloorTap: (x, y) => cbRef.current?.onFloorTap?.(x, y),
        onExit: () => cbRef.current?.onExit?.(),
        onItemMoved: (id, x, y) => cbRef.current?.onItemMoved?.(id, x, y),
        onItemSelected: (id) => cbRef.current?.onItemSelected?.(id),
      },
    })
      .then((scene) => {
        if (disposed) {
          scene.destroy();
          return;
        }
        sceneRef.current = scene;
        // I3：视口取自宿主元素（内嵌形态），宿主尺寸变化或窗口 resize 都重算。
        const stopObserve = observeViewport(host, (size: ViewportSize) => scene.resize(size.width, size.height));
        const initial = resolveViewport(host);
        scene.resize(initial.width, initial.height);
        cleanup = () => {
          stopObserve();
          scene.destroy();
          sceneRef.current = null;
        };
      })
      .catch(() => {
        // WebGL 不可用：不白屏，工具条仍可用，画布区给出明确提示（不假装渲染成功）
        setInitError(true);
      });
    return () => {
      disposed = true;
      cleanup?.();
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };
  }, []);

  useEffect(() => {
    sceneRef.current?.setLayout(layout);
  }, [layout]);

  useEffect(() => {
    sceneRef.current?.setHouse(houseId);
  }, [houseId]);

  useEffect(() => {
    sceneRef.current?.setEditMode(editMode);
  }, [editMode]);

  useEffect(() => {
    sceneRef.current?.setSelected(selectedId);
  }, [selectedId]);

  // 演出令牌：token 变化才触发，避免每次渲染都重放动画。
  useEffect(() => {
    if (!placeGlowToken || placeGlowToken.token === lastPlaceRef.current) return;
    lastPlaceRef.current = placeGlowToken.token;
    sceneRef.current?.playPlaceGlow(placeGlowToken.itemId);
  }, [placeGlowToken]);

  useEffect(() => {
    if (sleepToken === undefined || sleepToken === lastSleepRef.current) return;
    lastSleepRef.current = sleepToken;
    sceneRef.current?.playSleep();
  }, [sleepToken]);

  useEffect(() => {
    if (!sayToken || sayToken.token === lastSayRef.current) return;
    lastSayRef.current = sayToken.token;
    sceneRef.current?.say(sayToken.text);
  }, [sayToken]);

  useEffect(() => {
    if (!personToken || personToken.token === lastPersonRef.current) return;
    lastPersonRef.current = personToken.token;
    sceneRef.current?.movePerson(personToken.gx, personToken.gy);
  }, [personToken]);

  return (
    <>
      <div
        ref={hostRef}
        className="cabin-canvas-host"
        data-testid="cabin-interior-canvas"
        aria-hidden="true"
      />
      {initError && (
        <div className="cabin-fallback" role="alert">
          室内画面初始化失败：当前环境可能不支持 WebGL。布置数据仍会正常保存。
        </div>
      )}
    </>
  );
}

/** 供页面层引用的道具类型别名（避免页面重复 import 渲染层）。 */
export type { InteriorItem };
