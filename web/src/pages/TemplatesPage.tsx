/**
 * P13 · `/templates` 开箱模板页（A-开箱模板-02/03/05/07/08）。
 *
 * 三件事按需求原文的顺序排：
 *   1. **选中即懂**（-07）：左侧选模板，右侧直接展示成员 / 职责 / 工具 / 产出 / 大致用量。
 *   2. **隐性必备条件**（-03）：八项出厂默认值 + 说明，可逐项覆盖、可恢复出厂。
 *   3. **总控预设**（-02）：提示词默认可见可编辑，改坏给警告但不阻断，可一键回滚。
 *
 * 技术层入口（-05 展开为代码）与「一键试跑」（-07②）在同一页一级可见，
 * 不埋进二级菜单——需求原文要求「一下子就找到」。
 *
 * ⚠️ 页面挂载（`web/src/App.tsx`）与侧栏导航（`Layout.tsx`）归**主控**统一追加
 * （跨包锁三）。本文件只交付页面本身，挂载行见交付报告的「集成请求」。
 */
import { useEffect, useMemo, useState } from 'react';
import {
  controllerCheck,
  exampleRun,
  expandToCode,
  fetchLayers,
  fetchQualityTiers,
  instantiate,
  listTemplates,
  restoreFactory,
  templateDetail,
} from '../api/templates';
import type {
  ExampleRunResult,
  ExpandToCodeResult,
  InstantiateResult,
  TemplateCard,
  TemplateDetail,
} from '../api/templates';
import { Spinner, errorMessage, useAsync } from '../components/ui';
import { ControllerPromptPanel } from '../components/templates/ControllerPromptPanel';
import { EssentialsPanel } from '../components/templates/EssentialsPanel';
import { SystemOverviewCard } from '../components/templates/SystemOverviewCard';
import '../styles/pages/templates.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

