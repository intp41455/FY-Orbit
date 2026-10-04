// Build script for the Tauri shell. Kept minimal on purpose: tauri-build only
// needs to run generate_context! at build time, and the desktop config is
// committed rather than templated, so there is nothing to inject here.
fn main() {
    tauri_build::build()
}
