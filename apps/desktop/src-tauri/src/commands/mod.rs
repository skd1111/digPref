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
pub mod dataexpert;    // Phase 7 V0
pub mod sessions;      // Phase 6 V1.5
pub mod trace;         // Phase 16 思维链可视化
pub mod preview;       // Phase 15 V0 前端实时预览
