import { beforeEach, describe, expect, it, vi } from "vitest";
import { useExpertTeamStore } from "@/store/expertTeamStore";
import { ipc } from "@/ipc/invoke";
import type { ExpertTeam } from "@/types/expertTeam";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    expertTeamsList: vi.fn(),
    expertTeamsSave: vi.fn(),
    expertTeamsDelete: vi.fn(),
    expertTeamsImport: vi.fn(),
  },
}));

function _team(id: string): ExpertTeam {
  return {
    schema_version: "1.0",
    id,
    name: `${id} 专家团`,
    description: "",
    applicable_scenarios: [],
    trigger_keywords: [],
    enabled: true,
    members: [{ name: "专家", role: "r", responsibilities: [], focus_points: [], outputs: [], prompt: "" }],
    report_template: "",
  };
}

beforeEach(() => {
  useExpertTeamStore.setState({
    teams: [],
    selectedTeamIds: [],
    selectionMode: "auto",
    selectionSource: "",
    editorOpen: false,
    editingTeamId: null,
    importDialogOpen: false,
  });
});

describe("expertTeamStore", () => {
  it("loadTeams populates list", async () => {
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockResolvedValue({
      teams: [_team("due_diligence_team")],
    });
    await useExpertTeamStore.getState().loadTeams();
    expect(useExpertTeamStore.getState().teams).toHaveLength(1);
    expect(useExpertTeamStore.getState().teams[0].id).toBe("due_diligence_team");
  });

  it("loadTeams keeps empty list when backend unavailable", async () => {
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("agent not ready"),
    );
    await useExpertTeamStore.getState().loadTeams();
    expect(useExpertTeamStore.getState().teams).toEqual([]);
  });

  it("applyAutoSelection sets auto mode + source", () => {
    useExpertTeamStore.getState().applyAutoSelection(["t_a"], "preset");
    const s = useExpertTeamStore.getState();
    expect(s.selectedTeamIds).toEqual(["t_a"]);
    expect(s.selectionMode).toBe("auto");
    expect(s.selectionSource).toBe("preset");
  });

  it("selectManually overrides auto; clearSelection restores auto", () => {
    useExpertTeamStore.getState().applyAutoSelection(["t_a"], "llm");
    useExpertTeamStore.getState().selectManually(["t_b"]);
    let s = useExpertTeamStore.getState();
    expect(s.selectedTeamIds).toEqual(["t_b"]);
    expect(s.selectionMode).toBe("manual");
    expect(s.selectionSource).toBe("manual");

    useExpertTeamStore.getState().clearSelection();
    s = useExpertTeamStore.getState();
    expect(s.selectedTeamIds).toEqual([]);
    expect(s.selectionMode).toBe("auto");
    expect(s.selectionSource).toBe("");
  });

  it("importTeamYamlText surfaces backend error", async () => {
    (ipc.expertTeamsImport as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("agent returned 409: expert team x already exists"),
    );
    const res = await useExpertTeamStore.getState().importTeamYamlText("id: x");
    expect(res.ok).toBe(false);
    expect(res.error).toContain("409");
  });

  it("importTeamYamlText success reloads teams", async () => {
    (ipc.expertTeamsImport as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    (ipc.expertTeamsList as ReturnType<typeof vi.fn>).mockResolvedValue({
      teams: [_team("imported_team")],
    });
    const res = await useExpertTeamStore.getState().importTeamYamlText("id: imported_team");
    expect(res.ok).toBe(true);
    expect(useExpertTeamStore.getState().teams[0].id).toBe("imported_team");
  });

  it("deleteTeam removes team and clears its selection", () => {
    useExpertTeamStore.setState({ teams: [_team("t_a")], selectedTeamIds: ["t_a"] });
    (ipc.expertTeamsDelete as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    useExpertTeamStore.getState().deleteTeam("t_a");
    const s = useExpertTeamStore.getState();
    expect(s.teams).toEqual([]);
    expect(s.selectedTeamIds).toEqual([]);
  });
});
