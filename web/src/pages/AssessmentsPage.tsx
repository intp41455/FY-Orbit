import { useEffect, useState } from 'react';
import { assessmentsApi } from '../api/assessments';
import type { AssessmentCatalogEntry, AssessmentSession } from '../api/types';
import { Spinner, errorMessage } from '../components/ui';

export function AssessmentsPage() {
  const [catalog, setCatalog] = useState<AssessmentCatalogEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<AssessmentSession | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submitting, setSubmitting] = useState(false);

  async function loadCatalog() {
    setLoading(true);
    setError(null);
    try {
      const res = await assessmentsApi.catalog();
      setCatalog(res.assessments);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadCatalog();
  }, []);

  async function start(entry: AssessmentCatalogEntry) {
    setError(null);
    try {
      const s = await assessmentsApi.startSession(entry.assessment_id);
      setSession(s);
      setAnswers({});
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function answeredCount(): number {
    return Object.keys(answers).length;
  }

  async function submit(entry: AssessmentCatalogEntry) {
    if (!session) return;
    if (answeredCount() < entry.items.length) {
      // Never fabricate a result for missing answers.
      setError(
        `还有 ${entry.items.length - answeredCount()} 题未作答。漏答/空答不会生成默认人格结果；请补全后再提交。`,
      );
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const s = await assessmentsApi.submit(session.id, { answers });
      setSession(s);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading && !catalog) return <Spinner />;

  return (
    <>
      <div className="page-head">
        <h2>测评</h2>
        {session && <button onClick={() => setSession(null)}>返回目录</button>}
      </div>
      {error && <div className="notice danger" role="alert">{error}</div>}

      {!session && catalog && (
        <div className="grid cols-3">
          {catalog.map((a) => (
            <div className="card" key={a.assessment_id}>
              <div className="row spread">
                <strong>{a.title}</strong>
                <span className="badge accent">v{a.version}</span>
              </div>
              <p className="muted">{a.description}</p>
              <p className="muted" style={{ fontStyle: 'italic' }}>{a.compliance_notice}</p>
              <div className="muted">题目数：{a.items.length}</div>
              <button className="primary" style={{ marginTop: '0.6rem' }} onClick={() => void start(a)}>
                开始测评
              </button>
            </div>
          ))}
        </div>
      )}

      {session && catalog && (() => {
        const entry = catalog.find((c) => c.assessment_id === session.assessment_id)!;
        return (
          <div className="card">
            <div className="row spread">
              <h3 style={{ margin: 0 }}>{entry.title}</h3>
              <span className="badge accent">问卷版本 v{entry.version}</span>
            </div>
            <p className="muted" style={{ fontStyle: 'italic' }}>{entry.compliance_notice}</p>

            {session.result ? (
              <div>
                <div className="notice info">
                  计分由服务端完成；以下结果绑定问卷版本 v{session.questionnaire_version}。
                </div>
                {session.result.type_label && (
                  <h3>结果：{session.result.type_label}</h3>
                )}
                <div className="grid cols-2">
                  {Object.entries(session.result.scales).map(([k, v]) => (
                    <div className="card" key={k}>
                      <div className="muted">{k}</div>
                      <strong>{v}</strong>
                    </div>
                  ))}
                </div>
                <p>{session.result.interpretation}</p>
                <p className="notice warn">{session.result.caveat}</p>
                <p className="muted">{session.result.norm_note}</p>
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit(entry);
                }}
              >
                <div className="muted" style={{ marginBottom: '0.8rem' }}>
                  已答 {answeredCount()} / {entry.items.length}。带「反向计分」标记的题由服务端按问卷版本处理；
                  留空不会被默认填答。
                </div>
                {entry.items.map((item, idx) => (
                  <fieldset key={item.id} style={{ border: '1px solid var(--border)', borderRadius: 8, marginBottom: '0.7rem', padding: '0.6rem' }}>
                    <legend className="muted">第 {idx + 1} 题{item.reverse_scored ? ' · 反向计分' : ''}</legend>
                    <label htmlFor={item.id} style={{ color: 'var(--text)', fontSize: '0.95rem' }}>{item.text}</label>
                    <select
                      id={item.id}
                      value={answers[item.id] ?? ''}
                      onChange={(e) =>
                        setAnswers((a) => ({
                          ...a,
                          [item.id]: e.target.value === '' ? 0 : Number(e.target.value),
                        }))
                      }
                      style={{ marginTop: '0.4rem' }}
                    >
                      <option value="">未作答</option>
                      {Array.from({ length: entry.scale_max - entry.scale_min + 1 }, (_, i) => entry.scale_min + i).map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  </fieldset>
                ))}
                <button className="primary" type="submit" disabled={submitting || answeredCount() === 0}>
                  {submitting ? '提交计分中…' : '提交（服务端计分）'}
                </button>
                {answeredCount() < entry.items.length && (
                  <div className="muted" style={{ marginTop: '0.5rem' }}>
                    提示：仍有漏答；提交会被拒绝并要求补全，不会产生默认结果。
                  </div>
                )}
              </form>
            )}
          </div>
        );
      })()}
    </>
  );
}
