# -*- mode: python ; coding: utf-8 -*-
# 跨平台 PyInstaller spec（Windows / macOS 共用）：
#   - 路径一律正斜杠（两平台 Python 都能解析）
#   - 未入 git 的可选数据（如内网驱动 config/driver）缺失时自动跳过
import os


def _data_pairs():
    pairs = [
        ('services/agent/src/agent/llm/prompts', 'agent/llm/prompts'),
        ('services/agent/src/agent/dual/prompts', 'agent/dual/prompts'),
        ('services/agent/src/agent/doc_review/prompts', 'agent/doc_review/prompts'),
        ('services/agent/src/agent/llm/schema.sql', 'agent/llm'),
        ('services/agent/src/agent/audit/schema.sql', 'agent/audit'),
        ('services/agent/src/agent/audit_expert/schema.sql', 'agent/audit_expert'),
        ('services/agent/src/agent/biznav/schema.sql', 'agent/biznav'),
        ('services/agent/src/agent/reqflow/schema.sql', 'agent/reqflow'),
        ('services/agent/src/agent/codenav/schema.sql', 'agent/codenav'),
        ('services/agent/src/agent/dataexpert/schema.sql', 'agent/dataexpert'),
        ('services/agent/src/agent/doc_review/schema.sql', 'agent/doc_review'),
        ('services/agent/src/agent/image_processing/schema.sql', 'agent/image_processing'),
        ('services/agent/src/agent/loganalysis/schema.sql', 'agent/loganalysis'),
        ('services/agent/src/agent/orchestrator/schema.sql', 'agent/orchestrator'),
        ('services/agent/src/agent/preview/schema.sql', 'agent/preview'),
        ('services/agent/src/agent/sessions/schema.sql', 'agent/sessions'),
        ('services/agent/src/agent/ssh/schema.sql', 'agent/ssh'),
        ('services/agent/src/agent/ops/schema.sql', 'agent/ops'),
        ('services/agent/src/agent/datadict/schema.sql', 'agent/datadict'),
        ('services/agent/src/agent/config/llm/speculative.yaml', 'config/llm'),
        # V9.5 内置 Office 生成规范种子 Skill（首次启动播种到数据根 skills/）
        ('services/agent/src/agent/skills/seeds', 'agent/skills/seeds'),
        # 文档审核知识库（风险高亮依据 / 案例库引用；运行时 cwd 缺失时回退 _MEIPASS）
        ('knowledge-base', 'knowledge-base'),
        # NL2SQL 业务字典种子 YAML（运营术语→编码映射；运行时 cwd 优先，缺失回退 _MEIPASS）
        ('config/biz_dict', 'config/biz_dict'),
        # 进程内向量模型（2026-08-31，~23MB）：bge-small-zh-v1.5 ONNX 量化版，
        # 意图语义路由 / Few-Shot 检索 / KB 向量化共用；运行时路径解析同
        # knowledge-base 策略（cwd > _MEIPASS > 仓库根）。未入 git 时自动跳过，
        # 语义路由静默降级回退 LLM 分析，不阻断启动。
        ('model/bge-small-zh-v1.5-onnx', 'model/bge-small-zh-v1.5-onnx'),
        # 内网数据库驱动 wheel（未入 git；CI / macOS 构建缺失时跳过，
        # 对应 DB 驱动走 PyPI 安装的 asyncpg/aiomysql 等）
        ('config/driver', 'config/driver'),
        # V9 OfficeCLI 二进制（未入 git，fetch-officecli.ps1 拉取；缺失时跳过，
        # office 工具族运行时报 officecli_not_installed 友好错误）
        ('vendor/officecli', 'vendor/officecli'),
        # eaide-executor（Rust 本地执行器独立二进制，未入 git；由 build-all.bat /
        # release 构建产出后拷入；缺失时跳过，Agent 保持 Python 原生兜底）
        ('vendor/executor', 'vendor/executor'),
        # 注：V9.7 PPT Master 技能包（~76MB）与嵌入式 Python（~12MB）不进 datas（
        # 避免 onefile exe 膨胀 + 每次启动解压），改由 tauri.conf.json bundle.resources
        # 随安装包落到安装目录（生产模式 Agent cwd = 安装目录，第一级回退即命中）；
        # 开发态走仓库根。缺失时种子 prompt 引导友好提示，不阻断启动。
    ]
    return [(src, dst) for src, dst in pairs if os.path.exists(src)]


a = Analysis(
    ['services/agent/src/agent/main.py'],
    pathex=['services/agent/src'],
    binaries=[],
    datas=_data_pairs(),
    hiddenimports=['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.lifespan', 'starlette', 'fastapi', 'httpx', 'pydantic', 'pydantic_settings', 'langgraph', 'mcp', 'aiohttp', 'onnxruntime', 'tokenizers'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='eaide-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=True：Tauri 父进程通过 Stdio::piped() 管道接管 stdout/stderr，
    # 不会弹出可见控制台窗口。console=False（runw bootloader）在某些环境下
    # 启动即崩溃（无 stdout 可写），故改回 True。
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
