/**
 * credentialPreview.test.tsx —— 凭证预览面板测试（2026-11-09）。
 *
 * 覆盖需求里的四件事：
 *   1. 展示本业务需要哪些凭证节点 + 节点下是否真的传了；
 *   2. 点已录入的要素名 → 影像放大并高亮对应区域；
 *   3. 未归属材料可拖动挂载（跨专家拒绝，避免悄悄改审核人）；
 *   4. 业务汇总页只读回看（无归类/拖动入口）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CredentialPreviewModal } from "@/components/ops/CredentialPreviewModal";
import { useCredentialStore } from "@/store/credentialStore";
import { ipc } from "@/ipc/invoke";
import type { CredentialView } from "@/types/ops";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    opsCaseCredentials: vi.fn(),
    opsCaseClassifyCredentials: vi.fn(),
    opsCredentialAssetGet: vi.fn(),
    opsCredentialAssetAssign: vi.fn(),
    opsCredentialAssetRecognize: vi.fn(),
    opsCredentialAssetDelete: vi.fn(),
  },
}));

const LICENSE = "营业执照（副本）";
const APPLY = "开户/变更申请书";
const SEAL = "客户告知与授权签署件";

const blocks = [
  {
    text: "企业名称：某某贸易有限公司",
    bbox: [
      [10, 20],
      [310, 20],
      [310, 44],
      [10, 44],
    ],
    confidence: 0.91,
  },
  {
    text: "9133 0100",
    bbox: [
      [140, 60],
      [220, 60],
      [220, 84],
      [140, 84],
    ],
    confidence: 0.55,
  },
];

function asset(
  id: string,
  name: string,
  credential: string,
  memberKey: string,
  hasOcr = true,
) {
  return {
    kind: "file" as const,
    id,
    case_id: "bank__ops_open",
    team_id: "biz_process_team",
    member_key: memberKey,
    credential,
    classified_by: credential ? ("keyword" as const) : ("" as const),
    file_name: name,
    status: "passed",
    source: "upload",
    draft_id: "",
    review_note: "",
    is_image: true,
    has_ocr: hasOcr,
    ocr: {
      engine: hasOcr ? "rapidocr" : "none",
      width: hasOcr ? 400 : 0,
      height: hasOcr ? 300 : 0,
      blocks: hasOcr ? 2 : 0,
      confidence: hasOcr ? 0.8 : 0,
      elapsed_ms: 1200,
      error: "",
    },
    created_at: 1730000000,
  };
}

const VIEW: CredentialView = {
  case_id: "bank__ops_open",
  stats: {
    required_total: 3,
    required_ok: 1,
    missing: [
      { member_key: "材料清单专家", credential: APPLY },
      { member_key: "办理合规质检专家", credential: SEAL },
    ],
    has_preset: true,
    asset_total: 2,
    unassigned_total: 1,
  },
  teams: [
    {
      team_id: "biz_process_team",
      team_name: "业务办理专家团",
      members: [
        {
          member_key: "材料清单专家",
          has_preset: true,
          credentials: [
            {
              name: LICENSE,
              required: true,
              media: "image",
              hint: "四角完整、统一社会信用代码可读",
              keywords: ["营业执照", "执照"],
              satisfied: true,
              assets: [asset("CF-1", "营业执照.jpg", LICENSE, "材料清单专家")],
              field_values: [
                {
                  name: "corp_name",
                  label: "企业名称",
                  value: "某某贸易有限公司",
                  source: "ocr",
                  asset_id: "CF-1",
                  asset_kind: "file",
                  asset_credential: LICENSE,
                  confidence: 0.91,
                  can_locate: true,
                },
                {
                  name: "credit_code",
                  label: "统一社会信用代码",
                  value: "91330100MA2ABCDX1",
                  source: "ai",
                  asset_id: "CF-1",
                  asset_kind: "file",
                  asset_credential: LICENSE,
                  confidence: 0.55,
                  can_locate: true,
                },
                {
                  name: "remark",
                  label: "备注",
                  value: "线下核对无误",
                  source: "manual",
                  asset_id: "DR-1",
                  asset_kind: "draft",
                  asset_credential: "",
                  confidence: 0,
                  can_locate: false,
                },
              ],
              fields_missing: [{ name: "valid_to", label: "营业期限" }],
            },
            {
              name: APPLY,
              required: true,
              media: "image",
              hint: "",
              keywords: [],
              satisfied: false,
              assets: [],
              field_values: [],
              fields_missing: [],
            },
          ],
          unassigned: [
            asset("CF-9", "扫描件_001.jpg", "", "材料清单专家", false),
          ],
        },
        {
          member_key: "办理合规质检专家",
          has_preset: true,
          credentials: [
            {
              name: SEAL,
              required: true,
              media: "image",
              hint: "",
              keywords: [],
              satisfied: false,
              assets: [],
              field_values: [],
              fields_missing: [],
            },
          ],
          unassigned: [],
        },
      ],
    },
  ],
};

function renderModal(props?: { readOnly?: boolean }): void {
  render(
    <CredentialPreviewModal
      caseId="bank__ops_open"
      teamIds={["biz_process_team"]}
      readOnly={props?.readOnly ?? false}
      onClose={() => undefined}
    />,
  );
}

/** jsdom 没有 DataTransfer，给个够用的桩（handler 还会回退到组件内 dragAssetId） */
function makeDataTransfer(id: string): DataTransfer {
  return {
    setData: vi.fn(),
    getData: () => id,
    effectAllowed: "move",
  } as unknown as DataTransfer;
}

