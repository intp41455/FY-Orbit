import { useEffect, useState } from 'react';
import { profilesApi, type ProfileCluster, type ProfileImport, type ProfileMetric, type ProfileRevision, type ProfileSubject } from '../api/profiles';
import { errorMessage } from '../components/ui';

export function ProfilesPage() {
  const [subjects, setSubjects] = useState<ProfileSubject[]>([]);
  const [activeSubject, setActiveSubject] = useState<ProfileSubject | null>(null);
  const [revisions, setRevisions] = useState<ProfileRevision[]>([]);
  const [activeRevision, setActiveRevision] = useState<ProfileRevision | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // New subject form
  const [showNewSubject, setShowNewSubject] = useState(false);
  const [newLabel, setNewLabel] = useState('');
  const [newKind, setNewKind] = useState<string>('project');
  const [newDesc, setNewDesc] = useState('');

  // Import form
  const [importContent, setImportContent] = useState('');
  const [importFilename, setImportFilename] = useState('dialogue.txt');
  const [importDomain, setImportDomain] = useState<'personal' | 'work'>('personal');
  const [lastImport, setLastImport] = useState<ProfileImport | null>(null);
  const [importing, setImporting] = useState(false);

  // Load subjects
  useEffect(() => {
    loadSubjects();
  }, []);

  async function loadSubjects() {
    setLoading(true);
    setError(null);
    try {
      const res = await profilesApi.listSubjects();
      setSubjects(res.items);
      if (res.items.length > 0 && !activeSubject) {
        selectSubject(res.items[0]);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function selectSubject(subj: ProfileSubject) {
    setActiveSubject(subj);
    try {
      const revRes = await profilesApi.listRevisions(subj.id);
      setRevisions(revRes.items);
      if (revRes.items.length > 0) {
        setActiveRevision(revRes.items[0]);
      } else {
        setActiveRevision(null);
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleCreateSubject(e: React.FormEvent) {
    e.preventDefault();
    if (!newLabel.trim()) return;
    try {
      const subj = await profilesApi.createSubject({
        label: newLabel.trim(),
        kind: newKind,
        description: newDesc.trim(),
      });
      setSubjects((old) => [subj, ...old]);
      setShowNewSubject(false);
      setNewLabel('');
      setNewDesc('');
      selectSubject(subj);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleImportDocument(e: React.FormEvent) {
    e.preventDefault();
    if (!importContent.trim() || !activeSubject) return;
    setImporting(true);
    setError(null);
    try {
      const imp = await profilesApi.importDocument({
        content: importContent,
        filename: importFilename,
        subject_id: activeSubject.id,
        privacy_domain: importDomain,
      });
      setLastImport(imp);
      setImportContent('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setImporting(false);
    }
  }

  async function handleRunProfiling() {
    if (!activeSubject) return;
    setLoading(true);
    setError(null);
    try {
      const rev = await profilesApi.runProfiling(activeSubject.id);
      setRevisions((old) => [rev, ...old]);
      setActiveRevision(rev);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleEvidenceFeedback(evidenceId: string, action: 'accept' | 'edit' | 'reject' | 'uncertain') {
    try {
      await profilesApi.submitFeedback(evidenceId, { action });
      const statusMap: Record<string, 'accepted' | 'edited' | 'rejected' | 'uncertain'> = {
        accept: 'accepted',
        edit: 'edited',
        reject: 'rejected',
        uncertain: 'uncertain',
      };
      const newStatus = statusMap[action] ?? 'uncertain';
      if (activeRevision) {
        const updatedClusters = activeRevision.clusters.map((c) => ({
          ...c,
          nodes: c.nodes.map((n) =>
            n.evidence_refs.includes(evidenceId) ? { ...n, review_status: newStatus } : n
          ),
        }));
        setActiveRevision({ ...activeRevision, clusters: updatedClusters });
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <h2>个人与对象多维画像 (04 Multi-Dimensional Profile)</h2>
          <p className="subtext">
            支持本人与研究/工作对象画像构建；基于原始语料切片与事实链推演，严格区分语料统计与主观自述，杜绝任何臆想与伪心理诊断。
          </p>
        </div>
        <button
          className="btn btn-secondary"
          onClick={() => setShowNewSubject(!showNewSubject)}
        >
          {showNewSubject ? '取消新建' : '+ 新建档案对象'}
        </button>
      </header>

      {error && <div className="banner banner-error">{error}</div>}

      {/* New Subject Modal / Bar */}
      {showNewSubject && (
        <form className="card form-inline" onSubmit={handleCreateSubject} style={{ marginBottom: '1.5rem' }}>
          <h4>新建画像主体</h4>
          <div className="form-group">
            <label htmlFor="new-label">名称 / 标签</label>
            <input
              id="new-label"
              type="text"
              required
              placeholder="例如：自我认知、Project Falcon、协作对象"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label htmlFor="new-kind">类别</label>
            <select
              id="new-kind"
              value={newKind}
              onChange={(e) => setNewKind(e.target.value)}
            >
              <option value="self">本人档案 (Self)</option>
              <option value="project">项目对象 (Project)</option>
              <option value="person">外部人物 (Person)</option>
              <option value="topic">特定议题 (Topic)</option>
              <option value="work">工作组织 (Work)</option>
            </select>
          </div>
          <div className="form-group">
            <label htmlFor="new-desc">描述</label>
            <input
              id="new-desc"
              type="text"
              placeholder="画像背景与关注维度"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-primary">确认创建</button>
        </form>
      )}

      {/* Subject Tabs */}
      <div className="tabs-container" style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
        {subjects.map((s) => (
          <button
            key={s.id}
            className={`tab-btn ${activeSubject?.id === s.id ? 'active' : ''}`}
            onClick={() => selectSubject(s)}
          >
            <span className={`badge ${s.kind === 'self' ? 'badge-primary' : 'badge-neutral'}`}>
              {s.kind === 'self' ? '本人' : s.kind}
            </span>
            <strong>{s.label}</strong>
          </button>
        ))}
      </div>

      {activeSubject ? (
        <div className="grid grid-2" style={{ gap: '1.5rem' }}>
          {/* Left Column: Input and Ingestion */}
          <div>
            <div className="card" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h3>语料导入与说话人切片</h3>
                <span className="badge badge-neutral">对象: {activeSubject.label}</span>
              </div>
              <p className="subtext" style={{ fontSize: '0.85rem' }}>
                粘贴对话文本、反思日记或会议记录。系统将按换行与说话人前缀自动切片，并建立可追溯证据链。
              </p>

              <form onSubmit={handleImportDocument}>
                <div className="form-group">
                  <label htmlFor="doc-content">文本内容</label>
                  <textarea
                    id="doc-content"
                    rows={6}
                    required
                    placeholder="Alice: 我更重视不可变的审计与模块化架构设计。&#10;Bob: 这样能显著提升抗风险能力。"
                    value={importContent}
                    onChange={(e) => setImportContent(e.target.value)}
                  />
                </div>

                <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
                  <div className="form-group" style={{ flex: 1 }}>
                    <label htmlFor="doc-filename">文件标识</label>
                    <input
                      id="doc-filename"
                      type="text"
                      value={importFilename}
                      onChange={(e) => setImportFilename(e.target.value)}
                    />
                  </div>
                  <div className="form-group" style={{ width: '140px' }}>
                    <label htmlFor="doc-domain">数据隔离域</label>
                    <select
                      id="doc-domain"
                      value={importDomain}
                      onChange={(e) => setImportDomain(e.target.value as 'personal' | 'work')}
                    >
                      <option value="personal">私人 (Personal)</option>
                      <option value="work">工作 (Work)</option>
                    </select>
                  </div>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <button type="submit" className="btn btn-secondary" disabled={importing}>
                    {importing ? '解析切片中...' : '提交语料切片'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={handleRunProfiling}
                    disabled={loading}
                  >
                    {loading ? '画像推演中...' : '启动画像综合推演 (Run)'}
                  </button>
                </div>
              </form>

              {lastImport && (
                <div style={{ marginTop: '1rem', padding: '0.75rem', background: '#f8fafc', borderRadius: '4px', border: '1px solid #e2e8f0' }}>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#0f172a' }}>最近导入切片分析</div>
                  <div style={{ fontSize: '0.8rem', color: '#64748b' }}>
                    ID: {lastImport.id} | 大小: {lastImport.size} 字节 | 状态: {lastImport.status}
                  </div>
                  {lastImport.subject_candidates?.length > 0 && (
                    <div style={{ marginTop: '0.5rem', fontSize: '0.8rem' }}>
                      <strong>检测到的发言人切片：</strong>
                      <ul style={{ margin: '0.25rem 0', paddingLeft: '1.2rem' }}>
                        {lastImport.subject_candidates.map((c, i) => (
                          <li key={i}>
                            发言人 <code>{c.speaker}</code>: {c.segment_count} 个段落切片 (匹配候选: {c.candidate_subject})
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Limitations Notice */}
            <div className="card" style={{ borderLeft: '4px solid #f59e0b', background: '#fffbeb' }}>
              <h4 style={{ color: '#b45309', margin: '0 0 0.5rem 0' }}>画像边界与伦理限制规范</h4>
              <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.85rem', color: '#78350f' }}>
                <li><strong>非医疗/非心理诊断：</strong>画像结果仅反映输入文本的语言特征与结构推演，严格禁止下达任何临床诊断。</li>
                <li><strong>语料样本有限性：</strong>未导入的事实不作为定性依据，避免过度泛化。</li>
                <li><strong>事实与自述隔离：</strong>文本统计证据与用户主观自述严格分列，并在雷达图与特征群中独立标记。</li>
              </ul>
            </div>
          </div>

          {/* Right Column: Profile Revision Display */}
          <div>
            {activeRevision ? (
              <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <div>
                    <h3 style={{ margin: 0 }}>{activeRevision.core_summary?.title || '多维画像透视'}</h3>
                    <small className="subtext">
                      版本 Rev {activeRevision.revision} · 审核状态: {activeRevision.user_review_state} · 生成于 {new Date(activeRevision.created_at).toLocaleString()}
                    </small>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {revisions.length > 1 && (
                      <select
                        value={activeRevision.id}
                        onChange={(e) => {
                          const r = revisions.find((x) => x.id === e.target.value);
                          if (r) setActiveRevision(r);
                        }}
                        style={{ fontSize: '0.8rem', padding: '2px 6px' }}
                      >
                        {revisions.map((r) => (
                          <option key={r.id} value={r.id}>
                            Rev {r.revision} ({r.user_review_state})
                          </option>
                        ))}
                      </select>
                    )}
                    <span className="badge badge-ok">Schema 1.0</span>
                  </div>
                </div>

                <p style={{ fontSize: '0.9rem', color: '#334155', background: '#f1f5f9', padding: '0.75rem', borderRadius: '4px' }}>
                  {activeRevision.core_summary?.summary}
                </p>

                {/* Radar / Metrics Section */}
                <div style={{ marginTop: '1.5rem', marginBottom: '1.5rem' }}>
                  <h4>量化推演维度 (Corpus Stats vs Self Report)</h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
                    {activeRevision.metrics?.map((m: ProfileMetric, i: number) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.85rem' }}>
                        <span style={{ width: '140px', fontWeight: 500 }}>{m.dimension}</span>
                        <div style={{ flex: 1, height: '8px', background: '#e2e8f0', borderRadius: '4px', overflow: 'hidden' }}>
                          <div
                            style={{
                              width: `${m.score}%`,
                              height: '100%',
                              background: m.metric_type === 'corpus_stat' ? '#3b82f6' : '#10b981',
                            }}
                          />
                        </div>
                        <span style={{ width: '32px', textAlign: 'right', fontWeight: 600 }}>{m.score}</span>
                        <span className={`badge ${m.metric_type === 'corpus_stat' ? 'badge-primary' : 'badge-neutral'}`} style={{ fontSize: '0.75rem' }}>
                          {m.metric_type === 'corpus_stat' ? '语料统计' : '用户自述'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Clusters & Evidence Verification */}
                <div>
                  <h4>特征群与证据闭环 (Clusters & Evidence Feedback)</h4>
                  {activeRevision.clusters?.map((c: ProfileCluster) => (
                    <div key={c.id} style={{ marginBottom: '1.25rem', border: '1px solid #e2e8f0', borderRadius: '6px', padding: '0.75rem' }}>
                      <div style={{ fontWeight: 600, color: '#1e293b' }}>{c.name}</div>
                      <div style={{ fontSize: '0.8rem', color: '#64748b', marginBottom: '0.5rem' }}>{c.summary}</div>

                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {c.nodes?.map((node) => (
                          <div
                            key={node.id}
                            style={{
                              padding: '0.5rem',
                              background: '#f8fafc',
                              borderRadius: '4px',
                              borderLeft: `3px solid ${
                                node.review_status === 'accepted' ? '#10b981' : node.review_status === 'rejected' ? '#ef4444' : '#64748b'
                              }`,
                            }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <strong style={{ fontSize: '0.85rem' }}>{node.label}</strong>
                              <span className={`badge ${node.review_status === 'accepted' ? 'badge-ok' : 'badge-neutral'}`} style={{ fontSize: '0.7rem' }}>
                                状态: {node.review_status}
                              </span>
                            </div>
                            <div style={{ fontSize: '0.8rem', color: '#475569', margin: '0.25rem 0' }}>{node.description}</div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.25rem' }}>
                              <small style={{ color: '#94a3b8' }}>置信度: {(node.confidence * 100).toFixed(0)}% · 来源: {node.claim_kind}</small>
                              <div style={{ display: 'flex', gap: '0.25rem' }}>
                                <button
                                  type="button"
                                  className="btn btn-xs"
                                  style={{ padding: '2px 6px', fontSize: '0.7rem' }}
                                  onClick={() => handleEvidenceFeedback(node.evidence_refs[0], 'accept')}
                                >
                                  确认
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-xs"
                                  style={{ padding: '2px 6px', fontSize: '0.7rem' }}
                                  onClick={() => handleEvidenceFeedback(node.evidence_refs[0], 'reject')}
                                >
                                  否定
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-xs"
                                  style={{ padding: '2px 6px', fontSize: '0.7rem' }}
                                  onClick={() => handleEvidenceFeedback(node.evidence_refs[0], 'uncertain')}
                                >
                                  待验证
                                </button>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="card" style={{ textAlign: 'center', padding: '3rem 1rem', color: '#64748b' }}>
                <p>当前主体尚无画像版本。</p>
                <p style={{ fontSize: '0.85rem' }}>导入文本语料切片后，点击左侧「启动画像综合推演」以生成首个 Revision。</p>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          正在加载档案主体...
        </div>
      )}
    </div>
  );
}
