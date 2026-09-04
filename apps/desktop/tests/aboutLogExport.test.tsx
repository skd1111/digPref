/**
 * AboutSettingPanel 前端回归 —— BUGFIX #193「一键导出全部日志」。
 *
 * 覆盖：
 *   - 渲染出「一键导出全部日志」按钮 + 平台感知的日志路径列表
 *   - 点击按钮 → save() 对话框拿到路径 → ipc.exportAllLogs 被调用且参数正确
 *   - 导出成功 → 展示成功提示（含文件数 / 大小）
 *   - 导出失败 → 展示失败提示
 *   - 用户取消对话框（save 返回 null）→ 不触发 exportAllLogs
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const exportAllLogs = vi.fn();
const saveDialog = vi.fn();
const platformFn = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    exportAllLogs: (...args: unknown[]) => exportAllLogs(...args),
  },
}));

vi.mock('@tauri-apps/plugin-dialog', () => ({
  save: (...args: unknown[]) => saveDialog(...args),
}));

vi.mock('@tauri-apps/plugin-os', () => ({
  platform: () => platformFn(),
}));

import { AboutSettingPanel } from '@/views/settings/AboutSettingPanel';

function seed(): void {
  exportAllLogs.mockReset();
  saveDialog.mockReset();
  platformFn.mockReset();
  platformFn.mockReturnValue('windows');
}

describe('AboutSettingPanel（一键导出全部日志，BUGFIX #193）', () => {
  it('渲染导出按钮 + 平台感知日志路径', () => {
    seed();
    render(<AboutSettingPanel />);
    expect(screen.getByRole('button', { name: /一键导出全部日志/ })).toBeTruthy();
    // Windows 平台展示安装目录路径（文件名在多个 <li>/<span> 出现，用 getAllByText）
    expect(screen.getAllByText(/eaide\.log/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/agent\.log/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/<安装目录>/).length).toBeGreaterThan(0);
  });

  it('macOS 平台展示 Library/Application Support 路径', () => {
    seed();
    platformFn.mockReturnValue('macos');
    render(<AboutSettingPanel />);
    expect(screen.getAllByText(/Library\/Application Support\/eaide/).length).toBeGreaterThan(0);
  });

  it('点击导出 → save 对话框 → exportAllLogs 收到选定路径', async () => {
    seed();
    saveDialog.mockResolvedValue('C:/tmp/eaide-logs-test.zip');
    exportAllLogs.mockResolvedValue({
      ok: true,
      path: 'C:/tmp/eaide-logs-test.zip',
      file_count: 4,
      total_bytes: 20480,
      files: [],
      missing: [],
      data_dir: 'C:/install',
    });

    render(<AboutSettingPanel />);
    fireEvent.click(screen.getByRole('button', { name: /一键导出全部日志/ }));

    await waitFor(() => expect(saveDialog).toHaveBeenCalled());
    await waitFor(() => expect(exportAllLogs).toHaveBeenCalledWith('C:/tmp/eaide-logs-test.zip'));
    // save 对话框带 zip 过滤器
    const opts = saveDialog.mock.calls[0][0] as { filters?: Array<{ extensions: string[] }> };
    expect(opts.filters?.[0]?.extensions).toContain('zip');
  });

  it('导出成功展示成功提示（含文件数与大小）', async () => {
    seed();
    saveDialog.mockResolvedValue('C:/tmp/ok.zip');
    exportAllLogs.mockResolvedValue({
      ok: true,
      path: 'C:/tmp/ok.zip',
      file_count: 5,
      total_bytes: 10240,
      files: [],
      missing: [],
      data_dir: 'C:/install',
    });

    render(<AboutSettingPanel />);
    fireEvent.click(screen.getByRole('button', { name: /一键导出全部日志/ }));

    await waitFor(() => expect(exportAllLogs).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.getByText(/导出成功：C:\/tmp\/ok\.zip/)).toBeTruthy();
      expect(screen.getByText(/包含 5 个文件/)).toBeTruthy();
    });
  });

  it('导出失败展示失败提示', async () => {
    seed();
    saveDialog.mockResolvedValue('C:/tmp/bad.zip');
    exportAllLogs.mockRejectedValue(new Error('disk full'));

    render(<AboutSettingPanel />);
    fireEvent.click(screen.getByRole('button', { name: /一键导出全部日志/ }));

    await waitFor(() => expect(exportAllLogs).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.getByText(/导出失败：disk full/)).toBeTruthy();
    });
  });

  it('用户取消对话框（save 返回 null）→ 不触发 exportAllLogs', async () => {
    seed();
    saveDialog.mockResolvedValue(null);

    render(<AboutSettingPanel />);
    fireEvent.click(screen.getByRole('button', { name: /一键导出全部日志/ }));

    await waitFor(() => expect(saveDialog).toHaveBeenCalled());
    // 给一点时间确认 exportAllLogs 未被调用
    await new Promise((r) => setTimeout(r, 50));
    expect(exportAllLogs).not.toHaveBeenCalled();
  });
});
