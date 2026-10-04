import { createRoot } from 'react-dom/client';
import { TraceView } from './components/trace/TraceView';

/**
 * P1-16 trace 视图独立入口（/trace.html）。
 *
 * 独立于主 App 路由：不经 RequireAuth / 后端 API，便于在独立端口
 * （5195）上做 dev 走查与 Playwright E2E。正式挂载建议：在
 * src/App.tsx 的 Layout 子路由下追加
 * `<Route path="/trace" element={<TracePage />} />`（追加式，见任务报告）。
 */
const el = document.getElementById('root');
if (el) {
  createRoot(el).render(<TraceView />);
}
