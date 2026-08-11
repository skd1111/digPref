"""Safety package — sqlglot-based validation, dangerous-op blocker,
read-only account enforcement.
"""

from mcp_server_database.safety import (  # noqa: F401
    dangerous_ops,
    readonly_enforce,
    sqlglot_validator,
)
