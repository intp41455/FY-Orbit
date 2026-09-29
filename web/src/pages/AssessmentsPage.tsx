import { useEffect, useState } from 'react';
import {
  assessmentsApi,
  type QuestionnaireInfo,
  type RealAssessmentSession,
} from '../api/assessments';
import { Spinner, errorMessage } from '../components/ui';

// Likert scale used by the synthetic template; scoring/reverse-scoring is always
// done server-side and bound to questionnaire_version. The UI never computes
// scores and never defaults a blank answer.
const SCALE = [1, 2, 3, 4, 5];

export function AssessmentsPage() {
  const [catalog, setCatalog] = useState<QuestionnaireInfo[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<RealAssessmentSession | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await assessmentsApi.catalog();
        setCatalog(Array.isArray(res.questionnaires) ? res.questionnaires : []);
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function start(q: QuestionnaireInfo) {
    setError(null);
    try {
      const s = await assessmentsApi.startSession(q.id);
      setSession(s);
      setAnswers({});
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function submit() {
    if (!session) return;
    // Missing answers are authoritative from the server (`missing`). If any item
    // is still unanswered, refuse to submit — never fabricate a result client-side.
    if (session.missing.length > 0) {
      setError(
        `还有 ${session.missing.length} 题未作答。漏答/空答不会生成人格结果；请补全后再提交。`,
      );
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const s = await assessmentsApi.submit(session.session_id, answers);
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
          {catalog.map((q) => (
            <div className="card" key={q.id}>
              <div className="row spread">
                <strong>{q.title}</strong>
                <span className="badge accent">v{q.version}</span>
              </div>
              <p className="muted" style={{ fontStyle: 'italic' }}>{q.source_note}</p>
              <div className="muted">题目数：{q.item_count}</div>
              <button className="primary" style={{ marginTop: '0.6rem' }} onClick={() => void start(q)}>
                开始测评
              </button>
            </div>
          ))}
        </div>
      )}

      {session && (() => {
        const done = Object.keys(answers).length;
        const remaining = session.missing.filter((id) => answers[id] === undefined);
        return (
          <div className="card">
            <div className="row spread">
              <h3 style={{ margin: 0 }}>问卷 v{session.questionnaire_version}</h3>
              <span className="badge accent">item_set {session.item_set_hash.slice(0, 12)}</span>
            </div>
            <div className="muted" style={{ marginBottom: '0.8rem' }}>
              计分由服务端完成，结果绑定问卷版本 v{session.questionnaire_version}。
              反向题与合成分由服务端按版本处理；留空不会被默认填答。
            </div>

            {session.result ? (
              <div>
                <div className="notice info">
                  计分由服务端完成；以下结果绑定问卷版本 v{session.questionnaire_version}。
                </div>
                {session.result.type_label && <h3>结果：{session.result.type_label}</h3>}
                <div className="grid cols-2">
                  {Object.entries(session.result.scales ?? {}).map(([k, v]) => (
                    <div className="card" key={k}>
                      <div className="muted">{k}</div>
                      <strong>{v}</strong>
                    </div>
                  ))}
                </div>
                {session.result.interpretation && <p>{session.result.interpretation}</p>}
                {session.result.caveat && <p className="notice warn">{session.result.caveat}</p>}
                {session.result.norm_note && <p className="muted">{session.result.norm_note}</p>}
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit();
                }}
              >
                <div className="muted" style={{ marginBottom: '0.8rem' }}>
                  已答 {done} / {session.missing.length}。题目标题为合成模板占位，不计入结论。
                </div>
                {session.missing.map((id) => (
                  <fieldset key={id} style={{ border: '1px solid var(--border)', borderRadius: 8, marginBottom: '0.7rem', padding: '0.6rem' }}>
                    <legend className="muted">题目 {id}</legend>
                    <label htmlFor={id} style={{ color: 'var(--text)', fontSize: '0.95rem' }}>
                      请评分（1=非常不同意 … 5=非常同意）
                    </label>
                    <select
                      id={id}
                      value={answers[id] ?? ''}
                      onChange={(e) =>
                        setAnswers((a) => ({ ...a, [id]: e.target.value === '' ? 0 : Number(e.target.value) }))
                      }
                      style={{ marginTop: '0.4rem' }}
                    >
                      <option value="">未作答</option>
                      {SCALE.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  </fieldset>
                ))}
                <button className="primary" type="submit" disabled={submitting}>
                  {submitting ? '提交计分中…' : '提交（服务端计分）'}
                </button>
                {remaining.length > 0 && (
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
