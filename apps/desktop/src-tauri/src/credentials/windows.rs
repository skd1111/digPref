//! Windows-specific vault hooks.
//! We rely on keyring (Credential Manager). Future: enforce `CRED_PERSIST_LOCAL_MACHINE`.

#[allow(dead_code)]
pub const SERVICE_PREFIX: &str = "EnterpriseAIIde";