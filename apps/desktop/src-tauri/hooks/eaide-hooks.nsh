; eaide-hooks.nsh —— Tauri NSIS 自定义钩子（安装 + 卸载）
;
; 通过 tauri.conf.json 的 `bundle.windows.nsis.installerHooks` 引用此文件。
; 文件里的宏定义会被自动 include 进生成的 installer.nsi。
;
; 内含三个宏：
;   NSIS_HOOK_PREINSTALL  - 安装前（增量覆盖策略 + 运行检测 + 用户确认 + 快捷方式清理）
;   NSIS_HOOK_POSTINSTALL - 安装后（恢复默认覆盖策略）
;   NSIS_HOOK_POSTUNINSTALL - 卸载后（清 EAIDE 数据目录）

; ============================================================
; PREINSTALL: 运行检测 + 用户确认 + 快捷方式图标缓存清理
; ============================================================
;   功能：
;     1. 检测主程序是否正在运行（文件锁测试）
;     2. 若运行中 → 弹窗提示必须先关闭，询问是否强制关闭
;     3. 用户确定 → 杀进程后继续；取消 → 中止安装
;     4. 清理旧快捷方式 + 刷新 shell 图标缓存

!macro NSIS_HOOK_PREINSTALL
  SetShellVarContext current

  ; ─── 增量安装（v2.109）：未变化的随包文件不重写 ──────────
  ; NSIS 默认 SetOverwrite on 会把全部随包文件（主 exe / eaide-agent.exe /
  ; driver wheels / officecli 二进制）无条件重写一遍，既慢又抹掉未变化文件。
  ; ifdiff 语义：已存在文件时间戳与随包文件相同 → 跳过；更旧或更新 → 覆盖。
  ; （File 命令默认保留源文件时间戳，故「构建产物没变」⇔「时间戳没变」。）
  ; 注意：Agent 运行期写入安装目录的用户资产（skills/ workspace/ *.db /
  ; config/llm-config.json 等）本就不在安装器负载内，升级天然不触碰；
  ; ifdiff 只作用于安装器负载内的文件。
  SetOverwrite ifdiff

  ; ─── 0) 检测主程序是否正在运行 ───────────────────────────
  ; 原理：Windows 对正在运行的 EXE 施加独占文件锁，
  ;       尝试重命名该文件 —— 失败则说明进程正在运行。
  StrCpy $R0 ""  ; 清空标志

  IfFileExists "$INSTDIR\Enterprise AI IDE.exe" 0 eaide_skip_running_check
    ; 尝试重命名（文件锁测试）
    Rename "$INSTDIR\Enterprise AI IDE.exe" "$INSTDIR\_eaide_running_test.tmp"
    IfErrors 0 eaide_not_running
      ; ─── 重命名失败 → 程序正在运行 ───
      StrCpy $R0 "locked"
      MessageBox MB_ICONEXCLAMATION|MB_OKCANCEL|MB_DEFBUTTON1 "检测到 Enterprise AI IDE 正在运行。$\r$\n$\r$\n必须先关闭程序才能进行安装。$\r$\n是否强制关闭程序并继续安装？" /SD IDOK IDOK eaide_do_force_kill
        ; 用户点了"取消" → 中止安装
        Abort
      eaide_do_force_kill:
        ; 用户确认 → 强制结束主程序 + Agent 子进程
        nsExec::ExecToLog 'taskkill /F /IM "Enterprise AI IDE.exe"'
        Pop $0
        nsExec::ExecToLog 'taskkill /F /IM "eaide-agent.exe"'
        Pop $0
        Sleep 800  ; 等进程完全退出释放文件句柄
      Goto eaide_running_check_done
    eaide_not_running:
      ; 重命名成功 → 程序未运行，恢复文件名
      Rename "$INSTDIR\_eaide_running_test.tmp" "$INSTDIR\Enterprise AI IDE.exe"
  eaide_skip_running_check:
  eaide_running_check_done:

  ; ─── 1) 删旧桌面快捷方式（覆盖两种命名）─────────────────
  Delete "$DESKTOP\Enterprise AI IDE.lnk"
  Delete "$DESKTOP\EAIDE.lnk"

  ; ─── 2) 删旧开始菜单快捷方式 ─────────────────────────────
  Delete "$SMPROGRAMS\Enterprise AI IDE.lnk"
  Delete "$SMPROGRAMS\EAIDE.lnk"
  ; 兼容旧的可能在 Programs 子目录的情况
  Delete "$SMPROGRAMS\EAIDE\Enterprise AI IDE.lnk"
  Delete "$SMPROGRAMS\EAIDE\EAIDE.lnk"

  ; ─── 3) 通知 Windows shell 文件已变更 ────────────────────
  ;    SHChangeNotify(SHCNE_ASSOCCHANGED=0x08000000, SHCNF_IDLIST=0x0000, NULL, NULL)
  System::Call "shell32::SHChangeNotify(i 0x08000000, i 0x0000, p 0, p 0)"
!macroend


; ============================================================
; POSTINSTALL: 恢复默认覆盖策略（v2.109）
; ============================================================
;   PREINSTALL 里的 SetOverwrite ifdiff 只应作用于随包文件的 File 释放；
;   安装段收尾处恢复 on，防止策略泄漏到后续步骤 / 未来新增的释放逻辑。
;   （Tauri 模板在 Section Install 末尾 !insertmacro 本宏。）

!macro NSIS_HOOK_POSTINSTALL
  SetOverwrite on
!macroend


; ============================================================
; POSTUNINSTALL: 清掉 EAIDE 写在 %APPDATA%\eaide\ 下的应用数据
; ============================================================
;   内容：audit.sqlite / envs/*.json / index.json / llm-config.json / logs/
;   注意：只删 EAIDE 自己写的目录，不动 %APPDATA% 里其他应用的数据

!macro NSIS_HOOK_POSTUNINSTALL
  SetShellVarContext current

  ; 1) 主数据目录（Windows 标准 %APPDATA%\<bundle-id>）
  RmDir /r "$APPDATA\com.eaide.desktop"

  ; 2) 兼容性回退 —— 旧代码用 %APPDATA%\eaide\（无 com.eaide. 前缀）
  RmDir /r "$APPDATA\eaide"

  ; 3) 如果上面有遗留是文件（不是目录）的情况，单独删
  Delete "$APPDATA\com.eaide.desktop"
  Delete "$APPDATA\eaide"
!macroend
