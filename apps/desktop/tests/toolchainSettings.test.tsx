/**
 * ToolchainSettingsPanel 前端回归 —— 「工具链与编译」面板。
 *
 * 2026-08-28 合并自原「工具链」+「编译配置」两个页签：
 *   - 区块一（Agent toolchain.json）：工具可执行文件路径，供智能体验证器
 *   - 区块二（Rust compile.json）：编译器目录 + 产物输出，供文件树右键编译（离线可用）
 *
 * 覆盖：
 *   - 两区块各自加载并展示已有配置
 *   - 区块一保存 → saveToolchain 收到完整 paths
 *   - 区块二保存 → compileConfigSave 收到 javac 目录 + 输出目录（原编译配置面板的回归用例）
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getToolchain = vi.fn();
const saveToolchain = vi.fn();
const compileConfigGet = vi.fn();
const compileConfigSave = vi.fn();
const getWorkspace = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    getToolchain: (...args: unknown[]) => getToolchain(...args),
    saveToolchain: (...args: unknown[]) => saveToolchain(...args),
    compileConfigGet: (...args: unknown[]) => compileConfigGet(...args),
    compileConfigSave: (...args: unknown[]) => compileConfigSave(...args),
    getWorkspace: (...args: unknown[]) => getWorkspace(...args),
  },
}));

vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn() }));

import { ToolchainSettingsPanel } from '@/views/settings/ToolchainSettingsPanel';

function seed(): void {
  // mockReset 而非 mockClear：避免未消费的 mockResolvedValueOnce 污染后续用例
  getToolchain.mockReset();
  saveToolchain.mockReset();
  compileConfigGet.mockReset();
  compileConfigSave.mockReset();
  getWorkspace.mockReset();
  getToolchain.mockResolvedValue({ paths: { python: 'D:/py/python.exe' } });
  saveToolchain.mockImplementation(async (paths: unknown) => ({ paths }));
  compileConfigGet.mockResolvedValue({
    javac_dir: 'C:/jdk/bin',
    python_dir: '',
    gcc_dir: '',
    output_dir: '',
  });
  compileConfigSave.mockImplementation(async (cfg: unknown) => cfg);
  getWorkspace.mockResolvedValue({ path: 'C:/ws', custom: null, default: 'C:/ws' });
}

describe('ToolchainSettingsPanel（工具链与编译，合并页签）', () => {
  it('两区块加载并展示已有配置', async () => {
    seed();
    render(<ToolchainSettingsPanel />);
    await waitFor(() => expect(getToolchain).toHaveBeenCalled());
    await waitFor(() => {
      // 区块一：工具路径
      expect((screen.getByDisplayValue('D:/py/python.exe') as HTMLInputElement).value).toBe(
        'D:/py/python.exe',
      );
      // 区块二：编译配置
      expect((screen.getByDisplayValue('C:/jdk/bin') as HTMLInputElement).value).toBe(
        'C:/jdk/bin',
      );
    });
    // workspace 提示（输出目录默认基座）
    await waitFor(() => expect(screen.getByText(/当前工作空间/)).toBeTruthy());
  });

  it('区块一保存：saveToolchain 收到完整 paths', async () => {
    seed();
    render(<ToolchainSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('D:/py/python.exe')).toBeTruthy());

    const pyInput = screen.getByDisplayValue('D:/py/python.exe') as HTMLInputElement;
    fireEvent.change(pyInput, { target: { value: 'D:/py312/python.exe' } });

    // 第一个「保存」按钮属于区块一（工具路径）
    fireEvent.click(screen.getAllByText('保存')[0]);
    await waitFor(() => expect(saveToolchain).toHaveBeenCalledTimes(1));
    expect(saveToolchain.mock.calls[0][0]).toMatchObject({ python: 'D:/py312/python.exe' });
    await waitFor(() =>
      expect(screen.getByText(/未填写的工具将按 PATH \/ 常见安装目录自动探测/)).toBeTruthy(),
    );
  });

  it('区块二保存：compileConfigSave 收到 javac 目录 + 输出目录', async () => {
    seed();
    render(<ToolchainSettingsPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('C:/jdk/bin')).toBeTruthy());

    // 填输出目录后保存（第二个「保存」按钮属于区块二）
    const outInput = screen.getByPlaceholderText('留空 = 工作空间 workspace/compiled');
    fireEvent.change(outInput, { target: { value: 'D:/out' } });
    fireEvent.click(screen.getAllByText('保存')[1]);

    await waitFor(() => expect(compileConfigSave).toHaveBeenCalledTimes(1));
    expect(compileConfigSave.mock.calls[0][0]).toMatchObject({
      javac_dir: 'C:/jdk/bin',
      output_dir: 'D:/out',
    });
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeTruthy());
  });
});
