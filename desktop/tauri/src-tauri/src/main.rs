//! Find Yourself desktop shell — Tauri v2 entry point.
//!
//! Responsibilities (and nothing else):
//!   1. spawn the Python backend sidecar,
//!   2. learn the port it actually bound (see "port handshake" below),
//!   3. point the window at `http://127.0.0.1:<port>`,
//!   4. tear the sidecar down when the window closes.
//!
//! # Port handshake
//!
//! The shell cannot hardcode a port: the user may already be running the dev
//! backend (typically :8088), and two instances must not fight over it. The
//! sidecar (`desktop/sidecar/sidecar_main.py`) therefore binds port 0, then
//! prints one line to stdout:
//!
//! ```text
//! FY_SIDECAR_READY <port>
//! ```
//!
//! It also writes the same number to `%APPDATA%/FindYourself/run/port.txt` for
//! the case where a windowed PyInstaller build swallows stdout. We read both
//! and take whichever arrives first. If neither yields a port we **fail loudly**
//! with a message — we never guess a port, because guessing produces a white
//! window that looks like a hung app.
//!
//! # Why the window navigates to 127.0.0.1 rather than a custom scheme
//!
//! Serving the frontend from the backend keeps cookies first-party and
//! same-origin, so `SameSite=Lax` session cookies and the CSRF double-submit
//! header work with no CORS configuration at all (same reasoning as the vite
//! dev proxy in `web/vite.config.ts`).

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const READY_PREFIX: &str = "FY_SIDECAR_READY";
const SIDECAR_EXE: &str = "find-yourself-backend.exe";
const READY_TIMEOUT: Duration = Duration::from_secs(30);
const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Handle to the running backend, killed on window close / app exit.
struct Sidecar(Mutex<Option<CommandChild>>);

impl Sidecar {
    fn kill(&self) {
        if let Some(child) = self.0.lock().ok().and_then(|mut s| s.take()) {
            let _ = child.kill();
        }
    }
}

/// Backend base URL once the sidecar reports its port. The pet window and the
/// main window both navigate here so cookies/CSRF stay same-origin (W10).
struct BackendUrl(Mutex<String>);

/// W10-A: invoked from the pet window's right-click menu. Bring the main window
/// to the foreground and navigate it to the given in-app route (e.g. /cabin).
#[tauri::command]
fn open_main_route(app: AppHandle, route: String) -> Result<(), String> {
    let base = app
        .try_state::<BackendUrl>()
        .ok_or("backend url not ready")?
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    let main = app.get_webview_window("main").ok_or("main window missing")?;
    let url = format!("{base}{route}");
    main.navigate(url.parse().map_err(|e| format!("bad url: {e}"))?)
        .map_err(|e| e.to_string())?;
    main.set_focus().map_err(|e| e.to_string())?;
    Ok(())
}

/// W10-A: invoked from the pet window's "hide" menu item.
#[tauri::command]
fn hide_pet_window(window: tauri::WebviewWindow) -> Result<(), String> {
    window.hide().map_err(|e| e.to_string())
    // The pet can be brought back from the system tray later; tray itself is
    // deferred (W8 acceptance note: tray/auto-update/post-sig are out of scope).
}

/// Read the port the sidecar wrote to disk, if it is already there.
fn port_from_file() -> Option<u16> {
    let base = std::env::var("APPDATA")
        .or_else(|_| std::env::var("LOCALAPPDATA"))
        .ok()?;
    let path: PathBuf = [base.as_str(), "FindYourself", "run", "port.txt"].iter().collect();
    let text = std::fs::read_to_string(path).ok()?;
    text.trim().parse::<u16>().ok()
}

/// Locate the sidecar executable shipped next to the app binary.
fn sidecar_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let exe = app
        .path()
        .resource_dir()
        .map_err(|e| format!("cannot resolve resource dir: {e}"))?
        .join(SIDECAR_EXE);
    if exe.exists() {
        Ok(exe)
    } else {
        // Development fallback: sidecar built by desktop/sidecar/build.ps1.
        let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("sidecar")
            .join("dist")
            .join("find-yourself-backend")
            .join(SIDECAR_EXE);
        if dev.exists() {
            Ok(dev)
        } else {
            Err(format!(
                "backend sidecar not found at {}. Build it first:\n  \
                 powershell -ExecutionPolicy Bypass -File desktop/sidecar/build.ps1",
                exe.display()
            ))
        }
    }
}

