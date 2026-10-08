import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { ErrorBoundary } from './ErrorBoundary';
import { useAuth } from '../auth/AuthContext';
import { OfflineBadge } from './ui';
import { NotificationBell } from './NotificationBell';
import { LineIcon, type LineIconName } from './ui/LineIcon';
import { BRAND } from '../brand';
import { DeskPetCompanion } from './pet/DeskPetCompanion';

/**
 * 工作台优先、个人空间独立切换。团队画布收在「协作画布」内，不新增一级菜单。
 *
 * 收口包改造（07-收口与验收.md §2）：emoji → LineIcon 线条图标，
 * 顺序按工作流而非字母（高频项在最上），1–9 项带 .ui-kbd 数字角标。
 *
 * 口径说明：
 * - §2 标题写「全部 21 项重排」，但表里只列了 18 项（工作台 10 + 个人 8），
 *   代码里也正好 18 项。以表为准，18 是实际项数；21 疑为早期含 /private 等
 *   已裁决项的旧数，已记入总报告待主控确认。
 * - /skills 只登记一条（个人空间那条死条目「知识与技能」已按裁决删除），
 *   §2 明确要求不要写成两条，这里保持单条。
 * - /private 按裁决保留路由但本轮不做导航入口，§2 表里也没有它，故不加项。
 */
type Space = 'workbench' | 'personal';

interface NavItem {
  to: string;
  label: string;
  sub: string;
  /** 数字键直切角标：1–9；null 表示不显示。 */
  kbd: number | null;
  icon: LineIconName;
  space: Space;
}

const NAV: NavItem[] = [
  // ---- 工作台空间（§2 表顺序 1–10） ----
  { to: '/workbench', label: '任务工作台', sub: '指挥 · 终端 · 验收', kbd: 1, icon: 'workbench', space: 'workbench' },
  { to: '/canvas', label: '协作画布', sub: '内部团队 · 逐成员选模型', kbd: 2, icon: 'canvas', space: 'workbench' },
  { to: '/dsl-canvas', label: '工作流工坊', sub: '受限动词 · 数据流执行', kbd: 3, icon: 'flow', space: 'workbench' },
  // A-任务看板-01～13。icon 复用既有 LineIcon 的 grid（禁改 LineIcon.tsx），
  // kbd 留空：1-9 已被上面九条占满，抢号会与既有快捷键冲突。
  { to: '/kanban', label: '任务看板', sub: '四列 · 加权进度 · 依赖', kbd: null, icon: 'grid', space: 'workbench' },
  { to: '/chat-debug', label: 'Chat 调试', sub: '模板 · 工具 · 流式联动', kbd: 4, icon: 'sliders', space: 'workbench' },
  { to: '/agent-dispatch', label: '子 Agent 派发', sub: 'Task 协议 · 独立验收', kbd: 5, icon: 'dispatch', space: 'workbench' },
  { to: '/skills', label: 'Agent 与技能', sub: 'MCP · 经验沉淀', kbd: 6, icon: 'skills', space: 'workbench' },
  { to: '/hub', label: '超级中台', sub: '统一适配层 · 能力路由', kbd: 7, icon: 'hub', space: 'workbench' },
  { to: '/approvals', label: '审批中心', sub: '提案与授权', kbd: 8, icon: 'approvals', space: 'workbench' },
  { to: '/plugins', label: '插件市场', sub: '签名 · 扫描 · 授权安装', kbd: 9, icon: 'plugins', space: 'workbench' },
  { to: '/templates', label: '开箱模板', sub: '成套团队 · 总控预设', kbd: null, icon: 'cube', space: 'workbench' },
  { to: '/dossier', label: '任务档案库', sub: '档案全景 · 企业适配', kbd: null, icon: 'archive', space: 'workbench' },
  { to: '/timeline', label: '存档回溯', sub: '分叉重跑 · 差异对比', kbd: null, icon: 'timeline', space: 'workbench' },
  { to: '/observability', label: '统一可观测', sub: '实时流 · 性能与成本', kbd: null, icon: 'chart', space: 'workbench' },
  { to: '/settings', label: '设置与数据', sub: '同步 · 权限', kbd: null, icon: 'settings', space: 'workbench' },

  // ---- 个人空间（§2 表顺序 11–18） ----
  { to: '/chat', label: '对话', sub: '流式会话', kbd: null, icon: 'chat', space: 'personal' },
  { to: '/cabin', label: '我的小屋', sub: '数码小人 · 经营模拟', kbd: null, icon: 'cabin', space: 'personal' },
  /**
   * 私人空间（/private）：图片 / 音频 / 音乐三模块，真实对接 /api/assets。
   *
   * 07 §2 曾记录「/private 有路由但导航无入口」，当时用户裁决为「有路由就先留着，
   * 记为待办」，并要求收口包不要为它加项。**该裁决已被用户推翻**（2026-10-06：
   * 「私人空间那个页面在左侧栏没有对应的标签导航页，给它加上一个」），
   * 故此处按「独立一级页」补上入口。
   *
   * 位置放在「我的小屋」之后：素材可从私人空间一键挂进小屋，两者相邻便于串联。
   * 个人空间项一律无 .ui-kbd 角标（§2 只要求 1–9 项），所以插入不影响编号。
   */
  { to: '/private', label: '私人空间', sub: '图片 · 音频 · 音乐素材', kbd: null, icon: 'folder', space: 'personal' },
  { to: '/history', label: '历史', sub: '会话留痕', kbd: null, icon: 'history', space: 'personal' },
  { to: '/growth', label: '成长记录', sub: '阶段与复盘', kbd: null, icon: 'growth', space: 'personal' },
  { to: '/assessments', label: '测评', sub: '结构化评估', kbd: null, icon: 'assessments', space: 'personal' },
  { to: '/profiles', label: '多维画像', sub: '个人与对象', kbd: null, icon: 'profiles', space: 'personal' },
  { to: '/fortune', label: '星轨命理', sub: '每日签 · 塔罗 · 合盘', kbd: null, icon: 'sparkles', space: 'personal' },
  { to: '/avatar', label: '角色工坊', sub: '专属像素小人', kbd: null, icon: 'avatar', space: 'personal' },
  { to: '/knowledge', label: '知识库', sub: '本地文档 RAG · 适配器', kbd: null, icon: 'knowledge', space: 'personal' },
];

