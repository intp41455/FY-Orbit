import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { legalApi, type LegalNotice } from '../api/legal';
import { errorMessage, Spinner } from '../components/ui';
import '../styles/pages/system.css';

/**
 * `/legal` · 开源授权声明（NEW-2 NOTICE 模块）。
 *
 * 公开页面（不套 Layout、不要求登录，登录页页脚可直达）。正文全部来自
 * 后端 `GET /api/legal/notice`，前端不维护第二份文本——NOTICE 改了这里
 * 自动跟着变，杜绝两份副本漂移。
 */
export function LegalNoticePage() {
  const [notice, setNotice] = useState<LegalNotice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    legalApi
      .notice()
      .then((n) => {
        if (alive) setNotice(n);
      })
      .catch((e) => {
        if (alive) setError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  if (error) {
    return (
      <div className="main">
        <h1>开源授权声明</h1>
        <div role="alert">加载声明失败：{error}</div>
        <p>
          <Link to="/login">返回登录</Link>
        </p>
      </div>
    );
  }
  if (!notice) return <Spinner label="正在加载授权声明…" />;

  return (
    <div className="main">
      <h1>开源授权声明</h1>
      <p data-testid="legal-license-line">
        {notice.license.id} · © {notice.license.holder} · {notice.license.change_rule}
      </p>
      <pre data-testid="legal-notice-text">{notice.notice_text}</pre>

      <h2>第三方开源组件</h2>
      <h3>后端（{notice.third_party.backend_source}）</h3>
      {notice.third_party.backend.length === 0 ? (
        <p>清单缺失：{notice.third_party.backend_source}</p>
      ) : (
        <ul data-testid="legal-backend-deps">
          {notice.third_party.backend.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      )}
      <h3>前端（{notice.third_party.frontend_source}）</h3>
      {notice.third_party.frontend.length === 0 ? (
        <p>清单缺失：{notice.third_party.frontend_source}</p>
      ) : (
        <ul data-testid="legal-frontend-deps">
          {notice.third_party.frontend.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      )}
      <p>
        <Link to="/login">返回登录</Link>
      </p>
    </div>
  );
}
