// Tauri desktop entry. Boots the library crate, registers plugins,
// and starts the event loop.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    eaide_desktop_lib::run()
}