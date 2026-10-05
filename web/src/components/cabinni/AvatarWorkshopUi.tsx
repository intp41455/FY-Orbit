/**
 * 包 D 私有组件 · 角色工坊预览台 / 参数调节 / 素材墙
 * ---------------------------------------------------------------------------
 * ⛔ 像素-玻璃边界（包 D 任务书 §4）：
 *   `.cabin-ni-stage` 内是 canvas，因此**绝不给它加 backdrop-filter**——
 *   毛玻璃会每帧重采样采样区，像素画直接糊掉。
 *   玻璃只用在 canvas **外面**的容器（.cabin-ni-share / .cabin-ni-asset）。
 *
 * 「参数改动即时预览」（最少点击守则第 5 条）：不点「生成」也能看到变化。
 * 实现方式是本地重绘 —— `AvatarPreview` 本身就是纯 canvas 像素重绘，
 * 改 tuning/hueShift 后父级重新请求即可，不需要打后端。
 * 但**诚实性**要点：微调后的真实底稿指纹要等后端确认，
 * 所以预览区同时显示「本地预览（未提交）」，绝不假装已经生效。
 */
import { LineIcon } from '../../components/ui/LineIcon';
import { AvatarPreview, type AvatarAnimation } from '../avatar/AvatarPreview';
import { CabinNiIcon } from './CabinNiIcon';

/* ------------------------------------------------------------------ */
/* 预览台                                                              */
/* ------------------------------------------------------------------ */

export interface AvatarStageProps {
  frames: Parameters<typeof AvatarPreview>[0]['frames'];
  palette: Record<string, string>;
  animation: AvatarAnimation;
  onAnimation: (a: AvatarAnimation) => void;
  dirty: boolean;
}

export function AvatarStage({ frames, palette, animation, onAnimation, dirty }: AvatarStageProps) {
  return (
    <div className="cabin-ni-stage" data-testid="avatar-ni-stage">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-3)', alignItems: 'center' }}>
        <AvatarPreview
          frames={animation === 'walk' ? frames : frames}
          palette={palette}
          animation={animation}
          scale={8}
        />
        <div className="ui-tabs" role="group" aria-label="动画预览">
          {(
            [
              ['idle', '待机呼吸'],
              ['walk', '行走'],
              ['static', '静止'],
            ] as const
          ).map(([a, label]) => (
            <button
              key={a}
              type="button"
              className="ui-tab"
              aria-selected={animation === a}
              data-testid={`avatar-ni-anim-${a}`}
              onClick={() => onAnimation(a)}
            >
              {label}
            </button>
          ))}
        </div>
        {dirty && (
          <span className="ui-badge ui-badge--verifying" data-testid="avatar-ni-dirty">
            <LineIcon name="refresh" size={14} />
            本地预览（未提交）
          </span>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 参数调节（滑块 + 配色）                                              */
/* ------------------------------------------------------------------ */

export interface TuningRowProps {
  label: string;
  /** 当前值的中文含义提示（选项本身保持后端枚举原值，见页面注释）。 */
  gloss?: string;
  children: React.ReactNode;
}

/**
 * 参数调节行。
 *
 * 关键：`<label>` 包住控件 —— 点标签文字也能聚焦/切换控件，
 * 这就是「点击区域共享」的落地（无障碍：无死区）。
 */
export function TuningRow({ label, gloss, children }: TuningRowProps) {
  return (
    <label className="cabin-ni-field" style={{ cursor: 'pointer' }}>
      <span className="cabin-ni-field-label">{label}</span>
      {children}
      {gloss && <span className="cabin-ni-field-hint">{gloss}</span>}
    </label>
  );
}

export interface SliderRowProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  display: string;
  onChange: (v: number) => void;
  testId?: string;
}

export function SliderRow({ label, value, min, max, step = 1, display, onChange, testId }: SliderRowProps) {
  return (
    <TuningRow label={`${label}：${display}`}>
      <input
        type="range"
        className="cabin-ni-slider"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={label}
        data-testid={testId}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </TuningRow>
  );
}

/* ------------------------------------------------------------------ */
/* 配色色板                                                            */
/* ------------------------------------------------------------------ */

/**
 * 配色选项用**中文标签 + 色点**双通道表达。
 *
 * 色板里出现的都是发色/肤色这类**素材属性**，不是 UI 状态语义，
 * 因此不套九档状态色；但色点本身仍是「当前选中」的唯一视觉线索，
 * 所以必须同时有 `aria-pressed` 与文字，避免只靠颜色区分。
 */
export interface SwatchOption {
  value: string;
  label: string;
  /** 可选色点（十六进制）；没有就只出文字。 */
  dot?: string;
}

export function SwatchGroup({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string;
  options: readonly SwatchOption[];
  value: string;
  onChange: (v: string) => void;
  testId?: string;
}) {
  return (
    <TuningRow label={label}>
      <span className="cabin-ni-swatches" data-testid={testId}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className="cabin-ni-swatch"
            aria-pressed={value === o.value}
            title={o.label}
            onClick={() => onChange(o.value)}
          >
            {o.dot && (
              <span
                className="cabin-ni-swatch-dot"
                style={{ background: o.dot }}
                aria-hidden="true"
              />
            )}
            {o.label}
          </button>
        ))}
      </span>
    </TuningRow>
  );
}

