//! macOS-specific vault hooks. Currently we delegate to keyring (Keychain).
//! Future: hard-code `kSecAttrAccessible = kSecAttrAccessibleWhenUnlockedThisDeviceOnly`.

#[allow(dead_code)]
pub const SERVICE_PREFIX: &str = "com.eaide.desktop";