/** 左侧节点行（节点名同时出现在右侧抬头，靠 role=button 容器区分） */
function nodeRow(name: string): HTMLElement {
  const el = screen
    .getAllByText(name)
    .find((e) => e.closest("[role='button']") !== null);
  if (!el) throw new Error(`节点未找到：${name}`);
  return el as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  useCredentialStore.getState().reset();
  (ipc.opsCaseCredentials as ReturnType<typeof vi.fn>).mockResolvedValue(VIEW);
  (ipc.opsCredentialAssetGet as ReturnType<typeof vi.fn>).mockResolvedValue({
    kind: "file",
    id: "CF-1",
    file_name: "营业执照.jpg",
    mime: "image/png",
    content_base64: "AAAB",
    ocr: {
      engine: "rapidocr",
      width: 400,
      height: 300,
      blocks,
      confidence: 0.8,
    },
  });
  (ipc.opsCredentialAssetAssign as ReturnType<typeof vi.fn>).mockResolvedValue({
    ok: true,
  });
  (
    ipc.opsCredentialAssetRecognize as ReturnType<typeof vi.fn>
  ).mockResolvedValue({
    ok: true,
    asset_id: "CF-9",
    ocr: { engine: "rapidocr", width: 400, height: 300, blocks },
  });
  (
    ipc.opsCaseClassifyCredentials as ReturnType<typeof vi.fn>
  ).mockResolvedValue(VIEW);
});

describe("凭证预览 · 节点与上传状态", () => {
  it("列出预设凭证节点，并标出已传 / 缺失", async () => {
    renderModal();
    await waitFor(() =>
      expect(screen.getAllByText(LICENSE).length).toBeGreaterThan(0),
    );
    expect(nodeRow(APPLY)).toBeTruthy();
    expect(nodeRow(SEAL)).toBeTruthy();
    // 齐备度与缺失提示（必传 1/3 已齐 + 缺 2 张）
    expect(screen.getByText(/必传凭证 1\/3 已齐/)).toBeTruthy();
    expect(screen.getByText(/缺 2 张/)).toBeTruthy();
    // 已挂到节点的材料出现在资产条里
    expect(screen.getByText("营业执照.jpg")).toBeTruthy();
  });

  it("未预设凭证清单时给出可操作的说明而不是空白", async () => {
    (ipc.opsCaseCredentials as ReturnType<typeof vi.fn>).mockResolvedValue({
      case_id: "bank__ops_open",
      teams: [],
      stats: {
        required_total: 0,
        required_ok: 0,
        missing: [],
        has_preset: false,
        asset_total: 0,
        unassigned_total: 0,
      },
    });
    renderModal();
    await waitFor(() =>
      expect(screen.getByText(/还没有预设凭证清单/)).toBeTruthy(),
    );
  });

  it("选中缺失节点时说明去哪位专家卡上传", async () => {
    renderModal();
    await waitFor(() =>
      expect(screen.getAllByText(LICENSE).length).toBeGreaterThan(0),
    );
    fireEvent.click(nodeRow(APPLY));
    expect(await screen.findByText(/还没有上传/)).toBeTruthy();
    expect(screen.getByText(/「材料清单专家」的卡片下上传材料/)).toBeTruthy();
  });

  it("点顶部「缺 N 张」直接定位到第一张缺失凭证", async () => {
    renderModal();
    const chip = await screen.findByText(/缺 2 张/);
    fireEvent.click(chip.closest("button")!);
    // 右侧切到第一张缺失凭证（开户/变更申请书）并提示未上传
    expect(await screen.findByText(/这张凭证还没有上传/)).toBeTruthy();
    // 抬头 + 左栏各一份
    expect(screen.getAllByText(APPLY).length).toBeGreaterThan(1);
  });
});