/**
 * 路由 → 空间。**唯一事实源**：空间是路由的纯函数，不另存本地状态。
 *
 * 匹配用路径边界（全等或 `to + '/'` 前缀）而不是裸 `startsWith`：
 * 裸前缀会让 `/chat` 吃掉 `/chat-debug`，此前只是靠 NAV 里 workbench 段
 * 排在 personal 段之前才碰巧正确 —— 顺序一变就静默串空间。
 */
function spaceForPath(pathname: string): Space {
  const hit = NAV.find((n) => pathname === n.to || pathname.startsWith(`${n.to}/`));
  return hit?.space ?? 'workbench';
}

/** 切换空间时落到该空间的入口页：让「空间」与「路由」始终指同一件事。 */
const SPACE_HOME: Record<Space, string> = {
  workbench: '/workbench',
  personal: '/chat',
};

/** 全局字号缩放控件（侧栏底部，常驻可见但低调）。
 *
 * 与 main.tsx 的 applyFontScale 分工：那边负责「真正改 CSS」，这边只负责
 * 「显示当前值 + 提供按钮」。两者通过 window 自定义事件通信 —— 缩放是全局
 * CSS 关注点，不该为了显示一个百分比就让整棵 React 树重渲染。 */
function FontScaleIndicator() {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const onScale = (e: Event) => {
      const detail = (e as CustomEvent<{ scale: number }>).detail;
      if (detail && Number.isFinite(detail.scale)) setScale(detail.scale);
    };
    // 挂载时主动问一次当前值（main.tsx 已经把上次的选择写进 localStorage 并应用了）。
    try {
      const raw = window.localStorage.getItem('fy.fontScale');
      const n = raw ? Number(raw) : 1;
      setScale(Number.isFinite(n) ? n : 1);
    } catch {
      /* 隐私模式读不到就保持 100%，不影响功能 */
    }
    window.addEventListener('fy:fontscale', onScale);
    return () => window.removeEventListener('fy:fontscale', onScale);
  }, []);

  const pct = Math.round(scale * 100);

  return (
    <div
      className="font-scale-indicator"
      data-testid="font-scale-indicator"
      title="Ctrl+滚轮 或 Ctrl+加号/减号 调整全局字号；Ctrl+0 复位"
    >
      <button
        type="button"
        className="font-scale-btn"
        data-testid="font-scale-minus"
        aria-label="字号调小"
        onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '-', ctrlKey: true, bubbles: true }))}
      >
        −
      </button>
      <button
        type="button"
        className="font-scale-value"
        data-testid="font-scale-value"
        aria-label="复位到 100%"
        onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '0', ctrlKey: true, bubbles: true }))}
      >
        {pct}%
      </button>
      <button
        type="button"
        className="font-scale-btn"
        data-testid="font-scale-plus"
        aria-label="字号调大"
        onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '+', ctrlKey: true, bubbles: true }))}
      >
        +
      </button>
      <span className="font-scale-hint">Ctrl+滚轮调字号</span>
    </div>
  );
}

