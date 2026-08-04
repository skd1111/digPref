"""LangGraph state-machine package.

State flow:
    intent ─► planner ─► tool_runner ─► hitl_gate ─► repair ─► responder
                  ▲                                  │
                  └────────── interrupt ◄───────────┘  (HITL approve/reject)
"""