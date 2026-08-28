/**
 * McpSettingsPanel 前端回归 —— 设置页「MCP」面板。
 *
 * 覆盖：
 *   - 加载并展示已注册 server（命令行 + keyring 占位符标记）
 *   - 模板一键添加（联网搜索）→ 列表出现新条目且保存按钮可用
 *   - 保存整表 → mcpConfigSave 收到完整 servers
 *   - 连通性测试结果回显（成功 / 失败）
 *   - 删除条目后保存同步生效
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';

const mcpConfigGet = vi.fn();
const mcpConfigSave = vi.fn();
const mcpConfigTest = vi.fn();
const mcpConfigReload = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    mcpConfigGet: (...args: unknown[]) => mcpConfigGet(...args),
    mcpConfigSave: (...args: unknown[]) => mcpConfigSave(...args),
    mcpConfigTest: (...args: unknown[]) => mcpConfigTest(...args),
    mcpConfigReload: (...args: unknown[]) => mcpConfigReload(...args),
  },
}));

import { McpSettingsPanel } from '@/views/settings/McpSettingsPanel';

function seed(servers: Record<string, unknown> = {}): void {
  mcpConfigGet.mockClear();
  mcpConfigSave.mockClear();
  mcpConfigTest.mockClear();
  mcpConfigReload.mockClear();
  mcpConfigGet.mockResolvedValue({ path: 'D:/mcp.yaml', exists: true, servers });
  mcpConfigSave.mockResolvedValue({ ok: true, servers });
  mcpConfigReload.mockResolvedValue({ ok: true, servers: Object.keys(servers) });
}

describe('McpSettingsPanel', () => {
  it('加载并展示已注册 server（含 keyring 占位符标记）', async () => {
    seed({
      websearch: {
        command: 'uvx',
        args: ['duckduckgo-mcp-server'],
        env: {},
        allowed_tools: [],
        auto_start: false,
        working_dir: null,
      },
      brave: {
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-brave-search'],
        env: { BRAVE_API_KEY: '__KEYRING_REF:mcp.brave.api_key__' },
        allowed_tools: [],
        auto_start: false,
        working_dir: null,
      },
    });
    const { getByText } = render(<McpSettingsPanel />);
    await waitFor(() => expect(mcpConfigGet).toHaveBeenCalled());
    await waitFor(() => {
      expect(getByText('websearch')).toBeTruthy();
      expect(getByText('brave')).toBeTruthy();
    });
    // keyring 占位符值不明文展示，只显示 🔒 标记
    expect(getByText(/BRAVE_API_KEY=/).textContent).toContain('keyring');
  });

  it('模板一键添加联网搜索 → 保存收到完整 servers', async () => {
    seed({});
    const { getByText } = render(<McpSettingsPanel />);
    await waitFor(() => expect(mcpConfigGet).toHaveBeenCalled());

    fireEvent.click(getByText('＋ DuckDuckGo 搜索'));
    await waitFor(() => expect(getByText('websearch')).toBeTruthy());

    fireEvent.click(getByText('💾 保存'));
    await waitFor(() => {
      expect(mcpConfigSave).toHaveBeenCalledWith({
        websearch: {
          command: 'uvx',
          args: ['duckduckgo-mcp-server'],
          env: {},
          allowed_tools: [],
          auto_start: false,
          working_dir: null,
        },
      });
    });
  });

  it('连通性测试结果回显（成功）', async () => {
    seed({
      websearch: {
        command: 'uvx',
        args: ['duckduckgo-mcp-server'],
        env: {},
        allowed_tools: [],
        auto_start: false,
        working_dir: null,
      },
    });
    mcpConfigTest.mockResolvedValue({
      ok: true,
      tools: [{ name: 'search', description: 'web search' }],
    });
    const { getByText } = render(<McpSettingsPanel />);
    await waitFor(() => expect(mcpConfigGet).toHaveBeenCalled());

    fireEvent.click(getByText('🔌 测试连接'));
    await waitFor(() => {
      expect(getByText(/握手成功，发现 1 个工具/)).toBeTruthy();
    });
  });

  it('连通性测试失败回显错误信息', async () => {
    seed({
      broken: {
        command: 'no-such-cmd',
        args: [],
        env: {},
        allowed_tools: [],
        auto_start: false,
        working_dir: null,
      },
    });
    mcpConfigTest.mockResolvedValue({ ok: false, error: '找不到命令' });
    const { getByText } = render(<McpSettingsPanel />);
    await waitFor(() => expect(mcpConfigGet).toHaveBeenCalled());

    fireEvent.click(getByText('🔌 测试连接'));
    await waitFor(() => {
      expect(getByText(/找不到命令/)).toBeTruthy();
    });
  });

  it('删除条目后保存同步生效', async () => {
    seed({
      obsolete: {
        command: 'old-server',
        args: [],
        env: {},
        allowed_tools: [],
        auto_start: false,
        working_dir: null,
      },
    });
    vi.stubGlobal('confirm', vi.fn(() => true));
    const { getByText } = render(<McpSettingsPanel />);
    await waitFor(() => expect(mcpConfigGet).toHaveBeenCalled());

    fireEvent.click(getByText('删除'));
    await waitFor(() => expect(getByText(/尚未注册任何 MCP server/)).toBeTruthy());

    fireEvent.click(getByText('💾 保存'));
    await waitFor(() => expect(mcpConfigSave).toHaveBeenCalledWith({}));
    vi.unstubAllGlobals();
  });
});
