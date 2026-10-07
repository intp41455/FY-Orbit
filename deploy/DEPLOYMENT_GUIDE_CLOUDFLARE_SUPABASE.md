# FY Orbit · 星轨 —— Cloudflare 与 Supabase 云端在线部署完整指南

> **适用场景**：将 FY Orbit · 星轨完整上线至公网，包含 **Cloudflare Pages** 静态前端 CDN 托管、**Cloudflare Tunnel** 全栈安全隧道公网穿透、**Supabase PostgreSQL** 云端数据库集群、**Supabase Auth** 用户邮箱注册验证体系，以及 **Cloudflare R2** 零出站费私有对象存储。

---

## 架构拓扑总览

```mermaid
graph TD
  Browser[全球用户终端浏览器 / 桌面 Client] --> CFEdge[Cloudflare 边缘 CDN 网络 (Anycast)]
  
  subgraph Cloudflare 云端边缘服务
    CFEdge --> CFPages[Cloudflare Pages: Web UI 静态宿主 (fy-orbit.pages.dev)]
    CFEdge --> CFTunnel[Cloudflare Tunnel: 安全加密双向隧道 (*.trycloudflare.com)]
    CFEdge --> CFR2[Cloudflare R2: S3 兼容私有对象存储 (零出口流量费)]
  end

  subgraph 核心后端与应用实例 (云服务器 / 边缘节点 / 本地长效守护)
    CFTunnel --> FastAPI[FastAPI 核心调度网关 (Port 8000)]
    FastAPI --> Engine[自适应工作流引擎 + Claw 质量把关 + HITL 审批]
    FastAPI --> CFR2
  end

  subgraph Supabase 托管云数据库与认证平台
    FastAPI --> SupaPG[(Supabase PostgreSQL: 88 张核心实体表 / RLS)]
    Browser -. 邮箱注册与验证链接 .-> SupaAuth[Supabase Auth (GoTrue 引擎)]
    SupaAuth --> SMTP[SMTP 邮件服务: 发送账户激活与重置确认函]
    SupaAuth -. OIDC / JWT 签发 .-> FastAPI
  end
```

---

## 第一部分：Cloudflare Pages 静态前端与宣传页部署

FY Orbit 的前端采用了 React 18 + Vite + Three.js + PixiJS 构建，所有构建产物均已编译打包至 `web/dist`，完全解耦且纯静态化。

### 1. 快速自动化部署

在根目录下或 `deploy/cloudflare` 下运行一键发布脚本：

```bash
# Windows
deploy\cloudflare\deploy-pages.bat

# 或者直接在 web 目录下调用 wrangler
cd web
npx wrangler pages deploy dist --project-name fy-orbit
```

### 2. 访问与绑定自定义域名
* 部署完成后，Cloudflare Pages 将即时分配全球 CDN 加速地址：  
  👉 **`https://fy-orbit.pages.dev`**
* 若需绑定自定义域名，在 Cloudflare Dashboard -> **Workers & Pages** -> **fy-orbit** -> **Custom domains** 中添加您的专属域名，Cloudflare 将自动配置免费的 SSL/TLS 证书。

### 3. 产品宣传页与安装包分发挂载
* 官方宣传落地页 `landing-page-2026-10-06.html` 已整合于代码库根目录与 `web/public` 中，具备官方品牌视觉规范、特性深度解析与一键下载挂载。
* 可在 Cloudflare Pages 的构建配置中将其重命名为 `landing.html`，通过 `https://fy-orbit.pages.dev/landing` 随时向全球用户展示。

---

## 第二部分：Cloudflare Tunnel 全栈服务零成本即刻公网在线体验

若希望将包含 421 个 API、本地 Python 调度中枢以及工作台后端的全栈系统直接暴露给公网用户，无需购买云服务器公网 IP 与繁琐的端口映射，直接使用 **Cloudflare Quick Tunnel**：

