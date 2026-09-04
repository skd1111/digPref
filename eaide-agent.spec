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
        # 注：2026-09-04 起内置 knowledge-base/ 已彻底移除（不再打包、仓库目录删除）——
        # 文档审核「依据」与 ops 制度问答均改走用户上传的本地 RAG 知识库（kb.db + files/，
        # 随数据根迁移）；内置 grep 知识库、财税规则库、audit_doc 演示脚本一并下线。
        # NL2SQL 业务字典种子 YAML（运营术语→编码映射；运行时 cwd 优先，缺失回退 _MEIPASS）
        ('config/biz_dict', 'config/biz_dict'),
        # 进程内向量模型（2026-08-31，~23MB）：bge-small-zh-v1.5 ONNX 量化版，
        # 意图语义路由 / Few-Shot 检索 / KB 向量化共用；运行时路径解析同
        # config/biz_dict 策略（cwd > _MEIPASS > 仓库根）。未入 git 时自动跳过，
        # 语义路由静默降级回退 LLM 分析，不阻断启动。
        ('model/bge-small-zh-v1.5-onnx', 'model/bge-small-zh-v1.5-onnx'),
        # 进程内重排模型（2026-09-03）：bge-reranker ONNX 量化版（cross-encoder），
        # 混合检索召回 Top-N 后深度重排取 Top-K；路径解析同 bge-small 策略。
        # 未入 git / 未下载时自动跳过，reranker 静默 no-op（保持 RRF 序），不阻断启动。
        ('model/bge-reranker-base-onnx', 'model/bge-reranker-base-onnx'),
        # 注：config/driver（内网 DB 驱动 wheel ~139MB）与 vendor/officecli（OfficeCLI
        # 二进制 ~64MB）2026-09-03 起不再进 datas —— 二者已由 tauri.conf.json
        # bundle.resources 随安装包落到安装目录（config/driver、vendor/officecli，与 Agent
        # exe 同级），运行时分别由 driver_bootstrap（exe 所在目录）与
        # officecli_runtime._bundled_candidates（cwd → exe 所在目录）命中；再打进 onefile
        # exe 属重复负载（徒增 ~203MB 且每次启动解压）。缺失时各自友好降级
        # （DB 驱动走 PyPI asyncpg/aiomysql；office 工具报 officecli_not_installed）。
        # eaide-executor（Rust 本地执行器独立二进制 ~0.5MB，未入 git；由 build-all.bat /
        # release 构建产出后拷入；体积小，保留 datas 内副本作为 jsonrpc_stdio 的 _MEIPASS 兜底）
        ('vendor/executor', 'vendor/executor'),
        # 注：2026-09-03 起不再内置 PPT Master（vendor/ppt-master 已整体移出 tauri
        # bundle.resources，PPT 技能改由用户经 /skills/import 自行上传；嵌入式 Python
        # 亦早已移除）。故 spec datas 与 tauri resources 均不再含 ppt-master，安装包瘦身 ~100MB。
    ]
    return [(src, dst) for src, dst in pairs if os.path.exists(src)]


def _jieba_datas():
    # jieba 词典数据文件（dict.txt 等）必须随包，否则打包后分词静默失效 → FTS5 BM25 召回退化。
    try:
        from PyInstaller.utils.hooks import collect_data_files

        return collect_data_files('jieba')
    except Exception:
        return []


def _sqlite_vec_binaries():
    # sqlite-vec 的原生扩展 vec0.dll/.dylib/.so 是包内数据文件，运行时由
    # sqlite_vec.load() 经 conn.load_extension(<pkg>/vec0) 加载，PyInstaller 不会自动收集。
    # 缺失则打包后扩展加载失败 → 全仓向量检索（知识库/语义路由/L2 缓存/财税/NL2SQL）
    # 静默退化为纯关键词（BUGFIX #186）。collect_dynamic_libs 放到 sqlite_vec/ 下，与 loadable_path() 对齐。
    try:
        from PyInstaller.utils.hooks import collect_dynamic_libs

        return collect_dynamic_libs('sqlite_vec')
    except Exception:
        return []


def _pypdfium2_binaries():
    # pypdfium2 内含 pdfium 原生库（.dll/.dylib/.so），扫描件 PDF 栅格化依赖；
    # PyInstaller 不自动收集 → 缺失时扫描件 OCR 回退静默不可用（不阻断正常文本层 PDF）。
    try:
        from PyInstaller.utils.hooks import collect_dynamic_libs

        return collect_dynamic_libs('pypdfium2')
    except Exception:
        return []


def _rapidocr_datas():
    # RapidOCR 的 PP-OCRv4 mobile ONNX 模型（det/rec/cls ~15MB）+ config.yaml 随包分发，
    # 否则打包后端侧 OCR 因找不到模型静默退化 mock（扫描件识别为空）。
    try:
        from PyInstaller.utils.hooks import collect_data_files

        return collect_data_files('rapidocr_onnxruntime')
    except Exception:
        return []


a = Analysis(
    ['services/agent/src/agent/main.py'],
    pathex=['services/agent/src'],
    binaries=_sqlite_vec_binaries() + _pypdfium2_binaries(),
    datas=_data_pairs() + _jieba_datas() + _rapidocr_datas(),
    hiddenimports=['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.lifespan', 'starlette', 'fastapi', 'httpx', 'pydantic', 'pydantic_settings', 'langgraph', 'mcp', 'aiohttp', 'onnxruntime', 'tokenizers', 'jieba', 'sqlite_vec', 'pypdfium2', 'rapidocr_onnxruntime'],
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
