//! Linux-specific vault hooks. Requires Secret Service (gnome-keyring / KWallet).
//! In headless / container environments, fall back to an encrypted file vault
//! (TODO: implement via `age`).

#[allow(dead_code)]
pub const SERVICE_PREFIX: &str = "eaide";