export function TemplatesPage() {
  const [tier, setTier] = useState('novice');
  const [scenario, setScenario] = useState<string>('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useAsync(
    () => listTemplates({ tier, ...(scenario ? { scenario } : {}) }),
    [tier, scenario],
  );
  const meta = useAsync(() => Promise.all([fetchLayers(), fetchQualityTiers()]), []);

  const cards: TemplateCard[] = list.data?.items ?? [];

  // 首次加载后自动选中第一套模板，避免右侧空着。
  useEffect(() => {
    if (!selectedId && cards.length > 0) setSelectedId(cards[0].template_id);
  }, [cards, selectedId]);

  const detail = useAsync<TemplateDetail | null>(
    () => (selectedId ? templateDetail(selectedId, { tier }) : Promise.resolve(null)),
    [selectedId, tier],
  );

  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  const [controllerDraft, setControllerDraft] = useState('');
  const [draftTouched, setDraftTouched] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [result, setResult] = useState<InstantiateResult | null>(null);
  const [code, setCode] = useState<ExpandToCodeResult | null>(null);
  const [trial, setTrial] = useState<ExampleRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // 切模板 / 切档位时重置本地编辑态。
  useEffect(() => {
    setOverrides({});
    setDraftTouched(false);
    setWarnings([]);
    setResult(null);
    setCode(null);
    setTrial(null);
    setActionError(null);
    setControllerDraft(detail.data?.controller.system_prompt ?? '');
  }, [selectedId, tier, detail.data]);

  const essentials = useMemo(
    () =>
      (detail.data?.essentials_view ?? []).map((item) =>
        item.key in overrides ? { ...item, value: overrides[item.key] } : item,
      ),
    [detail.data, overrides],
  );

  async function run<T>(fn: () => Promise<T>, onOk: (v: T) => void) {
    setBusy(true);
    setActionError(null);
    try {
      onOk(await fn());
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <BaseBound surface="templates">
      <div className="fy-tpl-page">
        <header className="fy-tpl-page-head">
          <div>
            <h1>开箱模板</h1>
            <p className="muted">
              出厂成套的多 Agent 系统：1 个总控 + 若干成员，选中即用，不用你补必填项。
            </p>
          </div>
          <div className="row" style={{ gap: '0.6rem', alignItems: 'center' }}>
            <label className="fy-tpl-field">
              <span className="muted">质量档位</span>
              <select value={tier} onChange={(e) => setTier(e.target.value)} aria-label="质量档位">
                {(meta.data?.[1].tiers ?? [{ id: 'novice', label: '小白档' }]).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="fy-tpl-field">
              <span className="muted">场景</span>
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                aria-label="场景筛选"
              >
                <option value="">全部场景</option>
                <option value="writing">写作流水线</option>
                <option value="research">调研流水线</option>
                <option value="development">研发流水线</option>
                <option value="data">数据分析流水线</option>
              </select>
            </label>
          </div>
        </header>

        <p className="muted fy-tpl-layer-note">
          三层并存，共用同一份模板数据：新手默认层隐藏复杂度；进阶可换层按场景切换；
          技术层可拆开、改拓扑、直接展开为代码。
          {(meta.data?.[0].layers ?? []).map((l) => (
            <span key={l.id} className="badge">
              {l.label}
            </span>
          ))}
        </p>

        <div className="fy-tpl-layout">
          <aside className="fy-tpl-list" aria-label="模板列表">
            {list.loading && <Spinner label="载入模板…" />}
            {list.error && <p className="fy-tpl-error">{errorMessage(list.error)}</p>}
            {cards.map((c) => (
              <button
                key={c.template_id}
                type="button"
                className={c.template_id === selectedId ? 'fy-tpl-card active' : 'fy-tpl-card'}
                aria-pressed={c.template_id === selectedId}
                onClick={() => setSelectedId(c.template_id)}
              >
                <strong>{c.name}</strong>
                <span className="muted">{c.summary || c.scenario_label}</span>
                <span className="badge">{c.member_count} 成员</span>
              </button>
            ))}
            {!list.loading && cards.length === 0 && !list.error && (
              <p className="muted">该场景下暂无出厂模板。</p>
            )}
          </aside>

          <main className="fy-tpl-detail">
            {detail.loading && <Spinner label="载入模板详情…" />}
            {detail.error && <p className="fy-tpl-error">{errorMessage(detail.error)}</p>}
            {actionError && <p className="fy-tpl-error" role="alert">{actionError}</p>}

            {detail.data && (
              <>
                {detail.data.problems.length > 0 && (
                  <div className="fy-tpl-warning" role="alert">
                    <strong>这套模板的结构有问题，缺什么列在下面：</strong>
                    <ul>
                      {detail.data.problems.map((p) => (
                        <li key={p}>{p}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <SystemOverviewCard overview={detail.data.overview} />

                <ControllerPromptPanel
                  controller={detail.data.controller}
                  draft={controllerDraft}
                  warnings={warnings}
                  touched={draftTouched}
                  busy={busy}
                  onDraftChange={(v) => {
                    setControllerDraft(v);
                    setDraftTouched(true);
                  }}
                  onCheck={() =>
                    selectedId
                      ? run(() => controllerCheck(selectedId, controllerDraft),
                          (r) => setWarnings(r.warnings))
                      : undefined
                  }
                  onRestore={() =>
                    selectedId
                      ? run(() => restoreFactory(selectedId, tier), (r) => {
                          setControllerDraft(r.controller_prompt);
                          setDraftTouched(false);
                          setWarnings([]);
                        })
                      : undefined
                  }
                />

                <EssentialsPanel
                  items={essentials}
                  busy={busy}
                  onOverride={(key, value) => setOverrides((prev) => ({ ...prev, [key]: value }))}
                  onRestoreFactory={() =>
                    selectedId
                      ? run(() => restoreFactory(selectedId, tier), () => {
                          setOverrides({});
                          setResult(null);
                        })
                      : undefined
                  }
                />

                <section className="fy-tpl-actions" aria-label="从看清到跑通">
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || !selectedId}
                    onClick={() =>
                      selectedId
                        ? run(
                            () =>
                              instantiate(selectedId, {
                                tier,
                                overrides: {
                                  ...overrides,
                                  ...(draftTouched ? { controller_prompt: controllerDraft } : {}),
                                },
                              }),
                            setResult,
                          )
                        : undefined
                    }
                  >
                    创建我的系统
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy || !selectedId}
                    onClick={() => selectedId && run(() => exampleRun(selectedId, tier), setTrial)}
                  >
                    一键试跑
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy || !selectedId}
                    onClick={() => selectedId && run(() => expandToCode(selectedId, tier), setCode)}
                  >
                    展开为代码（技术层）
                  </button>
                </section>

                {result && (
                  <section className={result.runnable ? 'fy-tpl-result ok' : 'fy-tpl-result bad'}
                           aria-label="创建结果">
                    <strong>{result.runnable ? '可以直接跑通一次完整任务' : '还差必填项，跑不通'}</strong>
                    <p>{result.message}</p>
                    {result.unresolved.length > 0 && (
                      <ul>
                        {result.unresolved.map((u) => (
                          <li key={u}>缺：{u}</li>
                        ))}
                      </ul>
                    )}
                    {result.warnings.map((w) => (
                      <p key={w} className="fy-tpl-warn-line">{w}</p>
                    ))}
                  </section>
                )}

                {trial && (
                  <section className="fy-tpl-trial" aria-label="一键试跑规格">
                    <strong>试跑示例任务（不用你写提示词）</strong>
                    <p>{trial.example_task.goal}</p>
                    {trial.example_task.prompt && (
                      <p className="muted">发给系统的指令：{trial.example_task.prompt}</p>
                    )}
                    <p className="muted">
                      约 {trial.steps} 步。{trial.note}
                    </p>
                  </section>
                )}

                {code && (
                  <section className="fy-tpl-code" aria-label="展开为代码">
                    <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <strong>已展开为受限 DSL 代码 · {code.filename}</strong>
                      <button
                        type="button"
                        className="small ghost"
                        onClick={() => void navigator.clipboard?.writeText(code.code)}
                      >
                        复制代码
                      </button>
                    </div>
                    <p className="muted">
                      往返一致：{code.roundtrip_consistent ? '是（模板 ↔ 代码往返无损）' : '否'} ·
                      节点 {code.node_count} · 边 {code.edge_count} · 动词集 {code.verbs.join(', ')}
                    </p>
                    <pre className="fy-tpl-code-block">{code.code}</pre>
                  </section>
                )}
              </>
            )}
          </main>
        </div>
      </div>
    </BaseBound>
  );
}

export default TemplatesPage;