describe("凭证预览 · 点要素名定位高亮", () => {
  it("点「企业名称」→ 拉影像、画高亮框并给定位提示", async () => {
    renderModal();
    const field = await screen.findByRole("button", { name: "企业名称" });
    fireEvent.click(field);
    await waitFor(() =>
      expect(ipc.opsCredentialAssetGet).toHaveBeenCalledWith("CF-1"),
    );
    const layer = await screen.findByTestId("credential-highlight-layer");
    const rect = layer.querySelector("rect");
    expect(rect).not.toBeNull();
    expect(rect?.getAttribute("x")).toBe("6"); // rect.x - 4 边距
    expect(rect?.getAttribute("width")).toBe("308");
    expect(screen.getByText(/已定位 · 识别原文/)).toBeTruthy();
  });

  it("低置信命中时提示人工核对", async () => {
    renderModal();
    fireEvent.click(
      await screen.findByRole("button", { name: "统一社会信用代码" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("credential-highlight-layer")).toBeTruthy(),
    );
    expect(screen.getByText(/低置信，请核对/)).toBeTruthy();
  });

  it("手动录入的字段不可定位，点下去给明确说明", async () => {
    renderModal();
    const remark = await screen.findByRole("button", { name: "备注" });
    fireEvent.click(remark);
    expect(
      await screen.findByText(/手动录入的，凭证上没有对应区域/),
    ).toBeTruthy();
    expect(screen.queryByTestId("credential-highlight-layer")).toBeNull();
  });

  it("还没有识别块的影像给「立即识别影像」出口", async () => {
    (ipc.opsCredentialAssetGet as ReturnType<typeof vi.fn>).mockResolvedValue({
      kind: "file",
      id: "CF-9",
      file_name: "扫描件_001.jpg",
      mime: "image/png",
      content_base64: "AAAB",
      ocr: { engine: "none", width: 0, height: 0, blocks: [] },
    });
    renderModal();
    fireEvent.click(await screen.findByText("扫描件_001.jpg"));
    const btn = await screen.findByRole("button", { name: /立即识别影像/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(ipc.opsCredentialAssetRecognize).toHaveBeenCalledWith("CF-9"),
    );
  });
});

describe("凭证预览 · 拖动改归属", () => {
  it("把未归属材料拖到同专家的凭证节点 → 调用挂载接口", async () => {
    renderModal();
    await waitFor(() =>
      expect(screen.getByText("扫描件_001.jpg")).toBeTruthy(),
    );
    const chip = screen
      .getByText("扫描件_001.jpg")
      .closest("[draggable]") as HTMLElement;
    const target = nodeRow(LICENSE);
    const dt = makeDataTransfer("CF-9");
    fireEvent.dragStart(chip, { dataTransfer: dt });
    fireEvent.dragOver(target, { dataTransfer: dt });
    fireEvent.drop(target, { dataTransfer: dt });
    await waitFor(() =>
      expect(ipc.opsCredentialAssetAssign).toHaveBeenCalledWith(
        "CF-9",
        LICENSE,
      ),
    );
  });

  it("拖到别的专家的凭证被拒绝（不能悄悄改审核人）", async () => {
    const alertMock = vi
      .spyOn(window, "alert")
      .mockImplementation(() => undefined);
    renderModal();
    const chip = (await screen.findByText("扫描件_001.jpg")).closest(
      "[draggable]",
    ) as HTMLElement;
    const target = nodeRow(SEAL);
    const dt = makeDataTransfer("CF-9");
    fireEvent.dragStart(chip, { dataTransfer: dt });
    fireEvent.drop(target, { dataTransfer: dt });
    await new Promise((r) => setTimeout(r, 0));
    expect(alertMock).toHaveBeenCalled();
    expect(ipc.opsCredentialAssetAssign).not.toHaveBeenCalled();
    alertMock.mockRestore();
  });

  it("「智能归类」按钮走一次归类并重拉聚合", async () => {
    renderModal();
    const btn = await screen.findByRole("button", { name: /智能归类/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(ipc.opsCaseClassifyCredentials).toHaveBeenCalled(),
    );
    // 归类后重新聚合（load 至少被调用两次）
    expect(
      (ipc.opsCaseCredentials as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThanOrEqual(2);
  });
});

describe("凭证预览 · 汇总页只读", () => {
  it("readOnly 时隐藏归类入口且影像卡片不可拖动", async () => {
    renderModal({ readOnly: true });
    await waitFor(() =>
      expect(screen.getAllByText(LICENSE).length).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: /智能归类/ })).toBeNull();
    const chip = screen
      .getByText("营业执照.jpg")
      .closest("[draggable]") as HTMLElement;
    expect(chip.getAttribute("draggable")).not.toBe("true");
    expect(screen.getByText(/历史业务回看（只读）/)).toBeTruthy();
  });
});
