"""Phase 16 · 思维链可视化与文件操作追踪。

模块组成：
    - models.py    —— ThinkingStep / FileOperation 数据结构
    - diff.py      —— difflib unified diff 计算 + 预览片段 + 行数统计
    - storage.py   —— SQLite thinking_steps 表（trace.db 物理隔离）
    - collector.py —— TraceCollector（LangGraph Hook：记录思考/工具调用/文件操作）
    - api.py       —— FastAPI /trace 三端点

设计原则（架构师 6 忠告）：
    1. 中文思维链靠 Prompt 强制，不靠翻译。
    2. 文件引用正则识别（📄 filename.ext），不确定不强高亮（前端负责）。
    3. diff 用 difflib.unified_diff 后端预计算缓存，禁用第三方库。
    4. 大文件 diff 只存关键片段（前后 50 行）。
    5. 后端不区分模式一律记录（金融合规审计）；前端仅开发模式渲染。
    6. thinking_steps 只追加不删改。
"""
from agent.trace.models import FileOperation, ThinkingStep

__all__ = ["FileOperation", "ThinkingStep"]
