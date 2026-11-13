/**
 * expertTeamSelector.test.tsx —— 专家团选择器来源标签回归。
 *
 * 用户要求：AI 自动推荐的团不再展示「AI 推荐」四个字（选择器里只剩团名本身）；
 * 其余来源（业务预设 / 手动 / 未选择）仍保留提示，便于区分团是怎么来的。
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    expertTeamsList: vi.fn(),
    expertTeamsRecommend: vi.fn(),
  },
}));

import { ExpertTeamSelector } from "@/components/chat/ExpertTeamSelector";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { useUIStore } from "@/store/uiStore";

const TEAM = {
  schema_version: "1.0",
  id: "corp_account_team",
  name: "对公账户专家团",
  description: "",
  applicable_scenarios: [],
  trigger_keywords: [],
  enabled: true,
  members: [],
  report_template: "",
};

beforeEach(() => {
  useUIStore.setState({ mode: "operator" });
  useExpertTeamStore.setState({
    teams: [TEAM],
    selectedTeamIds: [],
    selectionMode: "auto",
    selectionSource: "",
    selectedForItemId: null,
    recommending: false,
  });
});

describe("专家团选择器来源标签", () => {
  it("AI 推荐来源 → 不展示「AI 推荐」字样", () => {
    useExpertTeamStore.setState({
      selectedTeamIds: [TEAM.id],
      selectionSource: "llm",
    });
    render(<ExpertTeamSelector />);
    expect(screen.queryByText("AI 推荐")).toBeNull();
    expect(screen.getByText("对公账户专家团")).toBeTruthy();
  });

  it("关键词匹配来源 → 同样不展示来源标签", () => {
    useExpertTeamStore.setState({
      selectedTeamIds: [TEAM.id],
      selectionSource: "keyword",
    });
    render(<ExpertTeamSelector />);
    expect(screen.queryByText(/AI 推荐/)).toBeNull();
  });

  it("业务预设来源 → 保留「业务预设」提示", () => {
    useExpertTeamStore.setState({
      selectedTeamIds: [TEAM.id],
      selectionSource: "preset",
    });
    render(<ExpertTeamSelector />);
    expect(screen.getByText("业务预设")).toBeTruthy();
  });

  it("手动选择 → 保留手动提示", () => {
    useExpertTeamStore.setState({
      selectedTeamIds: [TEAM.id],
      selectionMode: "manual",
      selectionSource: "manual",
    });
    render(<ExpertTeamSelector />);
    expect(screen.getByText(/手动 · 1 个团/)).toBeTruthy();
  });
});
