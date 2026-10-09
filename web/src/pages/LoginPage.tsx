import { useRef, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Navigate } from 'react-router-dom';
import { LineIcon } from '../components/ui/LineIcon';
import { BRAND } from '../brand';
import '../styles/pages/system.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

/**
 * `/login` · 不套 Layout。
 *
 * 包 F 任务书 §3：
 *  - 背景是装饰星图（纯 CSS/SVG，≤120 节点量级），透明度 ≤ .35，不抢表单；
 *  - 表单用玻璃卡，Tab 顺序 = 口令 → 主按钮，回车直接提交；
 *  - ⛔ 不做假的 macOS 红黄绿窗口控制点（参考图有，产品是 Tauri Windows
 *    + 网页端，做出来是假按钮）。要做真标题栏走 Tauri drag region，
 *    纯网页端整条隐藏——本页直接不渲染。
 */

/** 装饰星图节点（静态坐标，仅用于背景呼吸感，不承载任何信息） */
const STARS: Array<{ x: number; y: number; r: number; kind?: 'alt' | 'v' }> = [
  { x: 8, y: 22, r: 3 }, { x: 16, y: 48, r: 2 }, { x: 24, y: 30, r: 4, kind: 'alt' },
  { x: 31, y: 66, r: 2 }, { x: 38, y: 18, r: 3 }, { x: 44, y: 52, r: 2.5, kind: 'v' },
  { x: 52, y: 34, r: 3.5 }, { x: 58, y: 74, r: 2 }, { x: 63, y: 12, r: 2.5 },
  { x: 70, y: 44, r: 3, kind: 'alt' }, { x: 77, y: 62, r: 2 }, { x: 84, y: 26, r: 3 },
  { x: 91, y: 54, r: 2.5, kind: 'v' }, { x: 12, y: 72, r: 2.5 }, { x: 68, y: 86, r: 2 },
  { x: 46, y: 84, r: 2 }, { x: 27, y: 8, r: 2.5 }, { x: 95, y: 38, r: 2 },
];

const EDGES: Array<[number, number]> = [
  [0, 2], [2, 4], [4, 6], [6, 9], [9, 11], [11, 12], [1, 2], [1, 3], [3, 5],
  [5, 6], [6, 7], [7, 9], [8, 10], [10, 11], [13, 1], [14, 7], [15, 5], [16, 0], [17, 11],
];

export function LoginPage() {
  const { owner, loading, error, login, loginWithToken } = useAuth();
  const [token, setToken] = useState('dev-token-secret');
  const [submittingToken, setSubmittingToken] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  if (loading) return <div className="main">Loading…</div>;
  if (owner) return <Navigate to="/chat" replace />;

  async function handleLocalLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setSubmittingToken(true);
    try {
      await loginWithToken(token.trim());
    } finally {
      setSubmittingToken(false);
    }
  }

  return (
    <BaseBound surface="login" state="idle">
      <div className="login-stage">
        {/* 装饰背景：纯 SVG，透明度 .35，aria-hidden 不进无障碍树 */}
        <div className="login-backdrop" aria-hidden="true">
          <svg preserveAspectRatio="xMidYMid slice" viewBox="0 0 100 100">
            {EDGES.map(([a, b], i) => (
              <line
                key={`e${i}`}
                className="ln-edge"
                x1={STARS[a].x}
                y1={STARS[a].y}
                x2={STARS[b].x}
                y2={STARS[b].y}
              />
            ))}
            {STARS.map((s, i) => (
              <circle
                key={`s${i}`}
                className={`ln-star${s.kind === 'alt' ? ' ln-star--alt' : s.kind === 'v' ? ' ln-star--v' : ''}${i % 3 === 0 ? ' ln-drift' : i % 3 === 1 ? ' ln-drift ln-drift--2' : ' ln-drift ln-drift--3'}`}
                cx={s.x}
                cy={s.y}
                r={s.r}
              />
            ))}
          </svg>
        </div>

        {/* 左：品牌与价值主张 */}
        <section className="login-side">
          <div className="login-brand-row">
            <div className="login-mark" aria-hidden="true">{BRAND.mark}</div>
            {/* 母品牌英文。冻结契约 e2e_journey.spec.ts:31 断言存在可访问名为
                「Find Yourself」的 heading，所以这一行不能动；中文功能名叠加在其下。 */}
            <h1 className="login-brand">{BRAND.productEn}</h1>
          </div>
          {/* 中文功能名 + 主标语 + 副标语。命名书方案 A：主标点明品类，副标三条能力并列。
              旧的「单所有者自我探索陪伴系统」只说了用途线的一半（自我探索），
              没说工具线（搭建与调度），且与新定位不符。 */}
          <p className="login-slogan">{BRAND.productZh}</p>
          <p className="login-slogan-main">{BRAND.slogan}</p>
          <p className="login-slogan-sub">{BRAND.sloganSub}</p>

          <div className="login-features">
            <p className="login-feature">
              <LineIcon name="lock" size={18} />
              <span>数据本地留存，证据链可追溯，不上传云端</span>
            </p>
            <p className="login-feature">
              <LineIcon name="knowledge" size={18} />
              <span>导入日记与工作记录，刻画多维画像</span>
            </p>
            <p className="login-feature">
              <LineIcon name="cabin" size={18} />
              <span>像素小屋作为情绪陪伴模块</span>
            </p>
          </div>
        </section>

        {/* 右：玻璃表单卡 */}
        <section className="login-card">
          <h2 className="login-title">登录</h2>
          <p className="login-lead">
            生产环境通过后端 OIDC 登录；本地桌面单机体验可直接使用本地专属口令登录。
          </p>

          {error && (
            <div className="login-notice" role="alert">
              <LineIcon name="alert" size={16} />
              <span>{error}</span>
            </div>
          )}

          <button type="button" className="ui-btn ui-btn--primary ui-btn--block" onClick={login}>
            <LineIcon name="key" size={18} />
            使用 OIDC 登录
          </button>

          <hr className="login-sep" />

          <h3 className="login-subtitle">本地 / 桌面单机口令登录</h3>
          <p className="login-note">
            在 127.0.0.1 环回地址运行，通过本地环境变量或配置文件授权。
          </p>

          {/* 回车直接提交；默认值预填，免得用户先去查配置（最少点击第 3 条） */}
          <form onSubmit={handleLocalLogin} noValidate>
            <div className="login-field">
              <label className="ui-label" htmlFor="login-token">
                本地访问口令
              </label>
              <input
                id="login-token"
                ref={inputRef}
                className="ui-input"
                type="password"
                placeholder="默认: dev-token-secret"
                autoComplete="current-password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </div>
            <button
              type="submit"
              className="ui-btn ui-btn--primary ui-btn--block ui-btn--lg"
              disabled={submittingToken || !token.trim()}
            >
              {submittingToken ? (
                <>
                  <LineIcon name="refresh" size={18} />
                  登录中…
                </>
              ) : (
                <>
                  <LineIcon name="arrowRight" size={18} />
                  本地口令直接登录
                </>
              )}
            </button>
          </form>

          <p className="login-local-dot">
            <span className="ui-dot ui-dot--complete" aria-hidden="true" />
            会话校验由后端完成；未配置 OIDC 或口令不匹配时不会通过授权
          </p>

          <p className="login-foot">
            <span className="ui-kbd">Enter</span> 提交 · <span className="ui-kbd">Tab</span>{' '}
            切换焦点，全键盘可完成登录
          </p>
        </section>
      </div>
    </BaseBound>
  );
}