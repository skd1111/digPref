# vendor/officecli

OfficeCLI 二进制存放目录（V9 Office 能力，2026-08-25）。

- 二进制**不入 git**（见根 `.gitignore`），由拉取脚本生成：

  ```powershell
  .\infra\scripts\fetch-officecli.ps1                    # 最新版 win-x64
  .\infra\scripts\fetch-officecli.ps1 -Version <ver> -Sha256 <hash>
  ```

- 运行期定位（三级回退，`agent/builtin/officecli_runtime.py::resolve_officecli_exe`）：
  1. `EAIDE_BUILTIN_OFFICECLI_EXECUTABLE` 显式覆盖
  2. 本目录捆绑二进制（打包后为 `_MEIPASS/vendor/officecli/`）
  3. `PATH` 中的 `officecli`

- 缺失时 office 工具族返回 `officecli_not_installed` 友好错误，不影响其他功能。
- OfficeCLI 为 Apache 2.0（iOfficeAI/OfficeCLI），运行时强制 `OFFICECLI_SKIP_UPDATE=1`（内网禁外联）。
