"""Enterprise AI IDE Agent — control layer.

Public surface:
    - main:           uvicorn entrypoint
    - graph.compile:  compiled LangGraph state machine
    - api:            FastAPI routes (/chat, /approval, /health)
"""

__version__ = "0.1.0"