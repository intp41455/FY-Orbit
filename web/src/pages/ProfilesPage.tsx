import { useCallback, useEffect, useState } from 'react';
import {
  profilesApi,
  type ProfileCluster,
  type ProfileClusterNode,
  type ProfileEdge,
  type ProfileImport,
  type ProfileMetric,
  type ProfileRevision,
  type ProfileSubject,
} from '../api/profiles';
import { errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import '../styles/pages/cabin.css';
import { SubjectRail } from '../components/cabinni/SubjectRail';
import {
  DimensionBars,
  EvidenceCapabilityNote,
  EvidenceChain,
  reviewMeta,
} from '../components/cabinni/ProfileDimensions';
import { RelationGraph } from '../components/cabinni/RelationGraph';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

/**
 * 04 多维画像。
 *
 * 视觉层职责（包 D 任务书 §6）：
 *   左：对象列表（整块可点 + hover 浮现次要操作）
 *   中：语料导入与说话人切片（保留原 id 与按钮文案，e2e 依赖）
 *   右：维度条形图 + 特征群关系网 + 证据链（点维度即展开证据链，不跳页）
 *
 * 诚实性红线（承接原页面注释）：
 *   画像只反映导入语料的语言特征与结构推演，**不是**临床/心理诊断。
 */
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

  // Speaker confirmation
  const [speakerMappings, setSpeakerMappings] = useState<Record<string, string>>({});
  const [confirmingSpeakers, setConfirmingSpeakers] = useState(false);
  const [speakerConfirmMsg, setSpeakerConfirmMsg] = useState<string | null>(null);

  // Graph vs List & selected node
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const [selectedNode, setSelectedNode] = useState<ProfileClusterNode | null>(null);
  /** 「定位到切片」高亮的目标证据 id（最少点击守则第 4 条：不跳页，原地展开）。 */
  const [locatedEvidence, setLocatedEvidence] = useState<string | null>(null);

  const loadSubjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await profilesApi.listSubjects();
      setSubjects(res.items);
      if (res.items.length > 0 && !activeSubject) {
        void selectSubject(res.items[0]);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
    // activeSubject 故意不进依赖：它由本函数写入，进依赖会造成自我循环。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadSubjects();
  }, [loadSubjects]);

  async function selectSubject(subj: ProfileSubject) {
    setActiveSubject(subj);
    setSelectedNode(null);
    setLocatedEvidence(null);
    try {
      const revRes = await profilesApi.listRevisions(subj.id);
      setRevisions(revRes.items);
      if (revRes.items.length > 0) {
        setActiveRevision(revRes.items[0]);
        setSelectedNode(revRes.items[0].clusters?.[0]?.nodes?.[0] ?? null);
      } else {
        setActiveRevision(null);
        setSelectedNode(null);
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
      void selectSubject(subj);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleImportDocument(e: React.FormEvent) {
    e.preventDefault();
    if (!importContent.trim() || !activeSubject) return;
    setImporting(true);
    setError(null);
    setSpeakerConfirmMsg(null);
    try {
      const imp = await profilesApi.importDocument({
        content: importContent,
        filename: importFilename,
        subject_id: activeSubject.id,
        privacy_domain: importDomain,
      });
      setLastImport(imp);
      setImportContent('');
      const initialMap: Record<string, string> = {};
      imp.subject_candidates?.forEach((c) => {
        initialMap[c.speaker] = c.candidate_subject || (activeSubject.kind === 'self' ? 'self' : activeSubject.id);
      });
      setSpeakerMappings(initialMap);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setImporting(false);
    }
  }

  async function handleConfirmSpeakers() {
    if (!lastImport) return;
    setConfirmingSpeakers(true);
    setError(null);
    try {
      const res = await profilesApi.confirmSpeakers(lastImport.id, speakerMappings);
      setSpeakerConfirmMsg(`已确认 ${res.updated_segments} 个发言人切片归属，重新运行推演将准确隔离本人与第三方陈述。`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setConfirmingSpeakers(false);
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
      setSelectedNode(rev.clusters?.[0]?.nodes?.[0] ?? null);
      setLocatedEvidence(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleEvidenceFeedback(
    evidenceId: string,
    action: 'accept' | 'edit' | 'reject' | 'uncertain',
  ) {
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
            n.evidence_refs.includes(evidenceId) ? { ...n, review_status: newStatus } : n,
          ),
        }));
        setActiveRevision({ ...activeRevision, clusters: updatedClusters });
        if (selectedNode && selectedNode.evidence_refs.includes(evidenceId)) {
          setSelectedNode({ ...selectedNode, review_status: newStatus });
        }
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const allNodes: ProfileClusterNode[] = activeRevision?.clusters?.flatMap((c) => c.nodes) ?? [];
  const edges: ProfileEdge[] = activeRevision?.edges ?? [];
  const metrics: ProfileMetric[] = activeRevision?.metrics ?? [];

  return (
    <BaseBound surface="profiles">
      <div className="cabin-ni-profiles">
        <header className="page-header">
          <div>
            <h2>个人与对象多维画像 (04 Multi-Dimensional Profile)</h2>
            <p className="subtext">
              支持本人与研究/工作对象画像构建；基于原始语料切片与事实链推演，严格区分语料统计与主观自述，杜绝任何臆想与伪心理诊断。
            </p>
          </div>
          <div style={{ display: 'flex', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
            <button className="ui-btn" onClick={() => setShowNewSubject(!showNewSubject)}>
              <LineIcon name={showNewSubject ? 'close' : 'plus'} size={16} />
              {showNewSubject ? '取消新建' : '+ 新建档案对象'}
            </button>
          </div>
        </header>

        {error && (
          <div className="banner banner-error" role="alert">
            <LineIcon name="alert" size={16} /> {error}
          </div>
        )}

        {/* 新建对象（表单而非弹窗：窄屏不截断，也不遮挡星图式主视图） */}
        {showNewSubject && (
          <form className="cabin-ni-psubjects ui-panel ui-panel--pad" onSubmit={handleCreateSubject}>
            <h3 className="ui-panel-title">新建画像主体</h3>
            <div className="form-group">
              <label className="ui-label" htmlFor="new-label">
                名称 / 标签
              </label>
              <input
                id="new-label"
                className="ui-input"
                type="text"
                required
                placeholder="例如：自我认知、Project Falcon、协作对象"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="ui-label" htmlFor="new-kind">
                类别
              </label>
              <select
                id="new-kind"
                className="ui-select"
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
              <label className="ui-label" htmlFor="new-desc">
                描述
              </label>
              <input
                id="new-desc"
                className="ui-input"
                type="text"
                placeholder="画像背景与关注维度"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
              />
            </div>
            <button type="submit" className="ui-btn ui-btn--primary">
              确认创建
            </button>
          </form>
        )}

        {activeSubject ? (
          <div className="cabin-ni-profiles" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 15rem) minmax(0, 1fr)', gap: 'var(--ui-s-4)', alignItems: 'start' }}>
            {/* ---------------- 左：对象列表 ---------------- */}
            <aside className="ui-panel ui-panel--pad" aria-label="画像对象列表">
              <div className="ui-panel-hd" style={{ padding: 0, borderBottom: 'none' }}>
                <LineIcon name="profiles" size={18} />
                <span className="ui-panel-title">对象（{subjects.length}）</span>
              </div>
              <SubjectRail
                subjects={subjects}
                activeId={activeSubject.id}
                onSelect={(s) => void selectSubject(s)}
              />
            </aside>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-4)', minWidth: 0 }}>
              {/* ---------------- 中：语料导入 ---------------- */}
              <section className="card ui-panel ui-panel--pad">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
                  <h3 className="ui-panel-title">语料导入与说话人切片</h3>
                  <span className="ui-badge ui-badge--neutral">对象: {activeSubject.label}</span>
                </div>
                <p className="ui-hint">
                  粘贴对话文本、反思日记或会议记录。系统按说话人前缀自动切片，并建立可追溯证据链。
                </p>

                <form onSubmit={handleImportDocument}>
                  <div className="form-group">
                    <label className="ui-label" htmlFor="doc-content">
                      文本内容
                    </label>
                    <textarea
                      id="doc-content"
                      className="ui-textarea"
                      rows={6}
                      required
                      placeholder={'Alice: 我更重视不可变的审计与模块化架构设计。\nBob: 这样能显著提升抗风险能力。'}
                      value={importContent}
                      onChange={(e) => setImportContent(e.target.value)}
                    />
                  </div>

                  <div style={{ display: 'flex', gap: 'var(--ui-s-4)', marginBottom: 'var(--ui-s-3)', flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ flex: '1 1 12rem', minWidth: 0 }}>
                      <label className="ui-label" htmlFor="doc-filename">
                        文件标识
                      </label>
                      <input
                        id="doc-filename"
                        className="ui-input"
                        type="text"
                        value={importFilename}
                        onChange={(e) => setImportFilename(e.target.value)}
                      />
                    </div>
                    <div className="form-group" style={{ flex: '0 0 10rem' }}>
                      <label className="ui-label" htmlFor="doc-domain">
                        数据隔离域
                      </label>
                      <select
                        id="doc-domain"
                        className="ui-select"
                        value={importDomain}
                        onChange={(e) => setImportDomain(e.target.value as 'personal' | 'work')}
                      >
                        <option value="personal">私人 (Personal)</option>
                        <option value="work">工作 (Work)</option>
                      </select>
                    </div>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
                    <button type="submit" className="ui-btn" disabled={importing}>
                      <LineIcon name="upload" size={16} />
                      {importing ? '解析切片中…' : '提交语料切片'}
                    </button>
                    <button type="button" className="ui-btn ui-btn--primary" onClick={() => void handleRunProfiling()} disabled={loading}>
                      <LineIcon name="sparkles" size={16} />
                      {loading ? '画像推演中…' : '启动画像综合推演 (Run)'}
                    </button>
                  </div>
                </form>

                {lastImport && (
                  <div className="cabin-ni-evi" data-testid="profile-last-import" style={{ marginTop: 'var(--ui-s-3)' }}>
                    <div style={{ fontWeight: 600, color: 'var(--ui-ink-1)' }}>最近导入切片分析</div>
                    <div className="ui-hint">
                      ID: {lastImport.id} | 大小: {lastImport.size} 字节 | 状态: {lastImport.status}
                    </div>

                    {speakerConfirmMsg && (
                      <div className="ui-badge ui-badge--complete" role="status">
                        <LineIcon name="check" size={14} />
                        {speakerConfirmMsg}
                      </div>
                    )}

                    {lastImport.subject_candidates?.length > 0 && (
                      <div style={{ marginTop: 'var(--ui-s-2)' }}>
                        <div style={{ fontWeight: 600, marginBottom: 'var(--ui-s-2)' }}>
                          检测到的发言人切片归属映射：
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-2)' }}>
                          {lastImport.subject_candidates.map((c, i) => (
                            <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
                              <span>
                                发言人 <code>{c.speaker}</code> ({c.segment_count} 段)
                              </span>
                              <select
                                className="ui-select"
                                style={{ width: 'auto', minHeight: 34 }}
                                aria-label={`发言人 ${c.speaker} 归属映射`}
                                value={speakerMappings[c.speaker] || 'third_party'}
                                onChange={(e) =>
                                  setSpeakerMappings({ ...speakerMappings, [c.speaker]: e.target.value })
                                }
                              >
                                <option value="self">映射为本人 (Self)</option>
                                <option value={activeSubject.id}>映射为当前主体 ({activeSubject.label})</option>
                                <option value="third_party">第三方陈述 (仅供参考)</option>
                              </select>
                            </div>
                          ))}
                        </div>
                        <button
                          type="button"
                          className="ui-btn ui-btn--block"
                          style={{ marginTop: 'var(--ui-s-2)' }}
                          onClick={() => void handleConfirmSpeakers()}
                          disabled={confirmingSpeakers}
                        >
                          <LineIcon name="check" size={16} />
                          {confirmingSpeakers ? '保存中…' : '确认发言人归属并生效'}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </section>

              {/* 伦理边界：用等待（琥珀）+ 图标 + 文字表达，颜色不是唯一通道 */}
              <section className="cabin-ni-evi" style={{ borderLeft: '4px solid var(--ui-st-waiting)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-2)', fontWeight: 700, color: 'var(--ui-st-waiting)' }}>
                  <LineIcon name="alert" size={16} />
                  画像边界与伦理限制规范
                </div>
                <ul style={{ margin: 'var(--ui-s-2) 0 0', paddingLeft: '1.2rem', fontSize: 'var(--ui-fs-sm)', color: 'var(--ui-ink-2)' }}>
                  <li>
                    <strong>非医疗/非心理诊断：</strong>画像结果仅反映输入文本的语言特征与结构推演，严格禁止下达任何临床诊断。
                  </li>
                  <li>
                    <strong>语料样本有限性：</strong>未导入的事实不作为定性依据，避免过度泛化。
                  </li>
                  <li>
                    <strong>事实与自述隔离：</strong>文本统计证据与用户主观自述严格分列，并在图谱与特征群中独立标记。
                  </li>
                </ul>
              </section>

              {/* ---------------- 右：画像透视 ---------------- */}
              {activeRevision ? (
                <section className="card ui-panel ui-panel--pad">
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      gap: 'var(--ui-s-3)',
                      marginBottom: 'var(--ui-s-3)',
                      flexWrap: 'wrap',
                    }}
                  >
                    <div>
                      <h3 className="ui-panel-title">{activeRevision.core_summary?.title || '多维画像透视'}</h3>
                      <small className="ui-hint">
                        版本 Rev {activeRevision.revision} · 审核状态: {activeRevision.user_review_state} · 生成于{' '}
                        {new Date(activeRevision.created_at).toLocaleString('zh-CN')}
                      </small>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
                      {revisions.length > 1 && (
                        <select
                          className="ui-select"
                          style={{ width: 'auto', minHeight: 34 }}
                          aria-label="选择画像版本"
                          value={activeRevision.id}
                          onChange={(e) => {
                            const r = revisions.find((x) => x.id === e.target.value);
                            if (r) {
                              setActiveRevision(r);
                              setSelectedNode(r.clusters?.[0]?.nodes?.[0] ?? null);
                              setLocatedEvidence(null);
                            }
                          }}
                        >
                          {revisions.map((r) => (
                            <option key={r.id} value={r.id}>
                              Rev {r.revision} ({r.user_review_state})
                            </option>
                          ))}
                        </select>
                      )}
                      <span className="ui-badge ui-badge--neutral">Schema 1.0</span>
                    </div>
                  </div>

                  {/* 常模说明 */}
                  <div className="cabin-ni-evi" style={{ marginBottom: 'var(--ui-s-3)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
                      <strong style={{ color: 'var(--ui-ink-1)' }}>客观语料统计指标说明</strong>
                      <span className="ui-badge ui-badge--external">
                        <LineIcon name="info" size={14} />
                        {activeRevision.core_summary?.formal_norm
                          ? '标准化常模'
                          : '非标准化常模 (Formal Norm: False)'}
                      </span>
                    </div>
                    <p style={{ margin: 'var(--ui-s-2) 0 0', fontSize: 'var(--ui-fs-sm)', color: 'var(--ui-ink-2)' }}>
                      {activeRevision.core_summary?.norm_note ||
                        '未接入标准化心理量表授权输入，不呈现推测性能力分或人格测评常模分；以下呈现指标为可重算语料客观统计与自述提取。'}
                    </p>
                  </div>

                  <p className="cabin-ni-evi" style={{ fontSize: 'var(--ui-fs-md)', color: 'var(--ui-ink-1)' }}>
                    {activeRevision.core_summary?.summary}
                  </p>

                  {/* 维度条形图 */}
                  <div style={{ marginTop: 'var(--ui-s-5)' }}>
                    <h4 className="ui-panel-title">客观量化推演维度 (Deterministic Metrics)</h4>
                    <p className="ui-hint">
                      档位用九档状态色表达强弱（最强=深蓝完成，最弱=紫灰外部未知），并同时给出档位文字与图标——
                      颜色不是唯一信息通道。
                    </p>
                    <DimensionBars metrics={metrics} />
                  </div>

                  {/* 图 / 列表切换 */}
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: 'var(--ui-s-2)',
                      marginTop: 'var(--ui-s-5)',
                      marginBottom: 'var(--ui-s-3)',
                      flexWrap: 'wrap',
                    }}
                  >
                    <h4 className="ui-panel-title" style={{ margin: 0 }}>
                      特征群与推演关系网 (Clusters & Relation Graph)
                    </h4>
                    {/* 用 aria-pressed 的切换组，而不是 role=tab —— 后者会把隐式 button 角色
                        覆盖成 tab，破坏「按按钮名查找」的可访问性契约。 */}
                    <div className="ui-tabs" role="group" aria-label="关系网视图">
                      <button
                        type="button"
                        className="ui-tab"
                        aria-pressed={viewMode === 'graph'}
                        onClick={() => setViewMode('graph')}
                      >
                        <LineIcon name="network" size={16} />
                        二维关系图
                      </button>
                      <button
                        type="button"
                        className="ui-tab"
                        aria-pressed={viewMode === 'list'}
                        onClick={() => setViewMode('list')}
                      >
                        <LineIcon name="list" size={16} />
                        详细列表
                      </button>
                    </div>
                  </div>

                  {viewMode === 'graph' ? (
                    <div>
                      <RelationGraph
                        nodes={allNodes}
                        edges={edges}
                        selectedId={selectedNode?.id ?? null}
                        onSelect={(n) => {
                          setSelectedNode(n);
                          setLocatedEvidence(null);
                        }}
                      />

                      {/* 选中节点的证据链：原地展开，不跳页 */}
                      {selectedNode && (
                        <div className="ui-panel ui-panel--pad" style={{ marginTop: 'var(--ui-s-3)' }} data-testid="profile-evidence-panel">
                          <EvidenceChain
                            node={selectedNode}
                            locatedId={locatedEvidence}
                            onLocate={setLocatedEvidence}
                            onFeedback={(id, action) => void handleEvidenceFeedback(id, action)}
                          />
                          <EvidenceCapabilityNote />
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-3)' }}>
                      {activeRevision.clusters?.map((c: ProfileCluster) => (
                        <div key={c.id} className="cabin-ni-evi">
                          <div style={{ fontWeight: 600, color: 'var(--ui-ink-1)' }}>{c.name}</div>
                          <div className="ui-hint" style={{ marginBottom: 'var(--ui-s-2)' }}>{c.summary}</div>

                          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ui-s-2)' }}>
                            {c.nodes?.map((node) => {
                              const meta = reviewMeta(node.review_status);
                              return (
                                <div
                                  key={node.id}
                                  className="cabin-ni-evi"
                                  style={{ borderLeft: `3px solid var(--ui-st-${meta.tone})` }}
                                >
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--ui-s-2)', flexWrap: 'wrap' }}>
                                    <strong>{node.label}</strong>
                                    <span className={`ui-badge ui-badge--${meta.tone}`}>
                                      状态: {node.review_status} · {meta.label}
                                    </span>
                                  </div>
                                  <div style={{ fontSize: 'var(--ui-fs-sm)', color: 'var(--ui-ink-2)', margin: 'var(--ui-s-1) 0' }}>
                                    {node.description}
                                  </div>
                                  <div className="ui-hint">
                                    定位: <code>{node.locator || '未定位'}</code> (发言人: {node.speaker || '自述'})
                                  </div>
                                  <div
                                    style={{
                                      display: 'flex',
                                      justifyContent: 'space-between',
                                      alignItems: 'center',
                                      gap: 'var(--ui-s-2)',
                                      marginTop: 'var(--ui-s-1)',
                                      flexWrap: 'wrap',
                                    }}
                                  >
                                    <small style={{ color: 'var(--ui-ink-4)' }}>
                                      置信度: {(node.confidence * 100).toFixed(0)}% · 来源: {node.claim_kind}
                                    </small>
                                    <div style={{ display: 'flex', gap: 'var(--ui-s-1)', flexWrap: 'wrap' }}>
                                      <button
                                        type="button"
                                        className="ui-btn ui-btn--sm"
                                        onClick={() => void handleEvidenceFeedback(node.evidence_refs[0] || node.id, 'accept')}
                                      >
                                        确认
                                      </button>
                                      <button
                                        type="button"
                                        className="ui-btn ui-btn--sm"
                                        onClick={() => void handleEvidenceFeedback(node.evidence_refs[0] || node.id, 'reject')}
                                      >
                                        否定
                                      </button>
                                      <button
                                        type="button"
                                        className="ui-btn ui-btn--sm"
                                        onClick={() => void handleEvidenceFeedback(node.evidence_refs[0] || node.id, 'uncertain')}
                                      >
                                        待验证
                                      </button>
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              ) : (
                <section className="card ui-panel ui-panel--pad" style={{ textAlign: 'center' }}>
                  <p className="ui-panel-sub">当前主体尚无画像版本。</p>
                  <p className="ui-hint">导入文本语料切片后，点「启动画像综合推演 (Run)」生成首个 Revision。</p>
                </section>
              )}
            </div>
          </div>
        ) : (
          /* 三态而不是「非空即渲染」。旧写法只认 loading 与有主体两态：
             后端返回空数组时 activeSubject 恒为 null，页面永远停在
             「正在加载档案主体…」，看上去像卡死。空态必须给出可点的下一步，
             否则用户既看不到原因也走不出去。 */
          loading ? (
            <div className="card ui-panel ui-panel--pad" style={{ textAlign: 'center' }} role="status">
              正在加载档案主体…
            </div>
          ) : subjects.length === 0 ? (
            <section
              className="card ui-panel ui-panel--pad cabin-ni-profiles-empty"
              data-testid="profiles-empty"
              aria-labelledby="profiles-empty-title"
            >
              <h3 className="ui-panel-title" id="profiles-empty-title">
                还没有画像对象
              </h3>
              <p className="ui-hint">
                画像必须挂在一个对象上才能建立。先新建一个档案对象（可选「本人档案」，
                也可以是研究或工作对象），再往里导入对话文本、反思日记或会议记录，
                系统才会按说话人切片并建立可追溯的证据链。
              </p>
              <ol className="cabin-ni-profiles-empty-steps">
                <li>新建档案对象，填写名称与类别（类别决定它在本页的语义）。</li>
                <li>在「语料导入与说话人切片」粘贴文本并提交，确认说话人归属。</li>
                <li>在「画像版本」点「启动画像综合推演」，生成首个 Revision。</li>
              </ol>
              <div className="cabin-ni-profiles-empty-actions">
                <button
                  type="button"
                  className="ui-btn ui-btn--primary"
                  onClick={() => setShowNewSubject(true)}
                >
                  <LineIcon name="plus" size={16} />
                  新建第一个档案对象
                </button>
                <button
                  type="button"
                  className="ui-btn"
                  onClick={() => void loadSubjects()}
                >
                  <LineIcon name="refresh" size={16} />
                  重新加载对象列表
                </button>
              </div>
              {error && (
                <p className="ui-hint">
                  若你确信已经有对象，请点「重新加载对象列表」；仍失败说明后端读取失败，请看上方错误提示。
                </p>
              )}
            </section>
          ) : (
            /* 有对象但当前没选中：正常路径下 loadSubjects 会自动选第一个，
               走到这里说明选中动作被打断（切换瞬间的中间态）。给一个明确落点，
               不把用户晾在这里。 */
            <section
              className="card ui-panel ui-panel--pad"
              data-testid="profiles-no-active-subject"
              style={{ textAlign: 'center' }}
            >
              <p className="ui-panel-sub">共 {subjects.length} 个画像对象，尚未选中。</p>
              <p className="ui-hint">点下方按钮选中一个对象即可继续。</p>
              <button
                type="button"
                className="ui-btn ui-btn--primary"
                style={{ marginTop: 'var(--ui-s-2)' }}
                onClick={() => void selectSubject(subjects[0])}
              >
                选中「{subjects[0].label}」
              </button>
            </section>
          )
        )}
      </div>
    </BaseBound>
  );
}