// W11 · 角色预览：把 RGBA 快照画到 canvas，nearest 整数倍放大 + 两帧动画。
//
// 为什么用 canvas 而不是 <img>：像素画必须 imageSmoothingEnabled=false，
// nearest 是灵魂；CSS 放大 SVG/div 在部分浏览器会引入插值模糊。
//
// 动画：任务书要求「两帧待机呼吸 + 两帧行走」。这里是**帧索引轮播**，
// 帧由 avatarPixels.idleFrames / walkFrames 在前端从 8 层矩阵确定性派生，
// 后端只负责给底稿（诚实：不假装后端下发动画帧）。

import { useEffect, useRef, useState } from 'react';
import type { AvatarFrame } from './avatarPixels';

export type AvatarAnimation = 'idle' | 'walk' | 'static';

interface Props {
  frames: [AvatarFrame, AvatarFrame];
  palette: Record<string, string>;
  animation?: AvatarAnimation;
  /** 整数放大倍数（nearest）。默认 6 → 24×32 放大为 144×192。 */
  scale?: number;
  /** 每帧停留毫秒。 */
  intervalMs?: number;
  alt?: string;
  className?: string;
}

function drawFrame(
  canvas: HTMLCanvasElement,
  frame: AvatarFrame,
  palette: Record<string, string>,
  scale: number,
): void {
  const ctx = canvas.getContext('2d');
  if (!ctx) return; // 无 2d 上下文（测试环境 / 老浏览器）时静默跳过渲染，不抛错打断页面
  const rows = frame.matrix;
  const h = rows.length;
  const w = rows[0]?.length ?? 0;
  canvas.width = w * scale;
  canvas.height = h * scale;
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (let y = 0; y < h; y++) {
    const row = rows[y];
    for (let x = 0; x < row.length; x++) {
      const ch = row[x];
      if (ch === '.' || ch === ' ') continue;
      const hex = palette[ch];
      // 缺色不画：宁可少一块也不画错颜色（诚实：配色漂移应由测试发现，而不是被画面掩盖）
      if (!hex) continue;
      ctx.fillStyle = hex;
      ctx.fillRect(x * scale, y * scale, scale, scale);
    }
  }
}

export function AvatarPreview({
  frames,
  palette,
  animation = 'idle',
  scale = 6,
  intervalMs = 620,
  alt = '我的像素小人',
  className,
}: Props) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (animation === 'static') return;
    const id = window.setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => window.clearInterval(id);
  }, [animation, intervalMs]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    drawFrame(canvas, frames[tick % 2], palette, scale);
  }, [frames, palette, scale, tick]);

  return (
    <canvas
      ref={ref}
      className={className ?? 'avatar-preview'}
      role="img"
      aria-label={`${alt}（${animation === 'idle' ? '待机呼吸' : animation === 'walk' ? '行走' : '静止'}）`}
      style={{ imageRendering: 'pixelated' }}
    />
  );
}