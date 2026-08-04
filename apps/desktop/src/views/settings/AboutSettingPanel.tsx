/**
 * AboutSettingPanel — 关于页。
 */
export function AboutSettingPanel(): JSX.Element {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">About</h1>
        <p className="mt-1 text-2xs text-fg-muted">EAIDE — Enterprise Local AI IDE</p>
      </header>

      <section
        className="rounded p-4 text-ui"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
      >
        <table className="w-full">
          <tbody>
            <Row k="应用版本" v="0.1.0" />
            <Row k="Tauri" v="2.x" />
            <Row k="前端" v="React 18 + TypeScript + Tailwind" />
            <Row k="后端 Agent" v="Python 3.10+ / FastAPI / LangGraph" />
            <Row k="MCP 服务" v="stdio" />
            <Row k="凭证" v="OS Keychain (Keyring crate)" />
            <Row k="审计" v="SQLite (Rust + Python 共享)" />
          </tbody>
        </table>
      </section>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          日志位置
        </h2>
        <div
          className="rounded p-3 font-mono text-2xs"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          %LOCALAPPDATA%\Enterprise AI IDE\logs\
          <ul className="mt-2 space-y-0.5 text-fg-muted">
            <li>· eaide.log — 常规运行</li>
            <li>· crash.log — panic / 致命错误（含 backtrace）</li>
          </ul>
        </div>
      </section>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }): JSX.Element {
  return (
    <tr>
      <td className="py-1 text-fg-muted">{k}</td>
      <td className="py-1">{v}</td>
    </tr>
  );
}
