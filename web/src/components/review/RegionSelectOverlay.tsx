/**
 * P9 · 圈选区域评论（A-点哪评哪-06）。
 *
 * 点击页面元素只能针对「有 DOM 边界的元素」；但用户常常想圈出**一片区域**
 * （比如「这块留白太多」「这个图表整体偏左」）——可能是多个元素，也可能是
 * Canvas 绘制区。本组件提供拖拽圈选：
 *
 * - 按下拖动 → 半透明遮罩实时显示选区；
 * - 松开 → 把像素选区换算成**归一化矩形**（0~1）并回调；
 * - Esc 取消；选区过小（< 8px）视为误触，不提交。
 *
 * 坐标一律归一化（`geometry.toNormalizedRegion`）——像素坐标换窗口就失效。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  REVIEW_UI_ATTR,
  currentViewport,
  dragToNormalizedRegion,
  type NormalizedRegion,
} from './geometry';

export interface RegionSelectOverlayProps {
  /** 圈选完成回调（归一化矩形，0~1）。 */
  onSelect: (region: NormalizedRegion) => void;
  /** 取消圈选。 */
  onCancel: () => void;
  /** 最小有效边长（像素），小于此视为误触。 */
  minSize?: number;
}

interface DragState {
  startX: number;
  startY: number;
  curX: number;
  curY: number;
}

/** 选区遮罩（全屏 fixed 覆盖层，仅圈选模式挂载）。 */
export function RegionSelectOverlay({
  onSelect,
  onCancel,
  minSize = 8,
}: RegionSelectOverlayProps) {
  const [drag, setDrag] = useState<DragState | null>(null);
  const dragRef = useRef<DragState | null>(null);

  const setDragBoth = useCallback((v: DragState | null) => {
    dragRef.current = v;
    setDrag(v);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const onMouseDown = (e: React.MouseEvent): void => {
    if (e.button !== 0) return;
    e.preventDefault();
    setDragBoth({ startX: e.clientX, startY: e.clientY, curX: e.clientX, curY: e.clientY });
  };

  const onMouseMove = (e: React.MouseEvent): void => {
    const cur = dragRef.current;
    if (!cur) return;
    setDragBoth({ ...cur, curX: e.clientX, curY: e.clientY });
  };

  const onMouseUp = (): void => {
    const cur = dragRef.current;
    if (!cur) return;
    const w = Math.abs(cur.curX - cur.startX);
    const h = Math.abs(cur.curY - cur.startY);
    setDragBoth(null);
    if (w < minSize || h < minSize) {
      onCancel(); // 误触：太小，不产生意见
      return;
    }
    onSelect(
      dragToNormalizedRegion(
        { x: cur.startX, y: cur.startY },
        { x: cur.curX, y: cur.curY },
        currentViewport(),
      ),
    );
  };

  const box = drag
    ? {
        left: Math.min(drag.startX, drag.curX),
        top: Math.min(drag.startY, drag.curY),
        width: Math.abs(drag.curX - drag.startX),
        height: Math.abs(drag.curY - drag.startY),
      }
    : null;

  return (
    <div
      {...{ [REVIEW_UI_ATTR]: '' }}
      data-testid="region-select-overlay"
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      style={{
        position: 'fixed', inset: 0, zIndex: 2147482900,
        cursor: 'crosshair',
        background: 'rgba(2, 132, 199, 0.06)',
      }}
    >
      <div
        data-testid="region-select-hint"
        style={{
          position: 'fixed', top: 14, left: '50%', transform: 'translateX(-50%)',
          padding: '7px 14px', borderRadius: 999, fontSize: 12,
          border: '1px solid var(--glass-border)', background: 'var(--glass-strong)',
          color: 'var(--sky-deep)', pointerEvents: 'none',
        }}
      >
        拖拽圈出要评论的区域（Esc 取消）
      </div>
      {box && (
        <div
          data-testid="region-select-box"
          style={{
            position: 'fixed', left: box.left, top: box.top,
            width: box.width, height: box.height,
            border: '2px dashed var(--sky)', borderRadius: 4,
            background: 'rgba(2, 132, 199, 0.12)', pointerEvents: 'none',
          }}
        />
      )}
    </div>
  );
}
