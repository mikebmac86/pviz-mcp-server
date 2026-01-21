# mcp-server/auth_context.py
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Request-scoped bearer (set by pviz_mcp_http middleware)
PVIZ_REQUEST_BEARER: ContextVar[Optional[str]] = ContextVar("PVIZ_REQUEST_BEARER", default=None)

# Request-scoped session_id (set by pviz_mcp_http middleware)  
PVIZ_SESSION_ID: ContextVar[Optional[str]] = ContextVar("PVIZ_SESSION_ID", default=None)

# Simple in-memory session store (synchronous for easier access)
class SessionStore:
    def __init__(self):
        self._store = {}
    
    def get(self, session_id: str) -> Optional[str]:
        return self._store.get(session_id)
    
    def set(self, session_id: str, bearer: str) -> None:
        self._store[session_id] = bearer
    
    def cleanup(self) -> int:
        # Remove old sessions (implement expiry logic if needed)
        return 0

# session_id -> bearer (set by pviz_mcp_http, read-only elsewhere)
SESSION_BEARERS = SessionStore()