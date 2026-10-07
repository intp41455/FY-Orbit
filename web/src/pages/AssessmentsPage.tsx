import { useEffect, useState } from 'react';
import {
  assessmentsApi,
  type QuestionnaireInfo,
  type RealAssessmentSession,
} from '../api/assessments';
import { errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { EmptyState } from '../components/chatui/EmptyState';
import { Skeleton } from '../components/chatui/Skeleton';
import { StatusTag } from '../components/chatui/StatusTag';
import '../styles/pages/chat.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

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

  return (
    <BaseBound surface="assessments">
      <div className="assess-shell">
        <div className="page-head">
          <h2>测评</h2>
          {session ? (
            <button className="ui-btn ui-btn--sm" onClick={() => setSession(null)}>
              <LineIcon name="chevronLeft" size={16} /> 返回目录
            </button>
          ) : (
            <span className="ui-hint">计分由服务端完成，结果绑定问卷版本；留空不会被默认填答。</span>
          )}
        </div>

        {/* 唯一 role="alert"：漏答/失败等阻断信息 */}
        {error ? <div className="notice danger" role="alert">{error}</div> : null}

        {!session && loading ? (
          <Skeleton rows={3} variant="card" label="正在加载量表目录" />
        ) : null}

        {!session && !loading && catalog && catalog.length === 0 ? (
          <EmptyState
            icon="assessments"
            title="还没有可用量表"
            hint="后端没有返回任何量表。量表由服务端目录提供，本页不内置任何题目。"
          />
        ) : null}

        {!session && catalog && catalog.length > 0 ? (
          <div className="assess-catalog">
            {catalog.map((q) => (
              <div className="card assess-card" key={q.id}>
                <div className="assess-card-head">
                  <strong>{q.title}</strong>
                  <span className="ui-badge">v{q.version}</span>
                  {q.synthetic ? <StatusTag kind="waiting" text="合成题占位" /> : null}
                </div>
                {/* 来源说明必须原样可见（Synthetic 等），不隐藏 */}
                <p className="assess-card-note">{q.source_note}</p>
                <div className="ui-hint">题目数：{q.item_count}</div>
                <div className="assess-card-foot">
                  <button className="ui-btn ui-btn--primary" onClick={() => void start(q)}>
                    <LineIcon name="play" size={16} /> 开始测评
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {session ? (
          <div className="assess-run">
            <div className="card assess-run-card">
              <div className="assess-card-head">
                <h3 className="ui-panel-title">问卷 v{session.questionnaire_version}</h3>
                <span className="ui-badge">item_set {session.item_set_hash.slice(0, 12)}</span>
              </div>
              <p className="ui-hint">
                计分由服务端完成，结果绑定问卷版本 v{session.questionnaire_version}。
                反向题与合成分由服务端按版本处理；留空不会被默认填答。
              </p>
              <p className="assess-card-note">{session.questionnaire_id}</p>

              {session.result ? (
                <div className="assess-result">
                  <div className="notice info">
                    计分由服务端完成；以下结果绑定问卷版本 v{session.questionnaire_version}。
                  </div>
                  {session.result.type_label ? <h3>结果：{session.result.type_label}</h3> : null}
                  {session.result.official_mbti === false ? (
                    <StatusTag kind="waiting" text="非官方探索倾向（非 MBTI® 认证）" />
                  ) : null}
                  <div className="assess-result-grid">
                    {Object.entries(session.result.scales ?? {}).map(([k, v]) => (
                      <div className="card assess-scale" key={k}>
                        <div className="ui-hint">{k}</div>
                        <div className="assess-scale-value">{v}</div>
                      </div>
                    ))}
                  </div>
                  {session.result.interpretation ? <p>{session.result.interpretation}</p> : null}
                  {session.result.caveat ? <p className="notice warn">{session.result.caveat}</p> : null}
                  {session.result.norm_note ? <p className="ui-hint">{session.result.norm_note}</p> : null}
                </div>
              ) : (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void submit();
                  }}
                >
                  <p className="ui-hint">
                    已答 {Object.keys(answers).length} / {session.missing.length}。
                    题目标题为合成模板占位，不计入结论。
                  </p>
                  {session.missing.map((id) => (
                    <div className="assess-item" key={id}>
                      <label className="assess-item-label" htmlFor={id}>
                        请评分（1=非常不同意 … 5=非常同意）
                      </label>
                      <span className="ui-hint">题目 {id}</span>
                      <select
                        id={id}
                        className="ui-select assess-select"
                        aria-label={`请评分（题目 ${id}）`}
                        value={answers[id] ?? ''}
                        onChange={(e) =>
                          setAnswers((a) => ({ ...a, [id]: e.target.value === '' ? 0 : Number(e.target.value) }))
                        }
                      >
                        <option value="">未作答</option>
                        {SCALE.map((v) => (
                          <option key={v} value={v}>{v}</option>
                        ))}
                      </select>
                    </div>
                  ))}
                  <div className="assess-card-foot">
                    <button className="ui-btn ui-btn--primary" type="submit" disabled={submitting}>
                      {submitting ? '提交计分中…' : '提交（服务端计分）'}
                    </button>
                  </div>
                  {session.missing.filter((id) => answers[id] === undefined).length > 0 ? (
                    <p className="ui-hint">
                      提示：仍有漏答；提交会被拒绝并要求补全，不会产生默认结果。
                    </p>
                  ) : null}
                </form>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </BaseBound>
  );
}
