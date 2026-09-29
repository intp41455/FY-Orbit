import { useState } from 'react';
import { dataApi } from '../api/data';
import { useAsync, Spinner, errorMessage } from '../components/ui';

export function SettingsPage() {
  const { data, loading, error } = useAsync(() => dataApi.settings(), []);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  async function requestExport() {
    setExporting(true);
    setExportMsg(null);
    try {
      const res = await dataApi.export({ scope: 'all' });
      setExportMsg(`导出已请求（ID ${res.export_id}）。完成后通过短期鉴权链接下载；该链接不会被 Service Worker 缓存。`);
    } catch (e) {
      setExportMsg(errorMessage(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <div className="page-head"><h2>设置与数据</h2></div>
      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{error}</div>}
      {data && (
        <div className="card">
          <h3>系统配置状态</h3>
          <table>
            <tbody>
              <tr><td>模型供应商</td><td>{data.model_configured ? <span className="badge ok">已配置</span> : <span className="badge warn">未配置（付费调用关闭）</span>}</td></tr>
              <tr><td>OIDC 登录</td><td>{data.oidc_configured ? <span className="badge ok">已配置</span> : <span className="badge warn">未配置</span>}</td></tr>
              <tr><td>本地 dev-token</td><td>{data.local_dev_token_allowed ? <span className="badge warn">允许（仅 127.0.0.1）</span> : <span className="badge ok">已禁用</span>}</td></tr>
              <tr><td>数据域</td><td>{data.data_domains.join(', ') || '（无）'}</td></tr>
            </tbody>
          </table>
        </div>
      )}
      <div className="card">
        <h3 style={{ marginTop: 0 }}>导出与删除</h3>
        <p className="muted">导出为短期鉴权下载链接，不含系统密钥；私人原文不进入 Service Worker 缓存。</p>
        <button className="primary" onClick={() => void requestExport()} disabled={exporting}>
          {exporting ? '请求中…' : '请求导出我的数据'}
        </button>
        {exportMsg && <div className="notice info" style={{ marginTop: '0.6rem' }}>{exportMsg}</div>}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>隐私说明</h3>
        <p className="muted">
          Service Worker 仅预缓存静态外壳；API、私人聊天、导出/下载链接与令牌均不被缓存。离线时不可提交。登出会清理敏感客户端状态。
        </p>
      </div>
    </>
  );
}
