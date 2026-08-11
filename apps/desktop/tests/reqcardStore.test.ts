import { beforeEach, describe, expect, it, vi } from "vitest";
import { useReqcardStore } from "@/store/reqcardStore";
import { ipc } from "@/ipc/invoke";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    reqflowCreateBatch: vi.fn(),
    reqflowListBatches: vi.fn(),
    reqflowGenerateCard: vi.fn(),
    reqflowListCards: vi.fn(),
    reqflowCreateCard: vi.fn(),
    reqflowUpdateCard: vi.fn(),
    reqflowDeleteCard: vi.fn(),
    reqflowListCardVersions: vi.fn(),
    reqflowGetCardVersion: vi.fn(),
    reqflowExport: vi.fn(),
  },
}));

const CARD = {
  id: "REQ-1",
  batch_id: "BAT-1",
  project_name: "proj",
  system_name: "订单系统",
  title: "部分取消",
  feature_ids: ["f1"],
  business_value: "",
  change_points: "",
  feasibility: "feasible",
  feasibility_notes: "",
  impact: "",
  external_systems: [],
  priority: "P1",
  status: "draft",
  conversation_summary: "",
  session_id: "",
  approved_by: null,
  approved_at: null,
  version: 1,
  created_by: "",
  created_at: 0,
  updated_at: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  useReqcardStore.setState({
    batches: [],
    batchStats: {},
    currentBatchId: null,
    cards: [],
    selectedCardId: null,
    generating: false,
    saving: false,
    error: null,
    versions: [],
    viewingVersion: null,
    versionSnapshot: null,
    alignment: { active: false, featureIds: [] },
  });
});

describe("reqcardStore", () => {
  it("loadBatches populates list and stats", async () => {
    (ipc.reqflowListBatches as ReturnType<typeof vi.fn>).mockResolvedValue({
      batches: [{ id: "BAT-1", name: "B1", project_name: "proj" }],
      stats: { "BAT-1": { total: 2, done: 1 } },
    });
    await useReqcardStore.getState().loadBatches("proj");
    const s = useReqcardStore.getState();
    expect(s.batches).toHaveLength(1);
    expect(s.batchStats["BAT-1"].done).toBe(1);
  });

  it("createBatch sets current batch", async () => {
    (ipc.reqflowCreateBatch as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "BAT-9",
      name: "新批次",
    });
    (ipc.reqflowListBatches as ReturnType<typeof vi.fn>).mockResolvedValue({
      batches: [],
      stats: {},
    });
    const batch = await useReqcardStore.getState().createBatch("proj", "新批次");
    expect(batch?.id).toBe("BAT-9");
    expect(useReqcardStore.getState().currentBatchId).toBe("BAT-9");
  });

  it("updateCard replaces list item on success", async () => {
    useReqcardStore.setState({ cards: [{ ...CARD }] });
    (ipc.reqflowUpdateCard as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...CARD,
      title: "新标题",
      version: 2,
    });
    const ok = await useReqcardStore
      .getState()
      .updateCard("REQ-1", { title: "新标题" });
    expect(ok).toBe(true);
    const cards = useReqcardStore.getState().cards;
    expect(cards[0].title).toBe("新标题");
    expect(cards[0].version).toBe(2);
  });

  it("updateCard surfaces backend 409 error (illegal transition)", async () => {
    useReqcardStore.setState({ cards: [{ ...CARD }] });
    (ipc.reqflowUpdateCard as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("agent returned 409: illegal status transition: draft -> done"),
    );
    const ok = await useReqcardStore
      .getState()
      .updateCard("REQ-1", { status: "done" });
    expect(ok).toBe(false);
    expect(useReqcardStore.getState().error).toContain("409");
  });

  it("deleteCard removes from list and clears selection", async () => {
    useReqcardStore.setState({ cards: [{ ...CARD }], selectedCardId: "REQ-1" });
    (ipc.reqflowDeleteCard as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    const ok = await useReqcardStore.getState().deleteCard("REQ-1");
    expect(ok).toBe(true);
    const s = useReqcardStore.getState();
    expect(s.cards).toHaveLength(0);
    expect(s.selectedCardId).toBeNull();
  });

  it("generateCardDraft returns draft and toggles generating", async () => {
    (ipc.reqflowGenerateCard as ReturnType<typeof vi.fn>).mockResolvedValue({
      draft: { title: "AI 生成的标题", feasibility: "risky" },
    });
    const draft = await useReqcardStore.getState().generateCardDraft({
      featureIds: ["f1"],
      projectName: "proj",
      conversationSummary: "摘要",
    });
    expect(draft?.title).toBe("AI 生成的标题");
    expect(useReqcardStore.getState().generating).toBe(false);
  });

  it("generateCardDraft failure sets error", async () => {
    (ipc.reqflowGenerateCard as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("agent returned 502: 所有 LLM 后端均不可用"),
    );
    const draft = await useReqcardStore.getState().generateCardDraft({
      featureIds: [],
      projectName: "proj",
      conversationSummary: "x",
    });
    expect(draft).toBeNull();
    expect(useReqcardStore.getState().error).toContain("LLM");
  });

  it("version browsing: viewVersion sets snapshot, backToLatest clears", async () => {
    (ipc.reqflowListCardVersions as ReturnType<typeof vi.fn>).mockResolvedValue({
      card_id: "REQ-1",
      current_version: 3,
      versions: [
        { version: 2, changed_by: "", created_at: 0 },
        { version: 1, changed_by: "", created_at: 0 },
      ],
    });
    await useReqcardStore.getState().loadVersions("REQ-1");
    expect(useReqcardStore.getState().versions).toHaveLength(2);

    (ipc.reqflowGetCardVersion as ReturnType<typeof vi.fn>).mockResolvedValue({
      card_id: "REQ-1",
      version: 1,
      snapshot: { ...CARD, title: "旧标题", version: 1 },
    });
    await useReqcardStore.getState().viewVersion("REQ-1", 1);
    expect(useReqcardStore.getState().viewingVersion).toBe(1);
    expect(useReqcardStore.getState().versionSnapshot?.title).toBe("旧标题");

    useReqcardStore.getState().backToLatest();
    expect(useReqcardStore.getState().viewingVersion).toBeNull();
  });

  it("alignment start/cancel", () => {
    useReqcardStore.getState().startAlignment(["f1", "f2"]);
    expect(useReqcardStore.getState().alignment.active).toBe(true);
    expect(useReqcardStore.getState().alignment.featureIds).toEqual(["f1", "f2"]);
    useReqcardStore.getState().cancelAlignment();
    expect(useReqcardStore.getState().alignment.active).toBe(false);
  });
});
