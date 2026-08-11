# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['services\\agent\\src\\agent\\main.py'],
    pathex=['services/agent/src'],
    binaries=[],
    datas=[
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
        ('config/driver', 'config/driver'),
        # 文档审核知识库（风险高亮依据 / 案例库引用；运行时 cwd 缺失时回退 _MEIPASS）
        ('knowledge-base', 'knowledge-base'),
    ],
    hiddenimports=['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.lifespan', 'starlette', 'fastapi', 'httpx', 'pydantic', 'pydantic_settings', 'langgraph', 'mcp', 'aiohttp'],
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