/* ------------------------------------------------------------------ */
/* 素材墙                                                              */
/* ------------------------------------------------------------------ */

export interface AssetWallItem {
  id: string;
  name: string;
  meta: string;
  /** 缩略图 URL；没有就走占位。 */
  thumb?: string;
}

export interface AssetWallProps {
  items: AssetWallItem[];
  onPick: (item: AssetWallItem) => void;
  emptyHint: string;
}

/**
 * 素材墙 = 「上传创意挂件」入口。
 *
 * 整块可点 ≥48px；hover 显「使用」次要操作（absolute，不占常驻布局）。
 * 空态给一句可执行的话（去资产库上传），而不是干巴巴一句「暂无数据」。
 */
export function AssetWall({ items, onPick, emptyHint }: AssetWallProps) {
  if (items.length === 0) {
    return (
      <p className="cabin-ni-evi-empty" data-testid="avatar-ni-asset-empty">
        <CabinNiIcon name="gems" size={16} />
        {emptyHint}
      </p>
    );
  }

  return (
    <div className="cabin-ni-assets" data-testid="avatar-ni-asset-wall">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className="cabin-ni-asset cabin-ni-glass"
          onClick={() => onPick(item)}
          data-testid={`avatar-ni-asset-${item.id}`}
          title={item.name}
        >
          <span className="cabin-ni-asset-thumb">
            {item.thumb ? (
              <img src={item.thumb} alt="" width={128} height={84} loading="lazy" />
            ) : (
              <CabinNiIcon name="gems" size={24} />
            )}
          </span>
          <span className="cabin-ni-asset-name">{item.name}</span>
          <span className="cabin-ni-asset-meta">{item.meta}</span>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 分享卡                                                              */
/* ------------------------------------------------------------------ */

/**
 * 分享卡容器（纯玻璃，可以发光）。
 *
 * canvas 本身 `image-rendering: pixelated` 保持硬边；玻璃只在它的**边框容器**上。
 */
export function ShareCardFrame({
  canvasRef,
  caption,
  shortCode,
  baseShortCode,
  tuned,
  excludedCount,
}: {
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  caption: string;
  shortCode: string;
  baseShortCode: string;
  tuned: boolean;
  excludedCount: number;
}) {
  return (
    <div className="cabin-ni-share" data-testid="avatar-ni-share">
      <canvas
        ref={canvasRef}
        className="avatar-share-card"
        role="img"
        aria-label="我的像素小人分享卡预览"
      />
      <p style={{ margin: 0, fontSize: 'var(--ui-fs-sm)', color: 'var(--ui-ink-2)' }}>{caption}</p>
      <p style={{ margin: 0, fontSize: 'var(--ui-fs-xs)', color: 'var(--ui-ink-3)' }}>
        短码 <code>{shortCode}</code>
        {tuned && (
          <>
            {' '}
            （微调自底稿 <code>{baseShortCode}</code>）
          </>
        )}
        {' · '}已隐藏 {excludedCount} 项未勾选信息
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 分享徽章勾选                                                        */
/* ------------------------------------------------------------------ */

/**
 * 徽章选择。
 *
 * ⚠ 隐私红线（诚实契约第 4 条）：分享卡默认**一个都不勾**。
 *   零隐私泄露不是默认值，是**刻意的默认值**——所以这里不设任何默认勾选，
 *   并且把「默认零勾选」这句话直接写在界面上。
 */
export function BadgePicker({
  fields,
  checked,
  onToggle,
}: {
  fields: readonly string[];
  checked: string[];
  onToggle: (field: string) => void;
}) {
  return (
    <div className="cabin-ni-badges" data-testid="avatar-ni-badges">
      {fields.map((f) => (
        <label className="cabin-ni-badge" key={f}>
          <input
            type="checkbox"
            name={`share-badge-${f}`}
            checked={checked.includes(f)}
            onChange={() => onToggle(f)}
          />
          <span>{f}</span>
        </label>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 元信息表                                                            */
/* ------------------------------------------------------------------ */

export interface MetaRow {
  label: string;
  value: string;
}

/** 元信息用定义列表（语义正确，且比 div 拼接更易被读屏理解）。 */
export function MetaList({ rows }: { rows: MetaRow[] }) {
  const ICONS = ['target', 'key', 'layers', 'palette', 'check', 'refresh'] as const;
  return (
    <dl className="avatar-meta" style={{ margin: 0 }}>
      {rows.map((r, i) => {
        const icon = ICONS[i % ICONS.length];
        return (
          <div key={r.label}>
            <dt>
              {icon === 'palette' ? (
                <CabinNiIcon name="palette" size={14} />
              ) : (
                <LineIcon name={icon} size={14} />
              )}{' '}
              {r.label}
            </dt>
            <dd>{r.value}</dd>
          </div>
        );
      })}
    </dl>
  );
}