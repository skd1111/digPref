/**
 * SecretsSettingPanel — 凭证保险箱配置面板（占位）。
 *
 * 实际 list/get/set 通过 Tauri commands 调 Rust。占位先列出 key 空间。
 */
export function SecretsSettingPanel(): JSX.Element {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">Secrets · 凭证保险箱</h1>
        <p className="mt-1 text-2xs text-fg-muted">
          凭证只通过 OS Keychain 读写，永不落盘。命名空间：<code>db.&lt;name&gt;.dsn</code> /{' '}
          <code>api.&lt;name&gt;.token</code> / <code>ssh.&lt;name&gt;.&lt;kind&gt;</code>。
        </p>
      </header>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          已注册
        </h2>
        <div
          className="rounded p-4 text-2xs text-fg-muted"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          （占位：实际 list 通过 Tauri command <code>credential_list</code> 拿）
        </div>
      </section>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          添加
        </h2>
        <div
          className="rounded p-4 text-2xs text-fg-muted"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          （占位：实际通过 Tauri command <code>credential_set</code> 写 OS Keychain）
        </div>
      </section>
    </div>
  );
}
