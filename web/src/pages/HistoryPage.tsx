import { conversationsApi } from '../api/conversations';
import { useAsync, Spinner } from '../components/ui';

export function HistoryPage() {
  const { data, loading, error } = useAsync(() => conversationsApi.list(), []);

  return (
    <>
      <div className="page-head"><h2>历史会话</h2></div>
      <p className="muted">完整原文由后端保存；此处只列出授权范围内的会话元数据。</p>
      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{error}</div>}
      {data && data.conversations.length === 0 && <div className="muted">暂无会话。</div>}
      {data && data.conversations.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr><th>标题</th><th>域</th><th>模式</th><th>更新时间</th></tr>
            </thead>
            <tbody>
              {data.conversations.map((c) => (
                <tr key={c.id}>
                  <td>{c.title}</td>
                  <td><span className="badge">{c.domain}</span></td>
                  <td>{c.mode}</td>
                  <td>{new Date(c.updated_at).toLocaleString('zh-CN')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
