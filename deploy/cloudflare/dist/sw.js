// FY Orbit · 宣传页自毁 Service Worker
// 用途：此前 find-yourself 应用 PWA 曾部署到本域名，其 Workbox SW 会拦截
//       所有页面导航并返回缓存的应用界面（导致访问宣传页却看到应用报错）。
//       本文件用于接管 /sw.js 路径，清空全部缓存并注销自身，恢复为纯静态宣传页。
// 行为：不注册任何 fetch 处理器 —— 所有请求直连网络。

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // 1) 清空所有 Cache Storage
      try {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      } catch (e) {}

      // 2) 注销自身，之后不再拦截任何请求
      try {
        await self.registration.unregister();
      } catch (e) {}

      // 3) 让所有已打开的页面重新加载，立即看到真实内容
      try {
        const clients = await self.clients.matchAll({ type: 'window' });
        for (const c of clients) {
          try {
            c.navigate(c.url);
          } catch (e) {}
        }
      } catch (e) {}
    })()
  );
});

// 故意不注册 'fetch' 监听器：让浏览器直连网络。
