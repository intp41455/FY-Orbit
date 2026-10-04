"""Find Yourself — 桌面壳选型与两条启动路径的说明（W8）。

为什么选 Tauri v2 而不是 Electron
--------------------------------
本项目是**本地优先**的桌面工作台：绝大多数时间只有本机一个用户，后端是同机
的 Python sidecar，前端是已经存在的 React/Vite 产物。壳子在这里只负责三件事：
起一个 sidecar、握手拿到端口、加载 URL、退出时收干净。据此比较：

* **体积 / 内存**：Tauri 复用系统 webview（Windows 10/11 自带 WebView2，macOS
  自带 WKWebView，Linux 用 WebKitGTK）。安装包通常 5–15 MB 量级，常驻内存
  明显低于 Electron。Electron 会随应用再捆绑一份 Chromium（~100 MB+），
  对一个「本来就常驻一个后端进程」的产品是重复开销。
* **安全面**：Tauri 的 Rust 后端默认不暴露任意系统调用，前端只能走显式声明
  的 command 白名单；配合 CSP 与冻结契约里的「不返回密钥/堆栈」原则，攻击面更
  小。Electron 本身要求 `nodeIntegration: false` + `contextIsolation: true`
  才能达到同等效果，配置正确性靠人保证。
* **分发**：Tauri 出 NSIS / MSI / deb / AppImage，Windows 上还能签名为
  SmartScreen 信誉更好的安装器。

Electron 的合理备选场景（写清楚以便将来推翻本决策）：需要大量 Node 生态
原生模块、或需要跨平台完全一致的 Chromium 渲染行为（某些 WebView2 与
WKWebView 的 CSS/JS 差异会造成问题）。本项目前端只用标准 Web API，
且已在 jsdom + Playwright 下有真实 E2E 覆盖，因此不选 Electron。

两条启动路径的差异（务必不要混淆）
----------------------------------
1. **开发者路径**：`desktop/app/run_desktop.py` + `desktop/app/start.ps1`。
   要求本机**已装 Python**（用项目 `.venv` 或系统解释器），直接拉起 uvicorn，
   然后用 Edge App 模式开一个独立应用窗口。改代码后刷新即生效，适合开发。
2. **发行路径**：`desktop/tauri/` + `desktop/sidecar/build.ps1`。
   PyInstaller 把后端打成**独立 exe**（`--onedir`），Tauri 以 sidecar 方式拉起
   它，用户**不需要装 Python**。前端静态产物随包分发，窗口由 Tauri 提供。

两条路径共用同一套后端与同一套账号分层逻辑（游客/注册/会员），登录行为完全
一致；差别只在「谁来起后端」和「窗口是谁的」。本任务**不**把 Python 解释器
或 Ollama 打进安装包：Ollama 是用户自行安装的本地推理后端，由设置页检测
连通性；把模型和解释器塞进安装包会让包体失控，且与「本地优先」的用户预期
（自己的数据、自己的进程）相悖。
"""

from __future__ import annotations

# 该模块只承载文档，不参与运行；保留常量以便测试与其它模块引用同一事实。
TAURI_APP_NAME = "Find Yourself"
TAURI_WINDOW_TITLE = "Find Yourself"
TAURI_MIN_WIDTH = 1280
TAURI_MIN_HEIGHT = 720

#: sidecar 握手前缀。Rust 侧与 Python 侧共用，改动必须同步。
READY_PREFIX = "FY_SIDECAR_READY"

#: 本地数据目录（相对 %APPDATA%）。与 run_desktop.py 的
#: ``%LOCALAPPDATA%\FindYourself\data`` 保持同一布局意图。
DATA_SUBDIR = "FindYourself"
