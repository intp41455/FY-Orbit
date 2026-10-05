/**
 * 包 E 私有图标（局部补充集）
 * ---------------------------------------------------------------------------
 * `components/ui/LineIcon.tsx` 是全局单一写入者文件，包 E 无权修改（总纲 §8.1）。
 * 本文件按同一规范补齐本域需要的 3 枚图标：24×24、stroke 1.5、round cap/join、
 * fill:none、颜色继承 currentColor、路径无绿色。
 *
 * ⚠ 顺带发现的地基缺陷（交付报告已列，交收口包裁决）：
 *   总纲 §3 状态表要求「等待/审批 搭配 LineIcon clock」，
 *   但 LineIcon.tsx 集里**没有 clock**。包 D 与包 E 各自建了局部实现，
 *   建议收口包把下面 4 枚一次性补录进全局集，然后两处局部实现即可删除。
 *     clock · palette · door · map · compass · snowflake · gems · heart
 */
import type { ReactNode } from 'react';

const S = { fill: 'none', stroke: 'currentColor' } as const;

const LOCAL_ICONS = {
  clock: (
    <>
      <circle cx="12" cy="12" r="8.6" {...S} />
      <path d="M12 7.4V12l3.2 2" {...S} />
    </>
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="8.8" {...S} />
      <path d="m15.2 8.8-1.9 4.5-4.5 1.9 1.9-4.5z" {...S} />
    </>
  ),
  snowflake: (
    <>
      <path d="M12 2.6v18.8M3.6 7.2l16.8 9.6M20.4 7.2 3.6 16.8" {...S} />
      <path d="m9.4 4.8 2.6 2.6 2.6-2.6M9.4 19.2l2.6-2.6 2.6 2.6" {...S} />
    </>
  ),
  cube: (
    <>
      <path d="m12 2.6 8.6 4.8v9.2L12 21.4l-8.6-4.8V7.4Z" {...S} />
      <path d="m3.4 7.4 8.6 4.8 8.6-4.8M12 12.2v9.2" {...S} />
    </>
  ),
  map: (
    <>
      <path d="m2.8 6.4 6-2.6 6.4 2.6 6-2.6v13.8l-6 2.6-6.4-2.6-6 2.6Z" {...S} />
      <path d="M8.8 3.8v16.2M15.2 6.4v16.2" {...S} />
    </>
  ),
  door: (
    <>
      <path d="M5.4 3.6h9.2a1 1 0 0 1 1 1v14.8a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1V4.6a1 1 0 0 1 1-1Z" {...S} />
      <path d="M14.4 12.2v1.6" {...S} />
      <path d="M2.6 20.4h18.8" {...S} />
    </>
  ),
} as const;

export type KnowledgeNiIconName = keyof typeof LOCAL_ICONS;

export interface KnowledgeNiIconProps {
  name: KnowledgeNiIconName;
  size?: number;
  title?: string;
  className?: string;
}

export function KnowledgeNiIcon({ name, size = 18, title, className }: KnowledgeNiIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      style={{ flex: '0 0 auto', display: 'block' }}
    >
      {title ? <title>{title}</title> : null}
      {LOCAL_ICONS[name] as ReactNode}
    </svg>
  );
}

/** 需要补录到全局 LineIcon 的图标名（交付报告用）。 */
export const KNOWLEDGE_NI_ICON_NAMES: readonly KnowledgeNiIconName[] = [
  'clock',
  'compass',
  'snowflake',
  'cube',
  'map',
  'door',
];