export function Layout() {
  const { owner, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  // 空间 = 路由的纯函数。这里曾同时持有 `space` state 与路由派生的 `derived`，
  // 手动切换只改 state 而不改路由，导致 effect（依赖 derived）不重跑，
  // 侧栏分组与实际所在页面长期不一致。改为单事实源后该失效模式消失。
  const space = spaceForPath(location.pathname);

  // 切空间 = 导航到该空间入口页。已在该空间时是 no-op，
  // 免得从 `/history` 点一下「个人空间」就被拽回 `/chat`。
  function switchSpace(next: Space) {
    if (next === space) return;
    navigate(SPACE_HOME[next]);
  }

  // P3-21: owner 展示名。优先真实昵称；只有 sub（游客哈希）时脱敏。
  const ownerSub = owner?.sub ?? '';
  const ownerLabel = owner?.name?.trim()
    || (ownerSub ? `${ownerSub.slice(0, 8)}…` : '')
    || '本地访客';
  const ownerLabelFull = owner?.name?.trim() || ownerSub || '本地访客';

  const items = NAV.filter((n) => n.space === space);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">{BRAND.mark}</div>
          <div className="brand-text">
            {/* 母品牌中文功能名 + 主标语。旧的「AI 协作工作台 / 让 AI 团队，把事情
                做完」是改名前的遗留：同一产品在侧栏、登录页、预览区曾有三个不同名字，
                且后半句是修辞，命名书 §一.3 明令标语「直白、专业、无修辞」。
                文案事实源见 src/brand.ts。 */}
            <strong>{BRAND.productZh}</strong>
            <span>{BRAND.slogan}</span>
          </div>
        </div>

        <div className="space-switch" role="group" aria-label="空间切换">
          <button
            type="button"
            className={space === 'workbench' ? 'active' : ''}
            aria-pressed={space === 'workbench'}
            onClick={() => switchSpace('workbench')}
          >
            <LineIcon name="workbench" size={16} /> 工作台空间
          </button>
          <button
            type="button"
            className={space === 'personal' ? 'active' : ''}
            aria-pressed={space === 'personal'}
            onClick={() => switchSpace('personal')}
          >
            <LineIcon name="avatar" size={16} /> 个人空间
          </button>
        </div>

        <div>
          <div className="nav-label">
            {space === 'workbench' ? '工作台功能导航' : '个人空间导航'}
          </div>
          <nav className="nav" aria-label="主导航">
            {items.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end
                className={({ isActive }) => (isActive ? 'active' : '')}
              >
                {/* 图标为装饰：可访问名由紧随其后的 label 文本承担，
                    所以 LineIcon 必须 aria-hidden，不能让它进无名。 */}
                <span className="nav-icon" aria-hidden="true">
                  <LineIcon name={n.icon} size={20} />
                </span>
                <span>
                  {n.label}
                  <span className="nav-sub">{n.sub}</span>
                </span>
                {n.kbd !== null && (
                  <kbd className="ui-kbd nav-kbd" aria-hidden="true">{n.kbd}</kbd>
                )}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="sidebar-foot">
          <NavLink to="/settings" className="control-chip">
            <span className="row" style={{ gap: '0.45rem' }}>
              <span className="dot sky" aria-hidden="true" />
              权限与沙箱隔离
            </span>
            <span className="badge ok">受控</span>
          </NavLink>
          <NavLink to="/settings" className="control-chip">
            <span className="row" style={{ gap: '0.45rem' }}>
              <span className="dot amber" aria-hidden="true" />
              预算与用量
            </span>
            <span style={{ color: 'var(--amber)', fontWeight: 700 }}>查看</span>
          </NavLink>
          <div className="identity" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            {/* P3-21: 不裸露完整 owner id。游客会话 sub 是 32 位哈希，
                直接铺在侧栏既占版面又泄漏内部标识；这里只露前 8 位，
                完整值留在 title / aria-label 里供需要时查看。 */}
            <span
              title={ownerLabelFull}
              aria-label={ownerLabelFull}
              data-testid="sidebar-owner"
            >
              {ownerLabel}
            </span>
            <NotificationBell />
          </div>
          <FontScaleIndicator />
          <button type="button" className="small ghost" onClick={() => void logout()}>
            登出
          </button>
        </div>
      </aside>

      <main className="main">
        <OfflineBadge />
        {/* 页面级错误边界：只替换内容区，侧边导航保留；key 随路由变化自动复位，
            避免一次崩溃后切到别的页面也一直是错误页。 */}
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
      <DeskPetCompanion />
    </div>
  );
}