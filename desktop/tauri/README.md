# Find Yourself 桌面壳（Tauri v2）

本地优先的桌面端 AI 超级工作台。**数据全部留在你自己的电脑上**；工作台免登录
即可使用，个人空间里的云同步 / 社区 / 导出分享才需要账号。

> ⚠️ **构建状态诚实声明**：本仓库**未在当前机器上打包成功**——本机没有 Rust
> 工具链（`cargo` / `rustc` 均不存在）。交付物是**完整工程 + 可执行构建脚本 +
> 验收清单**，不是已验证的安装包。在装了 Rust 的机器上执行下面的命令即可出包。
> 这一条按项目「诚实原则」明确标注，不以「脚本写完了」冒充「包已验证」。

---

## 1. 为什么是 Tauri（以及为什么不是 Electron）

| 维度 | Tauri v2（已选） | Electron（备选） |
|---|---|---|
| 安装包体积 | 复用系统 webview，通常 5–15 MB | 随包捆绑 Chromium，~100 MB 起 |
| 常驻内存 | 明显更低 | 更高（独立浏览器进程树） |
| 安全面 | Rust 后端默认不开放任意系统调用，前端只能走显式 command 白名单 | 需靠 `nodeIntegration:false` + `contextIsolation:true` 配置正确性保证 |
| 分发 | NSIS / MSI / deb / AppImage，可签名 | 同左 |

**什么情况下应该改选 Electron**：需要大量 Node 生态原生模块，或必须让
WebView2 / WKWebView / WebKitGTK 渲染行为完全一致。本项目前端只用标准 Web
API，且已有 jsdom + Playwright 真实 E2E 覆盖，因此不选。

两条启动路径的完整差异见 `desktop/sidecar/__init__.py` 的模块文档（那里是
单一事实来源，本文不重复）。

## 2. 两条启动路径

| | 开发者路径 | 发行路径（本目录） |
|---|---|---|
| 入口 | `desktop/app/run_desktop.py`、`start.ps1` | `desktop/tauri/` + `desktop/sidecar/build.ps1` |
| 需要已装 Python | **是** | **否**（后端打成独立 exe） |
| 窗口 | Edge App 模式 | Tauri 原生窗口 |
| 适用 | 开发调试，改完刷新即生效 | 交付给用户的安装包 |

两条路径共用同一套后端与账号分层（游客 / 注册 / 会员位），登录行为一致。

**不内置的东西**（明确边界）：不打包 Python 解释器，不打包 Ollama。
Ollama 是用户自行安装的本地推理后端，设置页只**检测连通性**——检测不到就如实
报错，不会假装模型可用。

## 3. 构建

### 3.1 前置条件

```powershell
# Rust（必需，Tauri 编译用）
winget install Rustlang.Rustup     # 或访问 https://rustup.rs/
# 重开一个终端后确认：
cargo --version

# Python + Node
python --version                  # 建议 3.12+
node --version
```

### 3.2 一键出包

```powershell
cd desktop\tauri
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

产物：`dist-desktop\FindYourself-Setup.exe`（NSIS）。

脚本会依次执行「前端构建 → sidecar 打包 → cargo tauri build」，任一步失败立即
停止并说明原因。单独跑某一步：

```powershell
powershell -ExecutionPolicy Bypass -File ..\sidecar\build.ps1   # 只出后端 exe
.\build.ps1 -SkipWeb -SkipSidecar                                # 只重编壳
```

### 3.3 开发模式

```powershell
# 终端 1：后端（开发路径）
.\start.ps1 -Port 8000

# 终端 2：Tauri 壳（会自己拉 sidecar）
cd desktop\tauri
npx --no-install tauri dev
```

## 4. 端口握手（为什么不会和开发实例打架）

壳子**不写死端口**——用户很可能已经开着 8088 的开发后端。流程是：

1. 壳以 `--port 0` 拉起 sidecar，让操作系统分配空闲端口；
2. sidecar 绑定成功后打印一行 `FY_SIDECAR_READY <port>`，并把同一个端口写到
   `%APPDATA%\FindYourself\run\port.txt`；
3. 壳读到端口后才创建窗口加载 `http://127.0.0.1:<port>`。

stdout 与文件两条通道取先到者（窗口版 PyInstaller 会吞掉 stdout）。
**两条都拿不到时壳会明确报错并显示原因**，不会去猜端口——猜端口的表现是白屏，
看起来像应用卡死，比报错更难排查。

## 5. 数据与密钥

* SQLite：`%APPDATA%\FindYourself\data\find-yourself.db`
* 运行期端口文件：`%APPDATA%\FindYourself\run\port.txt`
* `.env`：首次启动**自动生成**到 `%APPDATA%\FindYourself\.env`，内含随机会话
  密钥与本地口令；已有文件不覆盖。

密钥不写死默认值——项目历史上出过「可猜测默认凭据比没有凭据更危险」的
问题（P1-14），这里不重复。

## 6. 干净机器验收清单

在一台**没装 Python、没装 Node、没开过开发后端**的 Windows 机器上执行：

- [ ] 1. 双击 `FindYourself-Setup.exe` 完成安装（当前用户模式，无需管理员）
- [ ] 2. 桌面出现「Find Yourself」快捷方式，启动后**窗口出现**且标题正确
- [ ] 3. 窗口最小尺寸不小于 1280×720（拖小到边界不应再缩小）
- [ ] 4. 后端**自动拉起**：任务管理器出现 `find-yourself-backend.exe`
- [ ] 5. **游客无感进入工作台**：没有任何登录弹窗，直接进入工作台界面
- [ ] 6. 工作台功能可用：能打开知识库、小屋、画布；能新建一条对话
- [ ] 7. 点「云同步 / 社区」类入口才提示注册（工作台本身不拦）
- [ ] 8. 设置页能看到账号卡，显示为「游客」，并有「升级为正式账号」入口
- [ ] 9. 游客升级后：此前创建的对话**仍然存在**（同一账号就地升级）
- [ ] 10. 关闭窗口 = 完全退出：任务管理器里 `find-yourself-backend.exe` 消失
- [ ] 11. 重启应用：数据仍在（SQLite 落在 `%APPDATA%`，未被清理）
- [ ] 12. 先手动启动 8088 端口的别的服务，再启动本应用——**不冲突**，能正常打开
- [ ] 13. 断网启动：应用能打开，本地功能可用，联网功能**如实报错**而非假装成功

第 12 条是端口握手的核心验证；第 5、9、10、13 条是「诚实原则」的可观察证据。

## 7. 已知限制

* **托盘常驻未做**（任务书允许延后）：关闭窗口即退出，没有「最小化到托盘」。
* **自动更新未接**：安装包需用户手动重下。
* **代码签名未做**：SmartScreen 可能提示未知发布者。
* **Ollama 不随包分发**：需用户自行安装，设置页检测连通性。
