/**
 * uiZoom.test.tsx —— 界面缩放（2026-11-09）。
 *
 * 覆盖：三档推进/夹紧、非 Tauri 环境降级 CSS zoom、View 菜单入口、
 * 设置页三档按钮 —— 三个入口写同一份 uiStore.uiZoom（持久化）。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MenuBar } from '@/components/chrome/MenuBar';
import { AdvancedSettingsPanel } from '@/views/settings/AdvancedSettingsPanel';
import { useUIStore } from '@/store/uiStore';
import { applyUiZoom, UI_ZOOM_DEFAULT, UI_ZOOM_MAX, UI_ZOOM_MIN } from '@/lib/uiZoom';

beforeEach(() => {
  useUIStore.setState({ uiZoom: UI_ZOOM_DEFAULT });
  document.documentElement.style.removeProperty('zoom');
});

describe('界面缩放（uiZoom）', () => {
  it('连续可调范围 80%–200%，默认 110%', () => {
    expect(UI_ZOOM_MIN).toBe(0.8);
    expect(UI_ZOOM_MAX).toBe(2);
    expect(UI_ZOOM_DEFAULT).toBe(1.1);
  });

  it('Zoom In / Zoom Out 每次 10%，在 80%–200% 边界夹紧', () => {
    const st = () => useUIStore.getState();
    expect(st().uiZoom).toBe(1.1);
    st().zoomIn();
    expect(st().uiZoom).toBe(1.2);
    st().zoomIn();
    expect(st().uiZoom).toBe(1.3);
    st().setUiZoom(1.95);
    st().zoomIn();
    expect(st().uiZoom).toBe(2); // 1.95 + 0.1 → 夹到 200%
    st().zoomIn(); // 已在顶档，不动
    expect(st().uiZoom).toBe(2);
    st().setUiZoom(0.85);
    st().zoomOut();
    expect(st().uiZoom).toBe(0.8); // 夹到 80%
    st().zoomOut(); // 已在底档，不动
    expect(st().uiZoom).toBe(0.8);
    st().resetZoom();
    expect(st().uiZoom).toBe(UI_ZOOM_DEFAULT);
  });

  it('非法值与超范围被夹回 80%–200%（两位精度）', () => {
    const st = () => useUIStore.getState();
    st().setUiZoom(9);
    expect(st().uiZoom).toBe(2);
    st().setUiZoom(0.1);
    expect(st().uiZoom).toBe(0.8);
    st().setUiZoom(Number.NaN);
    expect(st().uiZoom).toBe(UI_ZOOM_DEFAULT);
    st().setUiZoom(1.234);
    expect(st().uiZoom).toBe(1.23);
  });

  it('非 Tauri 环境降级为 CSS zoom（单测/浏览器预览可验证）', async () => {
    await applyUiZoom(1.25);
    expect(document.documentElement.style.getPropertyValue('zoom')).toBe('1.25');
  });

  it('View 菜单显示当前比例，Zoom In 生效且顶档后置灰', () => {
    render(
      <MemoryRouter>
        <MenuBar />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText('View'));
    expect(screen.getByText(/UI Zoom: 110%/)).toBeTruthy();
    fireEvent.click(screen.getByText('Zoom In'));
    expect(useUIStore.getState().uiZoom).toBe(1.2);

    // 顶档：菜单显示 200%，再点 Zoom In 不应变化（置灰不可点）
    useUIStore.getState().setUiZoom(2);
    fireEvent.click(screen.getByText('View'));
    expect(screen.getByText(/UI Zoom: 200%/)).toBeTruthy();
    fireEvent.click(screen.getByText('Zoom In'));
    expect(useUIStore.getState().uiZoom).toBe(2);

    // Reset Zoom 回默认档（点击会关闭菜单，需重开）
    fireEvent.click(screen.getByText('View'));
    fireEvent.click(screen.getByText('Reset Zoom'));
    expect(useUIStore.getState().uiZoom).toBe(UI_ZOOM_DEFAULT);
  });

  it('设置页滑块与 − / ＋ 按钮均可连续调节', () => {
    render(<AdvancedSettingsPanel />);
    const slider = screen.getByLabelText('界面缩放') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '1.35' } });
    expect(useUIStore.getState().uiZoom).toBe(1.35);
    fireEvent.click(screen.getByText('＋'));
    expect(useUIStore.getState().uiZoom).toBe(1.45);
    fireEvent.click(screen.getByText('−'));
    expect(useUIStore.getState().uiZoom).toBe(1.35);
  });
});
