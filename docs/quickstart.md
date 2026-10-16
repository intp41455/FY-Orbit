# 快速开始（下载即用）

> 目标：**下载 → 双击 → 能用**。不注册、不联网、不手写配置文件。
> 本文只写「下载即用」这一条最短路径；开发者从源码跑见文末。

## 一、Windows 用户：下载即用

### 1. 下载

去 [Releases](https://github.com/intp41455/FY-Orbit/releases/latest) 下两个二选一：

| 产物 | 适合谁 | 说明 |
|---|---|---|
| `FY-Orbit-Setup-v1.3.3.exe` | 大多数人 | 安装向导，可创建桌面快捷方式，走开始菜单启动 |
| `FY-Orbit-Windows-v1.3.3.zip` | 不想装东西 | 绿色包，解压即用。**同页附 `.sha256`**，校验值一起下 |

### 2. 校验完整性（可选但建议）

绿色包解压后，同目录的 `.sha256` 是该 zip 的校验值：

```powershell
Get-FileHash .\FY-Orbit-Windows-v1.3.3.zip -Algorithm SHA256
# 与 FY-Orbit-Windows-v1.3.3.zip.sha256 里的值比对
```

对不上就别运行 —— 供应链攻击的最低成本防线。

### 3. 启动

- 装包：开始菜单 / 桌面双击 **FY-Orbit**。
- 绿包：双击解压出的 `FY-Orbit.bat`。

首启会自动做三件事，**全程不需要你填任何东西**：

1. 生成 `%APPDATA%\FindYourself\.env`（随机会话密钥与本地令牌；已有则**不覆盖**）；
2. 拉起本地服务；
3. 打开浏览器工作台。

> 没有 `FY_SESSION_SECRET` / `FY_LOCAL_TOKEN` 时服务会**故意拒绝启动**
> （"不落不安全默认值"是设计，不是故障）。上面这一步就是为它们准备的。

### 4. 用起来

浏览器打开后即匿名可用（访客身份，365 天有效）。想跨设备同步再注册账号。

## 二、Docker 用户：一条命令

```bash
git clone https://github.com/intp41455/FY-Orbit.git
cd FY-Orbit
docker compose up -d --build
```

打开 <http://localhost:8000>。细节见 [docker.md](docker.md)。

## 三、开发者：从源码跑

需要 Python 3.12+ 与 [uv](https://github.com/astral-sh/uv)。

```bash
git clone https://github.com/intp41455/FY-Orbit.git
cd FY-Orbit
uv sync --frozen
uv run run.py            # 默认 http://127.0.0.1:8000
```

跑测试：

```bash
uv run pytest tests -q
cd web && npm ci && npx tsc --noEmit && npx vitest run
```

## 四、离线说明

首启 0 外网依赖（字体/CDN 一律不拉）。默认 `FY_OFFLINE_MODE=True`：
**绝不静默外传数据**，远程模型路由在离线模式下不可用并给明确错误码
（`OfflineUnavailable`），而不是返回一段假答案。接云端模型需你自己显式配置。

## 五、出问题先看这里

| 现象 | 先查 |
|---|---|
| 服务起不来，日志说缺 `FY_LOCAL_TOKEN` | 走的是源码裸启动；删掉 `.env` 让首启重新生成，或参考上面「开发者」段 |
| 智能体全部 `failed`、`blocked_reason` 是 `model_not_configured` | 没配模型。这是**诚实失败**，不是 bug |
| 团队永远停在「执行中」 | 旧版本缺陷，已在 `c1ed30b` 修复：现在轮次用尽会写明 `runner_gave_up` 并标 failed |
| 端口被占 | 换 `FY_PORT` 后重启 |

## 相关

- 架构与边界：[architecture/](architecture/)
- 编排引擎选型（LangGraph / Temporal 可降级双引擎）：[temporal.md](temporal.md)
- 容器形态：[docker.md](docker.md)