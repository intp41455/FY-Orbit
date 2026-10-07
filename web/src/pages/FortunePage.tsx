import { useState } from 'react';
import {
  chartsApi,
  type DailyFortuneResult,
  type TarotDrawResult,
  type ChartResult,
  type SynastryResult,
} from '../api/charts';
import { LineIcon } from '../components/ui/LineIcon';
import { errorMessage } from '../components/ui';

type TabKey = 'fortune' | 'tarot' | 'chart' | 'synastry';

export function FortunePage() {
  const [activeTab, setActiveTab] = useState<TabKey>('fortune');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 1. Daily fortune state
  const [birthDate, setBirthDate] = useState('1995-08-18');
  const [fortuneData, setFortuneData] = useState<DailyFortuneResult | null>(null);

  // 2. Tarot state
  const [tarotSpread, setTarotSpread] = useState<'single' | 'three_cards'>('three_cards');
  const [tarotQuestion, setTarotQuestion] = useState('');
  const [tarotData, setTarotData] = useState<TarotDrawResult | null>(null);

  // 3. Chart state
  const [chartSystem, setChartSystem] = useState<'bazi' | 'western'>('bazi');
  const [chartDate, setChartDate] = useState('1995-08-18');
  const [chartTime, setChartTime] = useState('14:30');
  const [unknownTime, setUnknownTime] = useState(false);
  const [chartData, setChartData] = useState<ChartResult | null>(null);

  // 4. Synastry state
  const [synDateA, setSynDateA] = useState('1995-08-18');
  const [synDateB, setSynDateB] = useState('1998-11-22');
  const [synData, setSynData] = useState<SynastryResult | null>(null);

  // Handlers
  const handleFetchFortune = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await chartsApi.getDailyFortune({ birth_date: birthDate });
      setFortuneData(res);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const handleDrawTarot = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await chartsApi.drawTarot({
        spread: tarotSpread,
        question: tarotQuestion.trim() || undefined,
      });
      setTarotData(res);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const handleComputeChart = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await chartsApi.computeChart({
        birth_date: chartDate,
        birth_time: unknownTime ? undefined : chartTime,
        unknown_time: unknownTime,
        system: chartSystem,
      });
      setChartData(res);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const handleComputeSynastry = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await chartsApi.computeSynastry({
        chart_a: { birth_date: synDateA },
        chart_b: { birth_date: synDateB },
      });
      setSynData(res);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '24px 20px', color: 'var(--ui-ink-1, #f1f5f9)' }}>
      {/* Page Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24, borderBottom: '1px solid var(--ui-line-1, rgba(255,255,255,0.08))', paddingBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 24, fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
            <LineIcon name="sparkles" size={24} />
            星轨命理与每日签
          </h2>
          <p style={{ margin: '6px 0 0 0', fontSize: 13, color: 'var(--ui-ink-3, #94a3b8)' }}>
            零模型推理开销 · 确定性干支历算与五行生克 · 经典大阿卡那启引 · 联动数码小屋与桌宠
          </p>
        </div>
        <div style={{ display: 'flex', gap: 6, background: 'rgba(255,255,255,0.04)', padding: 4, borderRadius: 10 }}>
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            style={{ background: activeTab === 'fortune' ? 'var(--ui-accent-bg, #3b82f6)' : 'transparent', color: activeTab === 'fortune' ? '#fff' : 'inherit' }}
            onClick={() => setActiveTab('fortune')}
          >
            🌟 每日运势签
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            style={{ background: activeTab === 'tarot' ? 'var(--ui-accent-bg, #3b82f6)' : 'transparent', color: activeTab === 'tarot' ? '#fff' : 'inherit' }}
            onClick={() => setActiveTab('tarot')}
          >
            🃏 塔罗启引
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            style={{ background: activeTab === 'chart' ? 'var(--ui-accent-bg, #3b82f6)' : 'transparent', color: activeTab === 'chart' ? '#fff' : 'inherit' }}
            onClick={() => setActiveTab('chart')}
          >
            🌌 确定性排盘
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            style={{ background: activeTab === 'synastry' ? 'var(--ui-accent-bg, #3b82f6)' : 'transparent', color: activeTab === 'synastry' ? '#fff' : 'inherit' }}
            onClick={() => setActiveTab('synastry')}
          >
            💫 能量合盘
          </button>
        </div>
      </div>

      {error && (
        <div className="notice danger" role="alert" style={{ marginBottom: 16 }}>
          <LineIcon name="alert" size={16} />
          {error}
        </div>
      )}

      {/* TAB 1: 每日运势 */}
      {activeTab === 'fortune' && (
        <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 24 }}>
          {/* Form */}
          <div style={{ background: 'var(--ui-surface-2, rgba(255,255,255,0.03))', padding: 20, borderRadius: 14, border: '1px solid var(--ui-line-1, rgba(255,255,255,0.08))', height: 'fit-content' }}>
            <h3 style={{ fontSize: 16, margin: '0 0 16px 0' }}>个人生辰信息</h3>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 6, color: 'var(--ui-ink-3, #94a3b8)' }}>出生公历日期</label>
              <input
                type="date"
                className="ui-input"
                style={{ width: '100%' }}
                value={birthDate}
                onChange={(e) => setBirthDate(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="ui-btn ui-btn--primary"
              style={{ width: '100%' }}
              disabled={loading || !birthDate}
              onClick={handleFetchFortune}
            >
              {loading ? '正在推算干支…' : '查看今日运势与签文'}
            </button>
            <p style={{ fontSize: 11, color: 'var(--ui-ink-4, #64748b)', marginTop: 12, lineHeight: 1.5 }}>
              本算法严格依据天文干支历书与天干生克推导，零 LLM 幻觉，完全离线运行。
            </p>
          </div>

          {/* Results */}
          <div>
            {fortuneData ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{ background: 'linear-gradient(135deg, rgba(59,130,246,0.12) 0%, rgba(139,92,246,0.12) 100%)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: 14, padding: 24 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <div>
                      <span style={{ fontSize: 12, color: 'var(--ui-sky-400, #38bdf8)', fontWeight: 600 }}>{fortuneData.lunar_date}</span>
                      <h4 style={{ fontSize: 22, margin: '4px 0 0 0' }}>{fortuneData.relation}</h4>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <span style={{ fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>能量协调指数</span>
                      <div style={{ fontSize: 32, fontWeight: 800, color: '#38bdf8' }}>{fortuneData.luck_score} <span style={{ fontSize: 14 }}>/ 100</span></div>
                    </div>
                  </div>
                  <div style={{ fontSize: 15, lineHeight: 1.6, padding: '12px 16px', background: 'rgba(0,0,0,0.25)', borderRadius: 8, margin: '16px 0' }}>
                    "{fortuneData.oracle_message}"
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div style={{ background: 'rgba(34,197,94,0.08)', padding: 12, borderRadius: 8, border: '1px solid rgba(34,197,94,0.2)' }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: '#4ade80', marginBottom: 6 }}>今日宜</div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {fortuneData.favorable.map((item) => (
                          <span key={item} style={{ background: 'rgba(34,197,94,0.15)', fontSize: 12, padding: '2px 8px', borderRadius: 4 }}>{item}</span>
                        ))}
                      </div>
                    </div>
                    <div style={{ background: 'rgba(239,68,68,0.08)', padding: 12, borderRadius: 8, border: '1px solid rgba(239,68,68,0.2)' }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: '#f87171', marginBottom: 6 }}>今日忌</div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {fortuneData.unfavorable.map((item) => (
                          <span key={item} style={{ background: 'rgba(239,68,68,0.15)', fontSize: 12, padding: '2px 8px', borderRadius: 4 }}>{item}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 20, marginTop: 14, fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>
                    <span>🎨 幸运色：<b>{fortuneData.lucky_color}</b></span>
                    <span>🧭 贵人方位：<b>{fortuneData.lucky_direction}</b></span>
                  </div>
                </div>

                {/* Cabin Linkage */}
                {fortuneData.cabin_event && (
                  <div style={{ background: 'var(--ui-surface-2, rgba(255,255,255,0.03))', border: '1px dashed rgba(53,224,200,0.4)', borderRadius: 14, padding: 16, display: 'flex', gap: 14, alignItems: 'center' }}>
                    <div style={{ background: 'rgba(53,224,200,0.15)', padding: 10, borderRadius: 10, color: '#35e0c8' }}>
                      <LineIcon name="cabin" size={24} />
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: '#35e0c8' }}>数码小屋共振 · {fortuneData.cabin_event.event_name}</div>
                      <div style={{ fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)', marginTop: 2 }}>{fortuneData.cabin_event.bonus_desc}</div>
                    </div>
                    <div style={{ fontSize: 12, background: 'rgba(255,255,255,0.06)', padding: '4px 10px', borderRadius: 6 }}>
                      获得道具: <b>{fortuneData.cabin_event.reward}</b>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '60px 20px', background: 'var(--ui-surface-2, rgba(255,255,255,0.02))', borderRadius: 14, border: '1px dashed var(--ui-line-1, rgba(255,255,255,0.08))' }}>
                <LineIcon name="sparkles" size={36} />
                <p style={{ marginTop: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>输入生日后点击上方按钮，即可计算今日专属干支与心理启引。</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: 塔罗启引 */}
      {activeTab === 'tarot' && (
        <div>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 20 }}>
            <select
              className="ui-input"
              value={tarotSpread}
              onChange={(e) => setTarotSpread(e.target.value as any)}
            >
              <option value="single">每日单张启引签</option>
              <option value="three_cards">时间流三牌阵（过去 / 现在 / 未来）</option>
            </select>
            <input
              type="text"
              className="ui-input"
              style={{ flex: 1 }}
              placeholder="选填：心中的疑惑或关注的主题（如：近期工程重构方向、合作抉择…）"
              value={tarotQuestion}
              onChange={(e) => setTarotQuestion(e.target.value)}
            />
            <button
              type="button"
              className="ui-btn ui-btn--primary"
              disabled={loading}
              onClick={handleDrawTarot}
            >
              {loading ? '正在洗牌…' : '从星轨洗牌并抽取'}
            </button>
          </div>

          {tarotData ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${tarotData.cards.length}, 1fr)`, gap: 16 }}>
                {tarotData.cards.map((c) => (
                  <div
                    key={c.card_id}
                    style={{
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: 14,
                      padding: 18,
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 10,
                    }}
                  >
                    <div style={{ fontSize: 12, color: '#38bdf8', fontWeight: 600 }}>{c.position_label}</div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: '#fff' }}>{c.name}</div>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {c.keywords.map((k) => (
                        <span key={k} style={{ fontSize: 11, background: 'rgba(56,189,248,0.12)', color: '#38bdf8', padding: '1px 6px', borderRadius: 4 }}>
                          #{k}
                        </span>
                      ))}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--ui-ink-2, #cbd5e1)', lineHeight: 1.5 }}>{c.meaning}</div>
                    <div style={{ fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)', borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 8 }}>
                      💡 <b>行动指引：</b>{c.guidance}
                    </div>
                  </div>
                ))}
              </div>

              {/* Overall Reading & Cabin Interaction */}
              <div style={{ background: 'rgba(18,21,29,0.85)', padding: 18, borderRadius: 12, border: '1px solid rgba(255,255,255,0.08)' }}>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>综合牌意解读</div>
                <div style={{ fontSize: 13, color: 'var(--ui-ink-3, #94a3b8)', lineHeight: 1.6 }}>{tarotData.overall_reading}</div>
                {tarotData.cabin_interaction && (
                  <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed rgba(255,255,255,0.08)', fontSize: 12, color: '#35e0c8' }}>
                    🐾 <b>桌宠动态：</b>{tarotData.cabin_interaction.deskpet_action} {tarotData.cabin_interaction.cabin_buff}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '60px 20px', background: 'var(--ui-surface-2, rgba(255,255,255,0.02))', borderRadius: 14, border: '1px dashed var(--ui-line-1, rgba(255,255,255,0.08))' }}>
              <LineIcon name="sparkles" size={36} />
              <p style={{ marginTop: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>点击上方按钮抽取大阿卡那启引，为当下的思考提供多重视角。</p>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: 确定性排盘 */}
      {activeTab === 'chart' && (
        <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 24 }}>
          <div style={{ background: 'var(--ui-surface-2, rgba(255,255,255,0.03))', padding: 20, borderRadius: 14, border: '1px solid var(--ui-line-1, rgba(255,255,255,0.08))' }}>
            <h3 style={{ fontSize: 16, margin: '0 0 16px 0' }}>排盘参数</h3>
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4, color: 'var(--ui-ink-3, #94a3b8)' }}>体系</label>
              <select className="ui-input" style={{ width: '100%' }} value={chartSystem} onChange={(e) => setChartSystem(e.target.value as any)}>
                <option value="bazi">中国四柱八字（十神 · 藏干 · 五行）</option>
                <option value="western">西方现代占星（黄道十二宫 · 行星度数）</option>
              </select>
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4, color: 'var(--ui-ink-3, #94a3b8)' }}>出生日期</label>
              <input type="date" className="ui-input" style={{ width: '100%' }} value={chartDate} onChange={(e) => setChartDate(e.target.value)} />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4, color: 'var(--ui-ink-3, #94a3b8)' }}>出生时间</label>
              <input type="time" className="ui-input" style={{ width: '100%' }} disabled={unknownTime} value={chartTime} onChange={(e) => setChartTime(e.target.value)} />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
                <input type="checkbox" checked={unknownTime} onChange={(e) => setUnknownTime(e.target.checked)} />
                出生时辰未知（忠实保留缺项，不伪造时柱）
              </label>
            </div>
            <button type="button" className="ui-btn ui-btn--primary" style={{ width: '100%' }} disabled={loading} onClick={handleComputeChart}>
              {loading ? '计算天文度数中…' : '生成确定性盘面'}
            </button>
          </div>

          <div>
            {chartData ? (
              <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 20 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div>
                    <h4 style={{ fontSize: 18, margin: 0 }}>盘面计算结果 ({chartData.system.toUpperCase()})</h4>
                    <span style={{ fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>引擎版本: {chartData.engine_version} · Hash: {chartData.data_hash.slice(0, 12)}…</span>
                  </div>
                  <span style={{ fontSize: 11, background: 'rgba(34,197,94,0.15)', color: '#4ade80', padding: '3px 8px', borderRadius: 6 }}>
                    权威无幻觉
                  </span>
                </div>

                {chartData.system === 'bazi' && chartData.computed_data.pillars && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                    {['year', 'month', 'day', 'hour'].map((col) => {
                      const p = chartData.computed_data.pillars[col];
                      const labels: Record<string, string> = { year: '年柱', month: '月柱', day: '日柱', hour: '时柱' };
                      return (
                        <div key={col} style={{ textAlign: 'center', background: 'rgba(0,0,0,0.3)', padding: 12, borderRadius: 8 }}>
                          <div style={{ fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)', marginBottom: 4 }}>{labels[col]}</div>
                          <div style={{ fontSize: 22, fontWeight: 800, color: '#38bdf8' }}>
                            {p ? `${p.stem} ${p.branch}` : '未知'}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {chartData.computed_data.element_counts && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>五行能量分布</div>
                    <div style={{ display: 'flex', gap: 12 }}>
                      {Object.entries(chartData.computed_data.element_counts).map(([el, count]) => (
                        <div key={el} style={{ flex: 1, background: 'rgba(0,0,0,0.2)', padding: 8, borderRadius: 6, textAlign: 'center' }}>
                          <span style={{ fontSize: 12, fontWeight: 600 }}>{el}</span>: <b>{String(count)}</b>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {chartData.warnings.length > 0 && (
                  <div className="notice warn" style={{ fontSize: 12 }}>
                    {chartData.warnings.join('；')}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '60px 20px', background: 'var(--ui-surface-2, rgba(255,255,255,0.02))', borderRadius: 14, border: '1px dashed var(--ui-line-1, rgba(255,255,255,0.08))' }}>
                <LineIcon name="canvas" size={36} />
                <p style={{ marginTop: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>配置生辰参数后点击生成，获得严格的天文与四柱数据结构。</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 4: 能量合盘 */}
      {activeTab === 'synastry' && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 16, alignItems: 'flex-end', marginBottom: 20 }}>
            <div>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4, color: 'var(--ui-ink-3, #94a3b8)' }}>甲方生辰 (Person A)</label>
              <input type="date" className="ui-input" style={{ width: '100%' }} value={synDateA} onChange={(e) => setSynDateA(e.target.value)} />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4, color: 'var(--ui-ink-3, #94a3b8)' }}>乙方生辰 (Person B)</label>
              <input type="date" className="ui-input" style={{ width: '100%' }} value={synDateB} onChange={(e) => setSynDateB(e.target.value)} />
            </div>
            <button type="button" className="ui-btn ui-btn--primary" disabled={loading} onClick={handleComputeSynastry}>
              {loading ? '比对中…' : '计算五行契合度'}
            </button>
          </div>

          {synData ? (
            <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 24 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                  <h4 style={{ fontSize: 18, margin: 0 }}>双人协同契合度评估</h4>
                  <span style={{ fontSize: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>互补平衡分析 · 无封建迷信因果断言</span>
                </div>
                <div style={{ fontSize: 32, fontWeight: 800, color: '#38bdf8' }}>
                  {synData.compatibility_score} <span style={{ fontSize: 14 }}>分</span>
                </div>
              </div>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#4ade80', marginBottom: 6 }}>🌟 优势与协同点</div>
                {synData.synergy_points.map((p, i) => (
                  <div key={i} style={{ fontSize: 13, color: 'var(--ui-ink-2, #cbd5e1)', marginBottom: 4 }}>• {p}</div>
                ))}
              </div>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#f59e0b', marginBottom: 6 }}>⚠️ 潜在节奏冲突与磨合点</div>
                {synData.friction_points.map((p, i) => (
                  <div key={i} style={{ fontSize: 13, color: 'var(--ui-ink-2, #cbd5e1)', marginBottom: 4 }}>• {p}</div>
                ))}
              </div>
              <div style={{ background: 'rgba(0,0,0,0.25)', padding: 12, borderRadius: 8, fontSize: 13, color: '#38bdf8' }}>
                💡 <b>落地建议：</b>{synData.actionable_advice}
              </div>
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '60px 20px', background: 'var(--ui-surface-2, rgba(255,255,255,0.02))', borderRadius: 14, border: '1px dashed var(--ui-line-1, rgba(255,255,255,0.08))' }}>
              <LineIcon name="user" size={36} />
              <p style={{ marginTop: 12, color: 'var(--ui-ink-3, #94a3b8)' }}>输入双方出生日期，比对五行能量与协作风格。</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
