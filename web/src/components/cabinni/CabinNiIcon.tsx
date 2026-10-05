/**
 * 包 D 私有图标（局部补充集）
 * ---------------------------------------------------------------------------
 * 为什么不直接改 `components/ui/LineIcon.tsx`：它是全局单一写入者文件，
 * 包 D 无权修改（总纲 §8.1 冻结清单）。
 *
 * 规范与 LineIcon 完全一致：24×24 网格、stroke 1.5、round cap + round join、
 * fill:none、颜色继承 currentColor、无渐变无阴影、路径里不出现绿色。
 *
 * 需要补录到全局集的名字（交付报告已列，交给收口包裁决）：
 *   - door    （门 / 进出室内）
 *   - map     （地图 / 场景切换）
 *   - gems    （宝石 / 素材库）
 *   - heart   （心 / 好感轨道）
 *   - clock   ⚠ 总纲 §3 状态表要求「等待/审批 搭配 LineIcon clock」，
 *             但当前 LineIcon.tsx 集里**没有** clock —— 这是地基缺陷，
 *             本包按规范自建局部实现并上报补录，不擅自改冻结文件。
 *   - palette （调色板 / 色板）
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
  palette: (
    <>
      <path d="M12 3.2a8.8 8.8 0 0 0 0 17.6c1.2 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.2-.3-.4-.5-.8-.5-1.2 0-1 .8-1.8 1.8-1.8h1.6a4.4 4.4 0 0 0 4.4-4.4c0-3.9-3.9-7.2-8.8-7.2Z" {...S} />
      <circle cx="8.4" cy="10.4" r="1.1" {...S} />
      <circle cx="12" cy="7.6" r="1.1" {...S} />
      <circle cx="15.6" cy="10" r="1.1" {...S} />
    </>
  ),
  door: (
    <>
      <path d="M5.4 3.6h9.2a1 1 0 0 1 1 1v14.8a1 1 0 0 1-1 1H5.4a1 1 0 0 1-1-1V4.6a1 1 0 0 1 1-1Z" {...S} />
      <path d="M14.4 12.2v1.6" {...S} />
      <path d="M2.6 20.4h18.8" {...S} />
    </>
  ),
  map: (
    <>
      <path d="m2.8 6.4 6-2.6 6.4 2.6 6-2.6v13.8l-6 2.6-6.4-2.6-6 2.6Z" {...S} />
      <path d="M8.8 3.8v16.2M15.2 6.4v16.2" {...S} />
    </>
  ),
  gems: (
    <>
      <path d="m8 3.6 3.2 4.6L8 12.8 4.8 8.2Z" {...S} />
      <path d="m16.4 10.6 2.6 3.8-2.6 3.8-2.6-3.8Z" {...S} />
      <path d="m9.4 15.4 2.8 4-2.8 4-2.8-4Z" {...S} />
    </>
  ),
  heart: <path d="M12 20.4S3.6 15.2 3.6 9.4a4.4 4.4 0 0 1 8.4-1.8 4.4 4.4 0 0 1 8.4 1.8c0 5.8-8.4 11-8.4 11Z" {...S} />,
} as const;

export type CabinNiIconName = keyof typeof LOCAL_ICONS;

export interface CabinNiIconProps {
  name: CabinNiIconName;
  size?: number;
  title?: string;
  className?: string;
}

export function CabinNiIcon({ name, size = 18, title, className }: CabinNiIconProps) {
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

/** 供交付报告列出需要补录到全局 LineIcon 的图标名。 */
export const CABIN_NI_ICON_NAMES: readonly CabinNiIconName[] = [
  'clock',
  'palette',
  'door',
  'map',
  'gems',
  'heart',
];