import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useRef, useState } from 'react';

/**
 * 守住 `TeamCanvasGraph.onPointerDown` 的排除条件。
 *
 * 真实组件依赖 Pixi，jsdom 下不可渲染，所以这里复刻同一交互契约：
 * 一个模拟「平移层」的结构，底部有缩放条；按下时若目标落在
 * `.cv-bottombar` 内，就不能进入平移状态。
 *
 * 这条规则若被破坏，后果是「缩小/放大/复位三按钮点击被 setPointerCapture
 * 吞掉，点了完全没反应」—— 用户可见且无任何报错，极难定位。
 */

/** 与 TeamCanvasGraph.tsx 中 onPointerDown 保持一致的排除条件。 */
function shouldStartPan(target: Element | null): boolean {
  if (!target) return false;
  if (target.closest('[data-cv-node]')) return false;
  if (target.closest('.cv-bottombar')) return false;
  return true;
}

function Harness() {
  const [panning, setPanning] = useState(false);
  const captured = useRef<number | null>(null);
  return (
    <div
      data-testid="canvas-pan-layer"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        if (!shouldStartPan(e.target as Element)) return;
        captured.current = e.pointerId;
        setPanning(true);
      }}
      onPointerUp={() => {
        captured.current = null;
        setPanning(false);
      }}
    >
      <div data-cv-node data-testid="a-node">
        节点
      </div>

      <div className="cv-bottombar" data-testid="bottombar">
        <button type="button" aria-label="缩小" onClick={() => {}}>
          -
        </button>
        <span>53%</span>
        <button type="button" aria-label="放大" onClick={() => {}}>
          +
        </button>
        <button type="button" aria-label="复位视图" onClick={() => {}}>
          ↺
        </button>
      </div>

      <output data-testid="panning">{String(panning)}</output>
      <output data-testid="captured">{String(captured.current)}</output>
    </div>
  );
}

describe('画布平移守卫：缩放条上的按下不应被当成拖动画布', () => {
  it('在缩放按钮上按下不进入平移状态', () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByLabelText('放大'), { button: 0, pointerId: 1 });
    expect(screen.getByTestId('panning').textContent).toBe('false');
    expect(screen.getByTestId('captured').textContent).toBe('null');
  });

  it('缩放按钮的 click 仍能触发（不被 setPointerCapture 吞掉）', () => {
    render(<Harness />);
    const zoomIn = screen.getByLabelText('放大');
    const onClick = vi.fn();
    zoomIn.addEventListener('click', onClick);
    fireEvent.pointerDown(zoomIn, { button: 0, pointerId: 1 });
    fireEvent.click(zoomIn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('空白区域按下仍可平移（排除规则没有过度收紧）', () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId('canvas-pan-layer'), {
      button: 0,
      pointerId: 2,
    });
    expect(screen.getByTestId('panning').textContent).toBe('true');
  });

  it('节点上的按下不进入平移（既有行为未被破坏）', () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId('a-node'), { button: 0, pointerId: 3 });
    expect(screen.getByTestId('panning').textContent).toBe('false');
  });

  it('非主键（右键/中键）不触发平移', () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId('canvas-pan-layer'), {
      button: 2,
      pointerId: 4,
    });
    expect(screen.getByTestId('panning').textContent).toBe('false');
  });
});
