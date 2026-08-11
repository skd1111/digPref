/**
 * ReqCardsRightPanel —— Phase 2H 开发模式「系统功能点」子模式的右侧面板。
 *
 * 用户要求：当是系统功能点（支持搜索）时，右侧就是需求卡片。
 * 展示当前选中功能点的关联需求卡片（reqflow），并支持一键发起改造需求。
 */
import { useEffect, useState } from "react";
import { useBiznavStore, selectSelectedFeature } from "@/store/biznavStore";
import { useReqcardStore } from "@/store/reqcardStore";
import { useChatStore } from "@/store/chatStore";
import { useUIStore } from "@/store/uiStore";
import { ipc } from "@/ipc/invoke";
import { STATUS_META, type CardStatus } from "@/types/reqcard";

interface RelatedCard {
  id: string;
  title: string;
  status: CardStatus;
  batch_id: string;
  priority: string;
  version: number;
}

export function ReqCardsRightPanel(): JSX.Element {
  const feature = useBiznavStore(selectSelectedFeature);
  const openEditor = useBiznavStore((s) => s.openEditor);
  const [cards, setCards] = useState<RelatedCard[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!feature) {
      setCards([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    ipc
      .reqflowListCards({ featureId: feature.id })
      .then((r) => {
        if (cancelled) return;
        setCards(
          (r.cards ?? []).map((c) => ({
            id: String(c.id),
            title: String(c.title),
            status: c.status as CardStatus,
            batch_id: String(c.batch_id),
            priority: String(c.priority ?? "P2"),
            version: Number(c.version ?? 1),
          })),
        );
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [feature?.id]);

  /** 打开需求卡片：切到批次并跳需求工作台 */
  const handleOpenCard = async (c: RelatedCard): Promise<void> => {
    const store = useReqcardStore.getState();
    await store.selectBatch(c.batch_id);
    useReqcardStore.setState({ selectedCardId: c.id });
    useUIStore.getState().setActivityId("requirements");
  };

  /** 一键发起改造需求（写入对话上下文 + 进入对齐模式） */
  const handleStartReq = (): void => {
    if (!feature) return;
    useChatStore.getState().setAlignmentFeatures([
      {
        feature_id: feature.id,
        feature_name: feature.name,
        feature_description: feature.description,
        ...(feature.skill_id != null ? { skill_id: feature.skill_id } : {}),
        related_files: feature.related_files,
        related_apis: feature.related_apis,
        related_tables: feature.related_tables,
        business_rules: feature.business_rules,
        source: feature.source,
      },
    ]);
    useReqcardStore
      .getState()
      .startAlignment([feature.id], useBiznavStore.getState().projectName);
  };

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: "#f3f3f3" }}
    >
      {/* 当前功能点 */}
      <div
        className="flex-shrink-0 border-b p-3"
        style={{ borderColor: "#d4d4d4", backgroundColor: "#ffffff" }}
      >
        {feature ? (
          <>
            <div className="mb-1 text-2xs" style={{ color: "#616161" }}>
              当前功能点
            </div>
            <div className="text-ui font-semibold" style={{ color: "#1f1f1f" }}>
              🧩 {feature.name}
            </div>
            <div
              className="mt-1 line-clamp-2 text-2xs"
              style={{ color: "#616161" }}
            >
              {feature.description}
            </div>
            <div className="mt-2 flex gap-1.5">
              <button
                type="button"
                onClick={handleStartReq}
                className="flex-1 rounded border px-2 py-1 text-2xs font-semibold"
                style={{
                  borderColor: "#007acc",
                  color: "#0451a5",
                  backgroundColor: "#ffffff",
                }}
                title="把该功能点上下文写入对话，对齐后生成需求卡片"
              >
                📝 发起改造需求
              </button>
              <button
                type="button"
                onClick={() => openEditor(feature.id)}
                className="rounded border px-2 py-1 text-2xs"
                style={{
                  borderColor: "#e0e0e0",
                  color: "#1f1f1f",
                  backgroundColor: "#ffffff",
                }}
                title="编辑功能点（含 Skill 绑定）"
              >
                ✏️
              </button>
            </div>
          </>
        ) : (
          <div className="text-2xs" style={{ color: "#616161" }}>
            从左侧选择一个系统功能点
          </div>
        )}
      </div>

      {/* 关联需求卡片 */}
      <div
        className="flex-shrink-0 border-b px-3 py-2 text-2xs font-semibold uppercase tracking-wider"
        style={{ borderColor: "#d4d4d4", color: "#0451a5" }}
      >
        📋 关联需求卡片{loading ? " …" : ` (${cards.length})`}
      </div>
      <div className="flex-1 overflow-auto p-2">
        {!feature ? (
          <div
            className="px-2 py-6 text-center text-2xs"
            style={{ color: "#616161" }}
          >
            选择功能点后展示其需求卡片
          </div>
        ) : cards.length === 0 ? (
          <div
            className="px-2 py-6 text-center text-2xs"
            style={{ color: "#616161" }}
          >
            暂无需求卡片
            <div className="mt-1">点击「发起改造需求」，AI 对齐后生成卡片</div>
          </div>
        ) : (
          <div className="space-y-1.5">
            {cards.map((c) => {
              const meta = STATUS_META[c.status];
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => void handleOpenCard(c)}
                  className="w-full rounded border p-2 text-left text-2xs transition-colors hover:brightness-95"
                  style={{
                    backgroundColor: "#ffffff",
                    borderColor: "#e0e0e0",
                  }}
                  title="在需求工作台打开"
                >
                  <div className="mb-1 flex items-center gap-1">
                    <span
                      className="font-mono font-semibold"
                      style={{ color: "#0451a5" }}
                    >
                      {c.id}
                    </span>
                    <span
                      className="ml-auto rounded px-1"
                      style={{
                        backgroundColor: `${meta.color}22`,
                        color: meta.color,
                      }}
                    >
                      {meta.icon} {meta.label}
                    </span>
                  </div>
                  <div
                    className="truncate font-semibold"
                    style={{ color: "#1f1f1f" }}
                  >
                    {c.title}
                  </div>
                  <div
                    className="mt-0.5 flex items-center gap-2"
                    style={{ color: "#616161" }}
                  >
                    <span>{c.priority}</span>
                    <span>v{c.version}</span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
