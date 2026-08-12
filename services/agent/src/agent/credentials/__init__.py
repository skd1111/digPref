"""Credential vault bridge — talks to the Tauri Rust side via local HTTP.

In production the Agent and Tauri share the OS keychain. During
standalone (non-Tauri) development we read from ~/.eaide/.env.yaml.
"""