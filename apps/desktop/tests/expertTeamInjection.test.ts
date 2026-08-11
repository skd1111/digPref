import { describe, expect, it, vi } from "vitest";
import {
  buildExpertTeamSnippet,
  renderExpertTeamBlock,
  renderSkillBlock,
} from "@/store/chatStore";
import type { ExpertTeam } from "@/types/expertTeam";
import type { Skill } from "@/types/skill";

vi.mock("@/ipc/invoke", () => ({
  ipc: {
    expertTeamsList: vi.fn(),
    expertTeamsSave: vi.fn(),
    expertTeamsDelete: vi.fn(),
    expertTeamsImport: vi.fn(),
    skillsList: vi.fn(),
    skillsSave: vi.fn(),
    skillsDelete: vi.fn(),
    skillsImport: vi.fn(),
  },
  invoke: vi.fn(),
}));

function _team(): ExpertTeam {
  return {
    schema_version: "1.0",
    id: "due_diligence_team",
    name: "尽职调查专家团",
    description: "贷前尽调多专家协同",
    applicable_scenarios: ["对公信贷贷前尽调"],
    trigger_keywords: ["尽调"],
    enabled: true,
    report_template: "",
    members: [
      {
        name: "尽调项目经理",
        role: "统筹整个尽调任务",
        responsibilities: ["判断尽调类型"],
        focus_points: ["关键资料缺失不得进入报告生成"],
        outputs: ["尽调任务书"],
        prompt: "你是尽职调查项目经理。",
      },
      {
        name: "财务分析专家",
        role: "财务分析",
        responsibilities: [],
        focus_points: [],
        outputs: [],
        prompt: "",
      },
    ],
  };
}

describe("专家团上下文注入", () => {
  it("renderExpertTeamBlock 含团名/成员/协作规则", () => {
    const lines = renderExpertTeamBlock(_team());
    const text = lines.join("\n");
    expect(text).toContain("【专家团上下文：尽职调查专家团");
    expect(text).toContain("尽调项目经理（统筹整个尽调任务）");
    expect(text).toContain("财务分析专家（财务分析）");
    expect(text).toContain("角色指令：你是尽职调查项目经理。");
    expect(text).toContain("协同工作");
  });

  it("buildExpertTeamSnippet 选中为空返回空串", () => {
    expect(buildExpertTeamSnippet([], [_team()])).toBe("");
  });

  it("buildExpertTeamSnippet 多团拼接且跳过未知 id", () => {
    const snippet = buildExpertTeamSnippet(
      ["due_diligence_team", "ghost_team"],
      [_team()],
    );
    expect(snippet).toContain("尽职调查专家团");
    expect(snippet).not.toContain("ghost_team");
  });

  it("renderSkillBlock 输出专家团预设/材料/交付物段落", () => {
    const skill: Skill = {
      schema_version: "1.0",
      id: "ops_due_diligence",
      name: "尽职调查业务",
      description: "",
      version: "1.0",
      author: "",
      tags: [],
      risk_level: "medium",
      enabled: true,
      trigger_keywords: [],
      mcp_servers: [],
      allowed_tools: [],
      role: "utility",
      system_prompt: "你是尽调业务助手。",
      few_shot_examples: [],
      required_expert_team_ids: ["due_diligence_team"],
      materials: ["营业执照", "近6个月银行流水"],
      deliverables: ["尽调报告初稿", "风险清单"],
      source_path: "",
      loaded_at: Date.now(),
      validation_errors: [],
    };
    const text = renderSkillBlock(skill).join("\n");
    expect(text).toContain("默认专家团：due_diligence_team");
    expect(text).toContain("办理材料清单：营业执照、近6个月银行流水");
    expect(text).toContain("最终交付物：尽调报告初稿、风险清单");
  });

  it("renderSkillBlock 对缺省三字段不输出对应段落（向后兼容）", () => {
    const skill = {
      schema_version: "1.0",
      id: "old_skill",
      name: "旧技能",
      system_prompt: "x",
      few_shot_examples: [],
    } as unknown as Skill;
    const text = renderSkillBlock(skill).join("\n");
    expect(text).not.toContain("默认专家团");
    expect(text).not.toContain("办理材料清单");
    expect(text).not.toContain("最终交付物");
  });
});