/// Spawn the sidecar and resolve the URL to load, or explain why we cannot.
fn start_backend(app: &tauri::AppHandle) -> Result<String, String> {
    let exe = sidecar_path(app)?;
    let (mut rx, child) = app
        .shell()
        .sidecar(SIDECAR_EXE)
        .map_err(|e| format!("sidecar spawn failed: {e}"))?
        .args(["--host", "127.0.0.1", "--port", "0"])
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;

    if let Some(state) = app.try_state::<Sidecar>() {
        if let Ok(mut slot) = state.0.lock() {
            *slot = Some(child);
        }
    }

    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        // stdout handshake
        while let Ok(event) = rx.try_recv() {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line);
                    if let Some(rest) = text.trim().strip_prefix(READY_PREFIX) {
                        if let Ok(port) = rest.trim().parse::<u16>() {
                            return Ok(format!("http://127.0.0.1:{port}"));
                        }
                    }
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[sidecar] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    return Err(format!(
                        "backend exited before it was ready (code {:?})",
                        payload.code
                    ));
                }
                _ => {}
            }
        }
        // file fallback (windowed builds swallow stdout)
        if let Some(port) = port_from_file() {
            return Ok(format!("http://127.0.0.1:{port}"));
        }
        std::thread::sleep(POLL_INTERVAL);
    }

    Err(format!(
        "backend did not report a port within {}s. Run it manually to see why:\n  {} --port 0",
        READY_TIMEOUT.as_secs(),
        exe.display()
    ))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Sidecar(Mutex::new(None)))
        .manage(BackendUrl(Mutex::new(String::new())))
        .invoke_handler(tauri::generate_handler![open_main_route, hide_pet_window])
        .setup(|app| {
            let handle = app.handle().clone();
            match start_backend(&handle) {
                Ok(url) => {
                    println!("[Find Yourself] backend ready at {url}");
                    // Remember the backend origin for the pet window + menu routing.
                    if let Some(state) = app.try_state::<BackendUrl>() {
                        if let Ok(mut slot) = state.0.lock() {
                            *slot = url.clone();
                        }
                    }
                    if let Some(window) = app.get_webview_window("main") {
                        window.navigate(url.parse()?)?;
                    }
                    // W10-A: transparent, always-on-top, taskbar-less pet window.
                    // It points at the SAME backend origin as the main window so
                    // the pet's fetch to /api/cabin/save carries the guest session.
                    let pet_url = format!("{url}/pet");
                    let pet = WebviewWindowBuilder::new(
                        &handle,
                        "pet",
                        WebviewUrl::External(
                            pet_url.parse().map_err(|e| format!("bad pet url: {e}"))?,
                        ),
                    )
                    .decorations(false)
                    .transparent(true)
                    .always_on_top(true)
                    .skip_taskbar(true)
                    .resizable(false)
                    .focused(false)
                    .inner_size(110.0, 160.0);
                    if let Err(e) = pet.build() {
                        // Pet window is a nicety, not a hard dependency: a failure
                        // must not take down the whole app.
                        eprintln!("[Find Yourself] pet window not opened: {e}");
                    }
                }
                Err(err) => {
                    // Honest failure: tell the user what broke instead of
                    // showing a blank window.
                    eprintln!("[Find Yourself] {err}");
                    if let Some(window) = app.get_webview_window("main") {
                        let html = format!(
                            "<html><body style=\"font-family:system-ui;padding:2rem\">\
                             <h2>后端未能启动</h2><pre style=\"white-space:pre-wrap\">{}</pre>\
                             </body></html>",
                            err.replace('&', "&amp;").replace('<', "&lt;")
                        );
                        window.navigate(format!("data:text/html;charset=utf-8,{}", html).parse()?)?;
                    }
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // "关闭 = 退出" (任务书 §2.1): the backend must not outlive the UI.
            if let WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Sidecar>() {
                    state.kill();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Find Yourself application")
        .run(|_app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) = _app.try_state::<Sidecar>() {
                    state.kill();
                }
            }
        });
}

#[cfg(not(mobile))]
fn main() {
    run()
}
