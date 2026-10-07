/**
 * 三重模式切换器 + 模板起手（A-三重模式-01/04）。
 *
 * 三种模式是**同一套 IR 的三个入口**（小白画布 / 代码 SDK / 企业治理），不是三套
 * 互不兼容的东西。界面语言也只有一句：换入口，不换图。
 *
 * 数据全部来自后端 `GET /api/dsl/modes`（`dsl_sdk.mode_overview()`）——
 * 入口名、起手模板、**模板真正的 DSL 文档**都是后端给的，前端不另建一份同名
 * 假模板（那就是第二套图定义了）。拉不到时如实显示「模式清单不可用」并保留
 * 当前模式，不假装只有一种模式。
 *
 * 交互刻意用**按钮**而不是 `<select>`：这里是导航，不是可编辑控件；
 * 而且 `DslCanvasPage` 本身是「纯展示页壳」（豁免在案），别在这里引入
 * `<select>` / `onChange=` 让它的豁免前提失效。
 */
import { useCallback, useEffect, useState } from 'react';

import { dslCanvasApi, type DslDocument, type DslMode, type DslModeOverview } from '../../api/dslCanvas';
import { useBase } from '../../hooks/useAutosave';

export interface UseDslModes {
  modes: DslModeOverview[];
  loading: boolean;
  error: string;
}

/** 拉取三模式概览；失败如实上报（`error` 非空），不静默回落成单模式。 */
export function useDslModes(): UseDslModes {
  const [modes, setModes] = useState<DslModeOverview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    void dslCanvasApi.modes()
      .then((r) => { if (alive) { setModes(r.modes ?? []); setError(''); } })
      .catch((e: { body?: { message?: string } }) => {
        if (alive) setError(e?.body?.message ?? '模式清单不可用');
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  return { modes, loading, error };
}

export interface ModeSwitcherProps {
  mode: DslMode;
  /**
   * 切换模式。刻意叫 `onSelect` 而不是 `onChange`：这里是**导航**，不是可编辑
   * 控件；名字与实际语义一致，也避免让「纯展示页壳」的豁免前提被带偏。
   */
  onSelect: (mode: DslMode) => void;
  overview: UseDslModes;
}

export function ModeSwitcher({ mode, onSelect, overview }: ModeSwitcherProps) {
  useBase({ surface: 'web/src/components/workflow/ModeSwitcher' });

  const { modes, loading, error } = overview;
  const current = modes.find((m) => m.mode === mode);

  return (
    <div className="ui-panel ui-panel--flat ui-panel--pad" data-testid="mode-switcher">
      <div
        className="ui-tabs"
        role="tablist"
        aria-label="创作模式（同一套 IR 的三个入口）"
      >
        {modes.map((m) => (
          <button
            key={m.mode}
            type="button"
            role="tab"
            aria-selected={m.mode === mode}
            className="ui-tab"
            onClick={() => onSelect(m.mode)}
            data-testid={`mode-tab-${m.mode}`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {loading && <p className="ui-hint" data-testid="mode-loading">正在读取三模式入口…</p>}

      {!loading && error && (
        <p className="ui-hint" role="alert" data-testid="mode-error">
          模式清单不可用（{error}）：当前仍是「{mode}」入口，画布可正常编辑。
        </p>
      )}

      {!loading && !error && modes.length !== 3 && (
        <p className="ui-hint" role="alert" data-testid="mode-count-warning">
          期望 3 个创作模式，实际收到 {modes.length} 个 —— 不静默补齐。
        </p>
      )}

      {current && (
        <p className="ui-hint" data-testid="mode-hint">
          {current.entries.map((e) => e.description).filter(Boolean).join('；')}
        </p>
      )}
    </div>
  );
}

export interface TemplateStarterProps {
  /** 当前模式的起手模板（来自 `/api/dsl/modes`）。 */
  templates: DslModeOverview['templates'];
  defaultTemplate: string;
  onPick: (doc: DslDocument, templateId: string) => void;
}

/** 小白入口的「模板起手」：点一个模板，把**后端给的**那张图放进画布。 */
export function TemplateStarter({ templates, defaultTemplate, onPick }: TemplateStarterProps) {
  useBase({ surface: 'web/src/components/workflow/TemplateStarter' });
  const [picked, setPicked] = useState('');

  const pick = useCallback((t: DslModeOverview['templates'][number]) => {
    setPicked(t.template_id);
    onPick(t.dsl, t.template_id);
  }, [onPick]);

  // 刻意**不自动载入**默认模板：切回小白模式就默默把画布换成另一张图，
  // 等于替用户丢掉他刚搭的东西。默认模板用主色按钮标出来，一点即用。
  const preferred = templates.find((t) => t.template_id === defaultTemplate)
    ?? templates[0];

  if (!templates.length) return null;

  return (
    <div className="ui-panel ui-panel--flat ui-panel--pad" data-testid="template-starter">
      <strong>从模板起手</strong>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
        {templates.map((t) => (
          <button
            key={t.template_id}
            type="button"
            className={`btn btn-sm${t.template_id === preferred?.template_id ? ' btn-primary' : ''}`}
            aria-pressed={picked === t.template_id}
            onClick={() => pick(t)}
            title={t.description}
            data-testid={`template-${t.template_id}`}
          >
            {t.label}
            {t.template_id === preferred?.template_id ? '（推荐）' : ''}
          </button>
        ))}
      </div>
      <p className="ui-hint" style={{ margin: '6px 0 0' }}>
        模板就是后端产出的那张图；载入之后照旧可拖、可连线、可运行。
      </p>
    </div>
  );
}
