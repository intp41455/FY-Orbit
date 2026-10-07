import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
// 设计系统地基（令牌 + .ui-* 共享层），必须在 styles.css 之后加载以覆盖旧别名。
import './styles/tokens.css';
import './styles/desktop-window.css';
import App from './App';
import { registerSW } from 'virtual:pwa-register';

// Register the service worker. It only precaches the static shell (vite.config
// workbox config). No runtime caching of API / private messages / tokens.
registerSW({ immediate: true });

const container = document.getElementById('root');
if (!container) throw new Error('root element missing');

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
