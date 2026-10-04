// 声明式适配器（manifest）导入：粘贴 YAML/JSON → 零代码注册一个连接。
// 失败必须把后端返回的真实原因显示出来（缺字段 / 格式错 / 名称占用）。
import { useState } from 'react';
import { hubApi } from '../../api/hub';
import { errorMessage } from '../ui';

export function ManifestImport({
  busy,
  notice,
  error,
  onImport,
}: {
  busy: boolean;
  notice: string;
  error: string;
  onImport: (text: string) => void;
}) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [loadingExample, setLoadingExample] = useState(false);
  const [exampleError, setExampleError] = useState('');

  // 示例由后端提供（manifest.py 的 EXAMPLE_MANIFEST），不在前端硬编码一份副本。
  const loadExample = async () => {
    setLoadingExample(true);
    setExampleError('');
    try {
      const ex = await hubApi.manifestExample();
      setText(JSON.stringify(ex.example, null, 2));
    } catch (e) {
      setExampleError(`取示例失败：${errorMessage(e)}`);
    } finally {
      setLoadingExample(false);
    }
  };

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} data-testid="hub-manifest-open">
        导入声明式适配器（manifest）
      </button>
    );
  }
  return (
    <section className="hub-manifest" data-testid="hub-manifest-panel">
      <h3>导入声明式适配器</h3>
      <p className="muted">
        粘贴 manifest（YAML 或 JSON）。必填凭证缺失时连接会停在「缺凭证」状态，补齐后才能启用——
        不会因为导入成功就假装能连。
      </p>
      <textarea
        value={text}
        rows={10}
        onChange={(e) => setText(e.target.value)}
        placeholder="apiVersion: hub/v1&#10;kind: http_webhook&#10;..."
        data-testid="hub-manifest-text"
      />
      <div className="hub-form-actions">
        <button
          type="button"
          disabled={busy || text.trim() === ''}
          onClick={() => onImport(text)}
          data-testid="hub-manifest-submit"
        >
          导入
        </button>
        <button type="button" onClick={() => void loadExample()} disabled={busy || loadingExample} data-testid="hub-manifest-example">
          {loadingExample ? '取示例中…' : '载入示例'}
        </button>
        <button type="button" onClick={() => setOpen(false)} disabled={busy}>
          收起
        </button>
      </div>
      {exampleError && <div className="notice danger" data-testid="hub-manifest-example-error">{exampleError}</div>}
      {error && <div className="notice danger" data-testid="hub-manifest-error">{error}</div>}
      {notice && <div className="notice" data-testid="hub-manifest-notice">{notice}</div>}
    </section>
  );
}
