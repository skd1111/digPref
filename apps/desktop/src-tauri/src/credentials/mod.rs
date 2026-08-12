//! 跨平台 OS 密钥保险箱封装。
//!
//! 基于 `keyring` crate，内部按平台分派到：
//!   - macOS：   Keychain（`kSecAttrAccessible = WhenUnlockedThisDeviceOnly`）
//!   - Windows： Credential Manager（`CRED_PERSIST_LOCAL_MACHINE`）
//!   - Linux：   通过 D-Bus 的 Secret Service（gnome-keyring / KWallet）
//!
//! 命名约定
//! --------
//! 每个密钥在 OS 保险箱里按 `(service, account)` 存储。我们把
//! `service` 固定为 `"com.eaide.desktop"`（或平台等价物），让 `account`
//! 用一个带点的路径来映射逻辑资源。
//!
//! 示例：
//!     com.eaide.desktop / db.orders_pg.dsn        → orders_pg 的 DSN
//!     com.eaide.desktop / api.jira.token          → Jira API token
//!     com.eaide.desktop / ssh.web1.private_key    → SSH 私钥
//!
//! 不含点的 key 会被直接拒绝——防止随便写一个 "password" 这种通用名，
//! 撞到其他应用。

#[cfg(test)]
mod tests;

use crate::error::{AppError, AppResult};

/// Master service name — used as the first keyring dimension.
pub const SERVICE_NAME: &str = "com.eaide.desktop";


pub struct Vault {
    service: String,
}

impl Vault {
    pub fn for_service(service: impl Into<String>) -> Self {
        Self { service: service.into() }
    }

    pub fn default() -> Self {
        Self::for_service(SERVICE_NAME)
    }

    // ---- Read / write / delete --------------------------------------------

    pub fn get(&self, account: &str) -> AppResult<Option<String>> {
        validate_account(account)?;
        let entry = keyring::Entry::new(&self.service, account)
            .map_err(AppError::from)?;
        match entry.get_password() {
            Ok(v) => Ok(Some(v)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    pub fn set(&self, account: &str, value: &str) -> AppResult<()> {
        validate_account(account)?;
        if value.is_empty() {
            return Err(AppError::Permission(
                "refusing to store empty credential".into(),
            ));
        }
        let entry = keyring::Entry::new(&self.service, account)
            .map_err(AppError::from)?;
        entry.set_password(value).map_err(AppError::from)
    }

    pub fn delete(&self, account: &str) -> AppResult<()> {
        validate_account(account)?;
        let entry = keyring::Entry::new(&self.service, account)
            .map_err(AppError::from)?;
        match entry.delete_credential() {
            Ok(()) => Ok(()),
            Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.into()),
        }
    }

    /// Bulk read used by `credential_list` — returns (account, value) for
    /// every entry under this service. There is no portable "list all
    /// accounts" API in the keyring crate, so we attempt common names
    /// AND let callers pre-register known keys.
    pub fn list(&self, known_accounts: &[String]) -> Vec<(String, Option<String>)> {
        known_accounts
            .iter()
            .map(|a| {
                let value = self.get(a).ok().flatten();
                (a.clone(), value)
            })
            .collect()
    }
}


/// Reject accounts that aren't namespaced (must contain at least one dot,
/// and must be plain ASCII identifier characters).
fn validate_account(account: &str) -> AppResult<()> {
    if account.is_empty() {
        return Err(AppError::Permission("empty account".into()));
    }
    if account.len() > 128 {
        return Err(AppError::Permission("account name too long".into()));
    }
    if !account.contains('.') {
        return Err(AppError::Permission(
            "account name must be namespaced (e.g. 'db.orders_pg.dsn')".into(),
        ));
    }
    if !account
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-')
    {
        return Err(AppError::Permission(
            "account name must be ASCII [a-zA-Z0-9._-]".into(),
        ));
    }
    Ok(())
}