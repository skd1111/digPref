/**
 * BiznavChatBridge —— Phase 2G 跨 store 桥接（headless 订阅者）。
 *
 * 关键设计（spec §2.3 / §5.4）：
 *   - 单向：biznavStore → chatStore。**反向不通**，杜绝 React #300。
 *   - 监听 biznavStore.selectedFeatureId / drawerOpen + uiStore.mode
 *   - mode !== 'operator' → 清 chatStore.selectedFeatureContext
 *   - drawerOpen && selectedId → 写 chatStore.selectedFeatureContext
 *   - 不渲染任何 DOM（return null）
 *
 * 挂在 WorkspaceLayout 顶层（与 pushMock 同模式）。
 */
import { useEffect } from 'react';
import { useBiznavStore } from '@/store/biznavStore';
import { useChatStore } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';

export function BiznavChatBridge(): null {
  const selectedId = useBiznavStore((s) => s.selectedFeatureId);
  const drawerOpen = useBiznavStore((s) => s.drawerOpen);
  const features = useBiznavStore((s) => s.features);
  const setFeatureContext = useChatStore((s) => s.setFeatureContext);
  const mode = useUIStore((s) => s.mode);

  useEffect(() => {
    // mode 切换 / drawer 关闭 → 清空
    if (mode !== 'operator' || !drawerOpen || !selectedId) {
      setFeatureContext(null);
      return;
    }
    // drawer 开 + 有 selectedId → 写入 chat context
    const f = features.find((x) => x.id === selectedId);
    if (!f) {
      setFeatureContext(null);
      return;
    }
    setFeatureContext({
      feature_id: f.id,
      feature_name: f.name,
      feature_description: f.description,
      related_files: f.related_files,
      related_apis: f.related_apis,
      related_tables: f.related_tables,
      business_rules: f.business_rules,
      source: f.source,
    });
  }, [drawerOpen, selectedId, features, mode, setFeatureContext]);

  return null;
}
