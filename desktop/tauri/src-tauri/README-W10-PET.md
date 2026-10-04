# W10-A 桌面宠物浮窗 · 桥接说明

## 组成
- `src/main.rs`：运行时动态建一个**透明、置顶、不进任务栏**的 pet WebviewWindow，
  导航到 `{后端地址}/pet`；提供两个命令：
  - `open_main_route(route: String)`：把主窗带到前台并跳转路由（宠物菜单用）；
  - `hide_pet_window()`：隐藏宠物浮窗（托盘唤回为后续项）。
- `preload.cjs`：向 pet 页注入 `window.__fyPetBridge = { openRoute, hideWindow }`，
  内部经 `__TAURI__.core.invoke` 接上面两个命令。纯浏览器预览 `/pet` 时不注入。
- `capabilities/default.json`：窗口权限列表从 `["main"]` 扩为 `["main","pet"]`。

## 挂载 preload
在 pet 窗 builder 上加一行（路径相对 src-tauri）：
```rust
WebviewWindowBuilder::new(tauri_window, "pet", WebviewUrl::External(pet_url.parse()?))
    .title("Find Yourself · Pet")
    .transparent(true)
    .always_on_top(true)
    .decorations(false)
    .skip_taskbar(true)
    .resizable(false)
    .preload("preload.cjs")   // <— 加这行
    .build()?;
```
或在 `tauri.conf.json` 的 `app.windows[].webviewPreferences.preload` 配置。

## 未完成/待验证
- **本机无 Rust 工具链，未 `cargo check` / 编译**。上述 builder 用法与 preload
  配置需在有 Rust 工具链的机器上出包时验证；如有 API 版本差异需微调。
- 宠物位置记忆、系统托盘「显示宠物」开关为后续项。
- 真实截图/点击依赖 `mss`/`pyautogui`/`pygetwindow`，后端缺依赖时诚实返回 503。
