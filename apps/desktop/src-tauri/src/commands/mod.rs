//! Tauri command surface (one module per logical group).
pub mod agent;
pub mod asset;
pub mod audit;
pub mod biznav;        // Phase 2G V1.2
pub mod codenav;       // Phase 2F V0
pub mod credentials;
pub mod dspark;        // Phase 13 V0
pub mod envconfig;
pub mod llm;
pub mod orchestrator;  // Phase 12 V0
pub mod router;        // Phase 2C V0
pub mod shell;
pub mod skills;        // Phase 2D V0
pub mod localai;       // Phase 4 V0
pub mod builtin;       // Phase 1B V1.5
pub mod audit_expert;  // Phase 5 V1
pub mod doc_review;  // 文档风险合规审核
pub mod dataexpert;    // Phase 7 V0
pub mod sessions;      // Phase 6 V1.5
pub mod trace;         // Phase 16 思维链可视化
pub mod preview;       // Phase 15 V0 前端实时预览
pub mod reqflow;       // 运营专家需求改造工作流（需求卡片 V1）
pub mod ops;           // Phase 2H 运营工作台业务记录
pub mod datadict;      // Phase 2H 数据字典
pub mod expert_teams;  // 专家团资产（设置页维护 + 运营模式自动注入）
pub mod compile;       // 文件树右键编译（2026-08-19）
