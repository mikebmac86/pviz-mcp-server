# mcp-server/auth_context.py
from __future__ import annotations

from contextvars import ContextVar
from typing import Dict, Optional

# Request-scoped bearer (set by pviz_mcp_http middleware)
PVIZ_REQUEST_BEARER: ContextVar[Optional[str]] = ContextVar("PVIZ_REQUEST_BEARER", default=None)

# session_id -> bearer (set by pviz_mcp_http, read-only elsewhere)
SESSION_BEARERS: Dict[str, str] = {}
