import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
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
