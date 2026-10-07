/**
 * P9 · 点哪评哪子组件桶（barrel）。
 *
 * 供 `ReviewMode.tsx` 统一从 `./review` 引入，避免散落的相对路径。
 */
export {
  REVIEW_UI_ATTR,
  buildMarker,
  buildSelector,
  captureDomTarget,
  computeDomPath,
  currentViewport,
  dragToNormalizedRegion,
  isInsideReviewUi,
  isUsableRegion,
  normalizeStroke,
  previewShortCode,
  toNormalizedRegion,
  type DomTargetCapture,
  type NormalizedRegion,
  type Stroke,
  type StrokePoint,
} from './geometry';
export { RegionSelectOverlay, type RegionSelectOverlayProps } from './RegionSelectOverlay';
export { AnnotationCanvas, type AnnotationCanvasProps } from './AnnotationCanvas';
export { RefreshLoopBar, useRefreshLoop, type RefreshLoopProps } from './RefreshLoop';
export {
  ReviewNoteCard,
  ReviewNoteList,
  type ReviewNoteCardProps,
  type ReviewNoteListProps,
} from './ReviewNoteList';
