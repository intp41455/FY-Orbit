# FY Orbit · 宣传落地页（fy-orbit.pages.dev）部署说明

## 这是唯一权威来源

`deploy/cloudflare/dist/` 就是 **https://fy-orbit.pages.dev** 的完整静态站点，已纳入 Git 版本管理。
线上内容和这里 **必须逐字节一致**，任何修改都请改这个目录，而不是别处的副本。

```
deploy/cloudflare/dist/
├── index.html                        # 落地页（唯一正文）
├── sw.js                             # 自毁 Service Worker（防止历史 PWA 劫持导航）
├── _headers                          # sw.js / index.html 禁用长效缓存
└── assets/
    ├── 20-avatar-workshop.webp       # 角色工坊
    └── screenshots/
        ├── 02-workflow-dsl-canvas.webp
        ├── 04-multi-agent-team-canvas.webp
        ├── 06-knowledge-3d-galaxy-v2.webp
        ├── 07-personal-cabin-v2.webp
        └── collab-canvas-agents.webp
```

## 一键部署（唯一正确命令）

```bat
cd deploy\cloudflare
npx wrangler pages deploy dist --project-name fy-orbit
```

或直接双击 `deploy-pages.bat`（已按上面的命令写好）。

> 部署必须 **带 `dist` 资源目录**。只传单个 index.html 会让页面上所有图片 404。

## ⚠️ 两个曾经踩过的坑

1. **别把 `web/dist` 部署到 `fy-orbit`。**
   `web/dist` 是 Web 工作台 SPA 的构建产物，属于 `find-yourself` 项目
   （https://find-yourself-45j.pages.dev）。一旦部署到 `fy-orbit`，
   落地页会被整体覆盖成应用界面。本目录的 `wrangler.toml` 已改为指向 `./dist`。
   SPA 请用 `deploy-app-frontend.bat` 部署。

2. **别在别的目录副本上改落地页。**
   旧版本根目录有一份 `landing-page-2026-10-06.html`（临时页，已于 02e1008 删除），
   本目录 `dist/index.html` 已是线上 `fy-orbit.pages.dev` 的**唯一权威来源**。
   发行包里的 `landing.html` 由 `deploy/build_release_package.py` 直接从这里取，
   不需要也不允许另存副本。改时只改这里。

## 关联项目

| 项目 | 域名 | 内容 | 部署方式 |
| --- | --- | --- | --- |
| `fy-orbit` | fy-orbit.pages.dev | 宣传落地页 | 本目录 `wrangler pages deploy dist` |
| `find-yourself` | find-yourself-45j.pages.dev | Web 工作台 SPA | `deploy-app-frontend.bat` |
