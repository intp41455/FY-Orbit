/**
 * P15 · `/dossier` 任务档案库与企业模式页。
 *
 * 三段，对应三条需求：
 *   1. **翻档案一秒上手**（A-上下文持久化-02）：贴任务 id → 档案全景 + 可粘贴简报。
 *      简报截断时**必须显示截断提示**（后端 `briefing.truncated` 为真），
 *      不能让用户以为这就是全量。
 *   2. **复盘与知识沉淀**（-03）：复盘报告 → 一键沉淀成可复用知识模板，下次直接套。
 *   3. **企业模式**（A-三重模式-03）：选目标框架 → 转换 → **逐条列出没被映射到的
 *      外部字段**（不静默丢弃）+ 企业接入文档。
 *
 * ⚠️ 页面挂载（`web/src/App.tsx`）与侧栏导航（`Layout.tsx`）由**主控**统一追加
 * （跨包锁三）；本文件只交付页面本身，挂载行见交付报告「集成请求」。
 */
import { useEffect, useState } from 'react';
import {
  distill,
  enterpriseAdapt,
  enterpriseCatalog,
  enterpriseOnboarding,
  fetchArchive,
  fetchBriefing,
  fetchRetrospective,
  knowledgeDetail,
  listKnowledge,
} from '../api/dossier';
import type {
  AdaptResult,
  BriefingResult,
  DistillResult,
  EnterpriseCatalog,
  KnowledgeItem,
  RetrospectiveResult,
  TaskArchive,
} from '../api/dossier';
import { Spinner, errorMessage, useAsync } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import '../styles/pages/dossier.css';

type Tab = 'archive' | 'knowledge' | 'enterprise';