### 1. 启动本地全栈服务
```bash
python run.py --port 8000 --no-browser
```

### 2. 建立 Cloudflare 安全加密公网穿透
```bash
# 使用 npx 直接免安装调用 cloudflared
npx --yes trycloudflare --port 8000

# 或在安装 cloudflared 后执行：
cloudflared tunnel --url http://127.0.0.1:8000
```

### 3. 即刻获得公网访问地址
Cloudflare 将在终端输出分配的临时公网 HTTPS 地址（形如 `https://random-subdomain.trycloudflare.com`）：
* **Web 工作台与画布**：`https://random-subdomain.trycloudflare.com/`
* **交互式 API 文档**：`https://random-subdomain.trycloudflare.com/docs`
* 全程享受 Cloudflare 全球 DDoS 防护与端到端 HTTPS 加密。

---

## 第三部分：Supabase 云端 PostgreSQL 数据库搭建与全量迁移

FY Orbit 原生支持 SQLite 与 PostgreSQL 双底座同源。在云端生产环境中使用 Supabase，可获得高可用 PostgreSQL 集群与连接池（PgBouncer/Supavisor）。

### 1. 创建 Supabase 项目
1. 登录 [Supabase 控制台](https://supabase.com/dashboard)；
2. 点击 **New project**，输入项目名称（如 `fy-orbit`），设置高强度数据库密码，并选择就近地域（如东京 `ap-northeast-1` 或新加坡 `ap-southeast-1`）。

### 2. 执行全量 88 表 DDL 迁移
本项目已预置提取出完整的 88 张核心实体表 PostgreSQL DDL 语句：  
📁 **`deploy/supabase/init.sql`** (64KB 纯正标准 DDL)

**迁移步骤**：
1. 打开 Supabase 控制台左侧导航栏的 **SQL Editor**；
2. 点击 **New query**；
3. 将 `deploy/supabase/init.sql` 中的全部 SQL 内容复制粘贴至编辑器中；
4. 点击右下角 **Run** 执行；
5. 执行完毕后，可在 **Table Editor** 中看到包含 `users`, `auth_sessions`, `audit_events`, `canvas_instances`, `tasks`, `kb_documents`, `claw_gate_decisions` 等全部 88 张表已成功就绪！

### 3. 获取连接字符串
在 Supabase 项目中进入 **Project Settings** -> **Database**：
* 复制 **Connection string** (URI)，选择 **Session Mode (端口 5432)** 或 **Transaction Mode (端口 6543)**：
  ```text
  postgresql://postgres.[YOUR-PROJECT-REF]:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
  ```

---

## 第四部分：用户注册登录与邮箱验证完整机制 (Supabase Auth)

系统支持两种用户注册与邮箱验证路径：

### 路径 A：基于 Supabase Auth (GoTrue) 的邮箱激活闭环（推荐）
Supabase 内置完整的企业级身份认证与邮件服务：

1. **配置 SMTP 邮件发信**：
   * 在 Supabase 控制台进入 **Project Settings** -> **Authentication** -> **Email Settings**；
   * 勾选 **Enable Custom SMTP**；
   * 填入企业邮箱或发信服务配置（如 Resend, SendGrid, QQ 企业邮, 163 邮箱或 Gmail App Password）：
     * **Host**: `smtp.resend.com` (或 `smtp.exmail.qq.com`)
     * **Port**: `465` (SSL) 或 `587` (TLS)
     * **Username** 与 **Password**
     * **Sender email**: `noreply@yourdomain.com`
2. **启用邮箱强制确认 (Email Confirmations)**：
   * 在 **Authentication** -> **Providers** -> **Email** 中：
   * 开启 **Confirm email** 开关。
   * **效果**：用户在注册账号后，系统状态处于未激活状态，Supabase 会自动向其邮箱发送一封包含带有短效 Token 的激活确认邮件。用户点击邮件中的确认链接后，账户状态转为 `email_confirmed_at = now()`，方可登录。
3. **绑定 FY Orbit 后端**：
   在 `.env` 中配置 Supabase 的 OIDC 接口信息（详见 `deploy/supabase/supabase-config.env`）：
   ```env
   FY_OIDC_ISSUER=https://[YOUR-PROJECT-REF].supabase.co/auth/v1
   FY_OIDC_CLIENT_ID=[YOUR-ANON-KEY]
   FY_OIDC_CLIENT_SECRET=[YOUR-SERVICE-ROLE-KEY]
   ```

### 路径 B：内置安全密码与 GDPR 隐私同意机制 (Multi-Tenant Local)
若直接使用 FY Orbit 的内置认证服务（`AuthService`）：
* 密码采用不可逆散列算法加盐哈希（`hash_password`），抵御彩虹表攻击；
* 强制要求用户显式同意隐私政策（`UserConsent`），符合严格的 GDPR / 数据安全法合规要求；
* 内置防暴力破解阶梯式锁定机制：连续输错 5 次密码自动冻结 IP 与账户 15 分钟（`LOGIN_LOCK_SECONDS = 900`）；
* 访客体验平滑升级：支持一键将本地 Guest 账号升级为真实绑定邮箱的正式注册用户，工作流与资产零丢失无缝保留。

---

## 第五部分：Cloudflare R2 私有对象存储配置 (零出站费)

针对大型资产、画布截图、多模态图片与模型权重，推荐接入 Cloudflare R2：

1. 登录 Cloudflare Dashboard，进入 **R2 Object Storage**；
2. 点击 **Create bucket**，输入名称 `fy-orbit-assets`；
3. 进入 **Manage R2 API Tokens**，创建具有读写权限的 API Token；
4. 将生成的凭据填入 `.env`：
   ```env
   FY_S3_ENDPOINT=https://<your_cloudflare_account_id>.r2.cloudflarestorage.com
   FY_S3_BUCKET=fy-orbit-assets
   FY_S3_ACCESS_KEY=<r2_access_key_id>
   FY_S3_SECRET_KEY=<r2_secret_access_key>
   ```

---

## 第六部分：生产环境变量清单速查表

| 配置项 | 推荐生产值 / 格式 | 用途与说明 |
| :--- | :--- | :--- |
| `FY_ENVIRONMENT` | `production` | 启用严格安全模式，强制 HTTPS，禁用本地 Dev-Token |
| `FY_PUBLIC_URL` | `https://fy-orbit.pages.dev` | 生产公网域名，用于 OIDC 回调与跨域信任判定 |
| `FY_DATABASE_URL` | `postgresql://...:6543/postgres` | Supabase PostgreSQL 连接串 (支持 88 表高并发) |
| `FY_SESSION_SECRET` | 随机生成 ≥32 字符的强随机串 | Cookie 加密签名与 CSRF 双重令牌秘钥 |
| `FY_OIDC_ISSUER` | `https://<ref>.supabase.co/auth/v1` | Supabase 认证服务发行端点 |
| `FY_S3_ENDPOINT` | `https://<id>.r2.cloudflarestorage.com` | Cloudflare R2 对象存储端点 |
| `FY_OFFLINE_MODE` | `0` (生产在线) 或 `1` (物理离线) | 控制是否允许访问公网外部大模型 API |
| `FY_TEMPORAL_ADDRESS` | `temporal:7233` | 分布式抗打断任务调度编排引擎 |

---

## 结语

通过 **Cloudflare** 的全球边缘分发与安全隧道，搭配 **Supabase** 稳固可靠的 PostgreSQL 底座与 GoTrue 邮箱认证系统，**FY Orbit · 星轨** 既可作为单机完全断网可用的私有化利器，也能瞬间化身为高可用、抗并发、具备严密安全管控的企业级云端多智能体调度工坊！
