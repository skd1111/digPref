//! Unit tests for the credential vault.
//!
//! These run on every platform the crate builds on. They will hit the
//! real OS keyring — so CI runners need a working keyring service:
//!   - macOS:  any user session (no setup)
//!   - Windows: any user session
//!   - Linux:  a running gnome-keyring / kwallet；无 Secret Service 的环境
//!     （如 GitHub ubuntu runner）自动跳过真实读写用例

#[cfg(test)]
mod cases {
    use super::super::*;

    fn vault() -> Vault {
        Vault::for_service("com.eaide.desktop.tests")
    }

    fn random_key() -> String {
        format!("test.rand.{}", uuid::Uuid::new_v4())
    }

    /// 探测系统钥匙串是否可用（Linux CI 无 Secret Service 时返回 false）。
    fn keyring_reachable() -> bool {
        let probe_key = random_key();
        let v = vault();
        v.get(&probe_key).is_ok()
    }

    #[test]
    fn roundtrip_get_set() {
        if !keyring_reachable() {
            eprintln!("skipped: no OS keyring on this host");
            return;
        }
        let v = vault();
        let key = random_key();
        // Cleanup any leftover from a previous failed run
        let _ = v.delete(&key);

        assert!(v.get(&key).unwrap().is_none());
        v.set(&key, "hunter2").unwrap();
        assert_eq!(v.get(&key).unwrap().as_deref(), Some("hunter2"));

        // Cleanup
        v.delete(&key).unwrap();
        assert!(v.get(&key).unwrap().is_none());
    }

    #[test]
    fn delete_is_idempotent() {
        if !keyring_reachable() {
            eprintln!("skipped: no OS keyring on this host");
            return;
        }
        let v = vault();
        let key = random_key();
        // No-op delete should not error
        v.delete(&key).unwrap();
    }

    #[test]
    fn overwrite_replaces_value() {
        if !keyring_reachable() {
            eprintln!("skipped: no OS keyring on this host");
            return;
        }
        let v = vault();
        let key = random_key();
        let _ = v.delete(&key);

        v.set(&key, "v1").unwrap();
        v.set(&key, "v2").unwrap();
        assert_eq!(v.get(&key).unwrap().as_deref(), Some("v2"));

        v.delete(&key).unwrap();
    }

    #[test]
    fn rejects_empty_value() {
        let v = vault();
        let key = random_key();
        let res = v.set(&key, "");
        assert!(res.is_err());
    }

    #[test]
    fn rejects_empty_account() {
        let v = vault();
        assert!(v.get("").is_err());
        assert!(v.set("", "x").is_err());
        assert!(v.delete("").is_err());
    }

    #[test]
    fn rejects_unnamespaced_account() {
        let v = vault();
        // No dot → rejected
        assert!(v.get("plainname").is_err());
        assert!(v.set("plainname", "x").is_err());
    }

    #[test]
    fn rejects_oversized_account() {
        let v = vault();
        let huge = format!("a.{}", "x".repeat(200));
        assert!(v.get(&huge).is_err());
    }

    #[test]
    fn rejects_non_ascii_account() {
        let v = vault();
        assert!(v.get("中文.key").is_err());
        assert!(v.get("foo; DROP--").is_err());
    }

    #[test]
    fn accepts_typical_namespaced_key() {
        if !keyring_reachable() {
            eprintln!("skipped: no OS keyring on this host");
            return;
        }
        let v = vault();
        let key = format!("db.orders_pg.dsn.{}", uuid::Uuid::new_v4());
        let _ = v.delete(&key);
        v.set(&key, "postgresql://readonly@db/orders").unwrap();
        assert!(v.get(&key).unwrap().is_some());
        v.delete(&key).unwrap();
    }

    #[test]
    fn list_returns_presence_only() {
        if !keyring_reachable() {
            eprintln!("skipped: no OS keyring on this host");
            return;
        }
        let v = vault();
        let k1 = format!("present.{}", uuid::Uuid::new_v4());
        let k2 = format!("missing.{}", uuid::Uuid::new_v4());
        let _ = v.delete(&k1);
        let _ = v.delete(&k2);

        v.set(&k1, "value").unwrap();
        let result = v.list(&[k1.clone(), k2.clone()]);
        let map: std::collections::HashMap<_, _> = result.into_iter().collect();
        assert_eq!(map.get(&k1).and_then(|v| v.as_deref()), Some("value"));
        assert_eq!(map.get(&k2).map(|v| v.is_none()), Some(true));

        v.delete(&k1).unwrap();
    }
}