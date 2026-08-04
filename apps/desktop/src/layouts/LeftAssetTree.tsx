/**
 * LeftAssetTree — system asset navigation tree.
 * Renders DB connections, REST API specs, SSH hosts, RPA targets
 * as a hierarchical tree; clicking a leaf opens a context panel
 * that lets the user pin it to the chat.
 *
 * 布局逻辑：
 *   - 未导入工程时：SystemAssetTree 独占面板（保持现状）
 *   - 导入工程后：ProjectFileTree 在上方（主区域），SystemAssetTree 折叠到下方
 */
import { useState } from 'react';
import { SystemAssetTree } from '@/components/asset-tree/SystemAssetTree';
import { ProjectFileTree } from '@/components/codenav/ProjectFileTree';
import { useCodeNavStore } from '@/store/codeNavStore';

export function LeftAssetTree(): JSX.Element {
  const hasProjects = useCodeNavStore((s) => (s.openedProjects ?? []).length > 0);
  // 导入工程后，系统资产默认折叠
  const [assetCollapsed, setAssetCollapsed] = useState(true);

  // 未导入工程：保持现状（SystemAssetTree 独占）
  if (!hasProjects) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex-1 overflow-auto p-2">
          <SystemAssetTree />
        </div>
      </div>
    );
  }

  // 已导入工程：工程目录树在上，系统资产折叠在下方
  return (
    <div className="flex h-full flex-col">
      {/* 工程目录树（主区域） */}
      <div className="flex-1 overflow-auto">
        <ProjectFileTree />
      </div>
      {/* 系统资产（底部可折叠区域，默认收起） */}
      <div className="flex-shrink-0" style={{ borderTop: '1px solid #e0e0e0' }}>
        <button
          type="button"
          onClick={() => setAssetCollapsed((v) => !v)}
          className="flex w-full items-center gap-1 px-3 py-[5px] text-left text-[11px] font-semibold uppercase tracking-wide hover:bg-[#2a2d2e]"
          style={{ color: '#bbbbbb' }}
          title={assetCollapsed ? '展开系统资产' : '折叠系统资产'}
        >
          <span className="text-[10px] text-[#c5c5c5]">{assetCollapsed ? '▶' : '▼'}</span>
          <span>系统资产</span>
        </button>
        {!assetCollapsed && (
          <div className="max-h-[240px] overflow-auto p-2">
            <SystemAssetTree />
          </div>
        )}
      </div>
    </div>
  );
}