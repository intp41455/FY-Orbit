/* W10-A · 桌面宠物浮窗最小桥。
 *
 * 注入 `window.__fyPetBridge`，把宠物浮窗（/pet 页）右键菜单里的动作接到
 * Rust 侧命令 open_main_route / hide_pet_window。非 Tauri 环境（纯浏览器预览
 * /pet 页）下 window.__TAURI__ 不存在，这里不做任何事，前端 resolveBridge 会
 * 自动退化为 no-op。
 *
 * 挂载：在 pet 窗 WebviewWindowBuilder 上 `.preload("preload.cjs")`，或在
 * tauri.conf.json 的 webviewPreferences.preload 指定本文件。
 */
;(function () {
  if (typeof window === 'undefined') return
  // 非 Tauri 壳里（浏览器直接打开 /pet）不注入。
  if (!window.__TAURI__ || !window.__TAURI__.core) return
  var invoke = window.__TAURI__.core.invoke
  window.__fyPetBridge = {
    openRoute: function (route) {
      return invoke('open_main_route', { route: route })
    },
    hideWindow: function () {
      return invoke('hide_pet_window')
    },
  }
})()
