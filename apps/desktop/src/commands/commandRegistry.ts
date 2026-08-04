/**
 * commandRegistry —— 全局命令注册中心（VSCode 风格）。
 *
 * 用法：
 *   1. 每个模块 import `registerCommand` 把自己想暴露的命令注册进来
 *   2. CommandPalette 弹窗组件读取所有命令，按输入过滤，回车执行
 *   3. 状态栏 / 菜单 / 快捷键 也可触发同一命令
 *
 * 命令 id 用 'namespace:action' 形式（避免冲突）：
 *   - chat.newTab
 *   - chat.closeTab
 *   - view.toggleSideBar
 *   - workbench.action.openSettings
 *   - llm.switch.mock
 */
export interface Command {
  id: string;
  title: string;
  /** 第二行：快捷键、来源模块 */
  description?: string;
  category?: string;
  /** VSCode 风格：showCommandPalette 时是否默认隐藏 */
  when?: () => boolean;
  run: () => void | Promise<void>;
}

const commands = new Map<string, Command>();

export function registerCommand(cmd: Command): () => void {
  commands.set(cmd.id, cmd);
  return () => commands.delete(cmd.id);
}

export function registerCommands(cmds: Command[]): () => void {
  cmds.forEach(registerCommand);
  return () => cmds.forEach((c) => commands.delete(c.id));
}

export function getAllCommands(): Command[] {
  return [...commands.values()];
}

export function runCommand(id: string): void {
  const cmd = commands.get(id);
  if (!cmd) {
    console.warn(`[commands] unknown id: ${id}`);
    return;
  }
  if (cmd.when && !cmd.when()) return;
  void cmd.run();
}