export function DossierPage() {
  const [tab, setTab] = useState<Tab>('archive');
  const [taskId, setTaskId] = useState('');
  const [loadedId, setLoadedId] = useState<string | null>(null);

  const [archive, setArchive] = useState<TaskArchive | null>(null);
  const [briefing, setBriefing] = useState<BriefingResult | null>(null);
  const [retro, setRetro] = useState<RetrospectiveResult | null>(null);
  const [distilled, setDistilled] = useState<DistillResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(fn: () => Promise<T>, onOk: (v: T) => void) {
    setBusy(true);
    setError(null);
    try {
      onOk(await fn());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function loadTask() {
    const id = taskId.trim();
    if (!id) return;
    setLoadedId(id);
    setDistilled(null);
    run(async () => Promise.all([fetchArchive(id), fetchBriefing(id), fetchRetrospective(id)]),
        ([a, b, r]) => {
          setArchive(a);
          setBriefing(b);
          setRetro(r);
        });
  }

  return (
    <div className="fy-ds-page">
      <header className="fy-ds-head">
        <div>
          <h1>任务档案库</h1>
          <p className="muted">
            档案是<strong>读模型</strong>：目标 / 里程碑 / 分工 / 决策 / 产出版本全部从既有
            记录现算，不另存冗余；每个字段都能追到来源。
          </p>
        </div>
        <nav className="fy-ds-tabs" aria-label="档案库视图">
          {([['archive', '任务档案'], ['knowledge', '知识沉淀'], ['enterprise', '企业模式']] as const)
            .map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={tab === id ? 'fy-ds-tab active' : 'fy-ds-tab'}
                aria-pressed={tab === id}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
        </nav>
      </header>

      {error && <p className="fy-ds-error" role="alert">{error}</p>}

      {tab === 'archive' && (
        <section aria-label="任务档案">
          <div className="fy-ds-search">
            <label className="fy-ds-field">
              <span className="muted">任务 id</span>
              <input
                value={taskId}
                aria-label="任务 id"
                placeholder="例如 task-abc123"
                onChange={(e) => setTaskId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && loadTask()}
              />
            </label>
            <button type="button" className="primary" onClick={loadTask} disabled={busy || !taskId.trim()}>
              <LineIcon name="search" size={14} /> 翻档案
            </button>
            {busy && <Spinner label="载入档案…" />}
          </div>

          {archive && loadedId && <ArchiveView archive={archive} />}

          {briefing && (
            <section className="fy-ds-card" aria-label="一秒上手简报">
              <div className="fy-ds-card-head">
                <strong>一秒上手简报</strong>
                <button
                  type="button"
                  className="small ghost"
                  onClick={() => void navigator.clipboard?.writeText(briefing.briefing)}
                >
                  复制给新 Agent
                </button>
              </div>
              {briefing.truncated && (
                <p className="fy-ds-warn" role="status">
                  <LineIcon name="alert" size={14} /> 简报已截断（{briefing.chars}/{briefing.limit} 字符）：
                  完整档案见 {briefing.archive_endpoint}
                </p>
              )}
              <pre className="fy-ds-pre">{briefing.briefing}</pre>
            </section>
          )}

          {retro && (
            <section className="fy-ds-card" aria-label="任务复盘">
              <div className="fy-ds-card-head">
                <strong>任务复盘</strong>
                <button
                  type="button"
                  className="small"
                  disabled={busy || !loadedId}
                  onClick={() => loadedId && run(() => distill(loadedId), setDistilled)}
                >
                  沉淀为知识模板
                </button>
              </div>
              <ul className="fy-ds-metrics">
                <li>变更事件 <b>{retro.metrics.change_events}</b></li>
                <li>决策 <b>{retro.metrics.decision_count}</b></li>
                <li>成员 <b>{retro.metrics.member_count}</b></li>
                <li>产出版本 <b>{retro.metrics.attempt_versions}</b></li>
                <li>历时 <b>{retro.metrics.span_minutes ?? '未知'}</b> 分钟</li>
              </ul>
              <h4>经验条目</h4>
              <ul>
                {retro.lessons.map((l) => (
                  <li key={l.text}><span className="badge">{l.kind}</span> {l.text}</li>
                ))}
              </ul>
              <details>
                <summary className="muted">展开完整复盘报告</summary>
                <pre className="fy-ds-pre">{retro.report}</pre>
              </details>
            </section>
          )}

          {distilled && (
            <section className="fy-ds-card ok" aria-label="已沉淀">
              <strong>已沉淀为知识模板：{distilled.title}</strong>
              <p className="muted">落盘位置：{distilled.path}　·　切到「知识沉淀」页可直接复用。</p>
            </section>
          )}
        </section>
      )}

      {tab === 'knowledge' && <KnowledgeView />}
      {tab === 'enterprise' && <EnterpriseView />}
    </div>
  );
}

function ArchiveView({ archive }: { archive: TaskArchive }) {
  const o = archive.objective;
  return (
    <section className="fy-ds-card" aria-label="档案全景">
      <div className="fy-ds-card-head">
        <strong>{o.goal}</strong>
        <span className="badge">{o.status} / {o.stage}</span>
      </div>
      <dl className="fy-ds-facts">
        <div>
          <dt>进度</dt>
          <dd>{o.progress_percent === null ? '未开始' : `${o.progress_percent}%`}</dd>
        </div>
        <div><dt>截止</dt><dd>{o.deadline ?? '未设置'}</dd></div>
        <div><dt>领域 / 模式 / 策略</dt><dd>{o.domain} / {o.mode} / {o.strategy}</dd></div>
        <div><dt>卡点</dt><dd>{o.blocked_reason ?? '无'}</dd></div>
      </dl>

      <h4>分工表</h4>
      {archive.roster.members.length === 0 ? (
        <p className="muted">该任务未绑定团队或成员实例。</p>
      ) : (
        <table className="fy-ds-table">
          <thead>
            <tr><th scope="col">角色</th><th scope="col">状态</th>
              <th scope="col">模型</th><th scope="col">卡点</th></tr>
          </thead>
          <tbody>
            {archive.roster.members.map((m) => (
              <tr key={m.instance_id}>
                <td>{m.role}<div className="muted">{m.title}</div></td>
                <td>{m.state}</td>
                <td>{m.effective_model ?? m.requested_model ?? '继承默认'}</td>
                <td>{m.blocked_reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>决策日志（来自审计哈希链）</h4>
      {archive.decisions.length === 0 ? (
        <p className="muted">审计链上没有与本任务相关的帧。</p>
      ) : (
        <ul>{archive.decisions.map((d) => (
          <li key={d.seq}><code>#{d.seq}</code> {d.action}</li>
        ))}</ul>
      )}

      <h4>产出版本</h4>
      {archive.artifacts.length === 0 ? (
        <p className="muted">尚无尝试记录。</p>
      ) : (
        <ul>{archive.artifacts.map((a) => (
          <li key={a.version}>v{a.version}｜{a.status}｜检查点 {a.checkpoint_ref ?? '无'}</li>
        ))}</ul>
      )}

      {archive.notes.length > 0 && (
        <>
          <h4>哪些是空的、为什么</h4>
          <ul className="muted">{archive.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </>
      )}

      <details>
        <summary className="muted">每个字段的来源</summary>
        <pre className="fy-ds-pre">{JSON.stringify(archive.sources, null, 2)}</pre>
      </details>
    </section>
  );
}

function KnowledgeView() {
  const list = useAsync(() => listKnowledge(), []);
  const [open, setOpen] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState('');
  const [error, setError] = useState<string | null>(null);

  const items: KnowledgeItem[] = list.data?.items ?? [];

  return (
    <section aria-label="知识沉淀">
      {list.loading && <Spinner label="载入知识模板…" />}
      {list.error && <p className="fy-ds-error">{errorMessage(list.error)}</p>}
      {error && <p className="fy-ds-error" role="alert">{error}</p>}
      {!list.loading && items.length === 0 && (
        <p className="muted">还没有沉淀过知识模板：在「任务档案」里跑一次复盘后点「沉淀为知识模板」。</p>
      )}
      <ul className="fy-ds-knowledge">
        {items.map((k) => (
          <li key={k.knowledge_id}>
            <button
              type="button"
              className="fy-ds-knowledge-item"
              onClick={() =>
                void knowledgeDetail(k.knowledge_id)
                  .then((d) => {
                    setOpen(k.knowledge_id);
                    setMarkdown(d.markdown);
                    setError(null);
                  })
                  .catch((e) => setError(errorMessage(e)))
              }
            >
              <strong>{k.title}</strong>
              <span className="muted">源任务：{k.source_task_id}</span>
              {k.tags.map((t) => <span key={t} className="badge">{t}</span>)}
            </button>
          </li>
        ))}
      </ul>
      {open && <pre className="fy-ds-pre">{markdown}</pre>}
    </section>
  );
}

function EnterpriseView() {
  const catalog = useAsync(() => enterpriseCatalog(), []);
  const [target, setTarget] = useState('generic');
  const [adapted, setAdapted] = useState<AdaptResult | null>(null);
  const [onboarding, setOnboarding] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(fn: () => Promise<T>, onOk: (v: T) => void) {
    setBusy(true);
    setError(null);
    try {
      onOk(await fn());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const adapters = (catalog.data as EnterpriseCatalog | undefined)?.adapters ?? [];

  useEffect(() => {
    if (adapters.length > 0 && !adapters.some((a) => a.target === target)) {
      setTarget(adapters[0].target);
    }
  }, [adapters, target]);

  const sample = {
    name: 'contract-reviewer',
    system_prompt: '你是合同审阅助手，逐条核对风险条款并给出修改建议。',
    model: 'openai:gpt-4o',
    tools: [{ name: 'knowledge.search' }],
    max_steps: 12,
    memory: { type: 'short_term', window: 30 },
  };

  return (
    <section aria-label="企业模式">
      {catalog.loading && <Spinner label="载入适配器目录…" />}
      {catalog.error && <p className="fy-ds-error">{errorMessage(catalog.error)}</p>}
      {error && <p className="fy-ds-error" role="alert">{error}</p>}

      <div className="fy-ds-search">
        <label className="fy-ds-field">
          <span className="muted">目标框架</span>
          <select
            value={target}
            aria-label="目标框架"
            onChange={(e) => setTarget(e.target.value)}
          >
            {adapters.map((a) => <option key={a.target} value={a.target}>{a.label}</option>)}
          </select>
        </label>
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={() => run(() => enterpriseAdapt(target, sample), setAdapted)}
        >
          用示例定义试转换
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy}
          onClick={() => run(() => enterpriseOnboarding(target), (d) => setOnboarding(d.markdown))}
        >
          企业接入文档
        </button>
      </div>

      {catalog.data && (
        <p className="muted">
          权限与身份<strong>复用</strong>既有服务：{String(catalog.data.governance.permission_source)}
          —— 企业模式不提供第二套多租户。
        </p>
      )}

      {adapted && (
        <section className="fy-ds-card" aria-label="转换结果">
          <div className="fy-ds-card-head">
            <strong>转换结果 · {adapted.target}</strong>
            <span className="badge">未执行（{String(adapted.executed)}）</span>
          </div>
          <h4>已映射</h4>
          <ul>{adapted.mapped.map((m) => (
            <li key={`${m.from}->${m.to}`}><code>{m.from}</code> → <code>{m.to}</code></li>
          ))}</ul>
          <h4>没被映射到的字段（一个都没丢）</h4>
          {adapted.unmapped.length === 0
            ? <p className="muted">全部字段都有对应映射。</p>
            : <ul>{adapted.unmapped.map((u) => <li key={u}><code>{u}</code></li>)}</ul>}
          {adapted.warnings.length > 0 && (
            <ul className="fy-ds-warn-list">
              {adapted.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          <details>
            <summary className="muted">内部单循环规格</summary>
            <pre className="fy-ds-pre">{JSON.stringify(adapted.spec, null, 2)}</pre>
          </details>
        </section>
      )}

      {onboarding && (
        <section className="fy-ds-card" aria-label="企业接入文档">
          <pre className="fy-ds-pre">{onboarding}</pre>
        </section>
      )}
    </section>
  );
}

export default DossierPage;
