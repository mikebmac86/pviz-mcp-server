"""
HTTP/SSE Transport Wrapper for pviz MCP Server - PATCHED WITH DEBUGGING (Option A)

Option A goal:
  - Hosted MCP does NOT use a static PVIZ_JWT_TOKEN.
  - Instead, it binds the user's Authorization: Bearer <token> to the MCP session_id
    and makes that token available (per-request) to downstream MCP tool handlers
    via a ContextVar.

Notes:
  - This file only handles binding + request-scoped token availability.
  - You still need to update your backend API adapter to read the token from
    the ContextVar instead of env/file.
  - This implementation is single-process / single-replica safe. If you run
    multiple replicas behind a load balancer, you'll need sticky sessions or
    a shared store (Redis) for session_id -> token.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from .auth_context import PVIZ_REQUEST_BEARER, SESSION_BEARERS, PVIZ_SESSION_ID
from .pviz_mcp_server import mcp
from .api_adapter import token_fingerprint


# -----------------------------------------------------------------------------
# Option A: Request-scoped bearer + session binding
# -----------------------------------------------------------------------------

def _now_s() -> float:
    return time.time()


def _parse_bearer(auth_header: str) -> Optional[str]:
    if not auth_header:
        return None
    a = auth_header.strip()
    if not a:
        return None
    if a.lower().startswith("bearer "):
        tok = a.split(" ", 1)[1].strip()
        return tok or None
    return None


# -----------------------------------------------------------------------------
# Env helpers / parsing
# -----------------------------------------------------------------------------

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _split_csv_env(name: str, default: str = "") -> List[str]:
    v = os.getenv(name, default).strip()
    if not v:
        return []
    return [p.strip() for p in v.split(",") if p.strip()]


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _strip_scheme_and_port(host: str) -> str:
    h = (host or "").strip()
    if not h:
        return ""
    if h == "*":
        return "*"

    if "://" in h:
        h = h.split("://", 1)[1]

    h = h.split("/", 1)[0]
    h = h.split("?", 1)[0]
    h = h.split("#", 1)[0]

    if ":" in h:
        h = h.split(":", 1)[0]

    return h.strip()


def _starlette_safe_hosts(hosts: List[str]) -> List[str]:
    cleaned: List[str] = []
    for h in hosts:
        h2 = _strip_scheme_and_port(h)
        if h2:
            cleaned.append(h2)
    return _dedupe_preserve_order(cleaned)


def _expand_host_variants_for_mcp(hosts: List[str]) -> List[str]:
    out: List[str] = []
    for raw in hosts:
        h = (raw or "").strip()
        if not h:
            continue
        out.append(h)

        # Expand bare hosts for MCP TransportSecuritySettings matching behavior
        if ":" not in h and h != "*":
            out.append(f"{h}:*")
            out.append(f"{h}:443")
            out.append(f"{h}:80")

    return _dedupe_preserve_order(out)


# -----------------------------------------------------------------------------
# Debug logging (stderr only)
# -----------------------------------------------------------------------------

DEBUG_HTTP = _bool_env("MCP_DEBUG_HTTP", False)
DEBUG_AUTH = _bool_env("MCP_DEBUG_AUTH_BIND", False)
DEBUG_TRANSPORT_SECURITY = _bool_env("MCP_DEBUG_TRANSPORT_SECURITY", True)


def _log_http(*parts: object) -> None:
    if not DEBUG_HTTP:
        return
    try:
        print("[pviz_mcp_http][http]", *parts, file=sys.stderr, flush=True)
    except Exception:
        pass


def _log_auth(*parts: object) -> None:
    if not DEBUG_AUTH:
        return
    try:
        print("[pviz_mcp_http][auth]", *parts, file=sys.stderr, flush=True)
    except Exception:
        pass


def _log_ts(*parts: object) -> None:
    if not DEBUG_TRANSPORT_SECURITY:
        return
    try:
        print(*parts, file=sys.stderr, flush=True)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# ASGI middleware (SSE-safe): Auth binder
# -----------------------------------------------------------------------------

class MCPAuthBindMiddleware:
    """
    ASGI-native middleware (SSE-safe).

    Binds Authorization: Bearer <token> to session_id (for /mcp/messages calls),
    and sets PVIZ_REQUEST_BEARER contextvar for downstream handling.

    IMPORTANT:
      - Must wrap the MCP sub-app (mounted at /mcp), not the top Starlette app,
        unless you're prepared to lose Starlette methods like add_middleware().
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Extract headers
        hdrs: Dict[str, str] = {}
        for k, v in (scope.get("headers") or []):
            try:
                hdrs[k.decode("latin-1").lower()] = v.decode("latin-1")
            except Exception:
                continue

        auth = hdrs.get("authorization", "")
        bearer = _parse_bearer(auth)

        # Extract session_id from query string
        session_id = ""
        try:
            qs = (scope.get("query_string") or b"").decode("utf-8", errors="replace")
            for part in qs.split("&"):
                if part.startswith("session_id="):
                    session_id = part.split("=", 1)[1]
                    break
        except Exception:
            session_id = ""

        # Bind token when present
        if bearer and session_id:
            SESSION_BEARERS.set(session_id, bearer)  # Remove await

        # Fallback: if token missing but we have session_id, try store
        if (not bearer) and session_id:
            bearer = SESSION_BEARERS.get(session_id)  # Remove await

        token_ctx = PVIZ_REQUEST_BEARER.set(bearer)
        session_ctx = PVIZ_SESSION_ID.set(session_id if session_id else None)

        path = scope.get("path", "")
        if DEBUG_AUTH and (path.endswith("/sse") or path.endswith("/messages") or "/messages" in path):
            _log_auth(
                "AUTH_BIND",
                "path=", path,
                "session_id=", "yes" if bool(session_id) else "no",
                "auth_header=", "yes" if bool(auth) else "no",
                "bearer_bound=", "yes" if bool(bearer) else "no",
            )

        try:
            await self.app(scope, receive, send)
        finally:
            PVIZ_REQUEST_BEARER.reset(token_ctx)
            PVIZ_SESSION_ID.reset(session_ctx)

# -----------------------------------------------------------------------------
# ASGI middleware (SSE-safe): Debug /mcp/messages body without breaking streaming
# -----------------------------------------------------------------------------

class DebugMcpMessagesMiddleware:
    """
    ASGI-native debug middleware that is SAFE for StreamingResponse / SSE.

    Only triggers on /messages (inside the mounted /mcp sub-app).
    """

    def __init__(self, app, enabled: bool = True, max_preview: int = 300) -> None:
        self.app = app
        self.enabled = enabled
        self.max_preview = max_preview

    def _preview_bytes(self, b: bytes) -> str:
        if not b:
            return ""
        bb = b[: self.max_preview]
        try:
            return bb.decode("utf-8", errors="replace")
        except Exception:
            return repr(bb)

async def __call__(self, scope, receive, send) -> None:
    if scope.get("type") != "http":
        await self.app(scope, receive, send)
        return

    # Extract headers
    hdrs: Dict[str, str] = {}
    for k, v in (scope.get("headers") or []):
        try:
            hdrs[k.decode("latin-1").lower()] = v.decode("latin-1")
        except Exception:
            continue

    auth = hdrs.get("authorization", "")
    bearer = _parse_bearer(auth)
    path = scope.get("path", "")

    # For SSE connections to /mcp/sse, we need to intercept the response
    # to extract the session_id from the endpoint event and bind the token
    if path.endswith("/sse") and bearer:
        # Wrap send to intercept SSE events
        original_send = send
        session_id_found = [None]  # Use list for closure mutation
        
        async def intercepting_send(message):
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    text = body.decode("utf-8", errors="replace")
                    # Look for endpoint event with session_id
                    if "event: endpoint" in text or "event:endpoint" in text:
                        for line in text.split("\n"):
                            if "session_id=" in line:
                                # Extract session_id from URL in data line
                                if "session_id=" in line:
                                    sid = line.split("session_id=")[1].split("&")[0].split()[0]
                                    if sid and not session_id_found[0]:
                                        session_id_found[0] = sid
                                        SESSION_BEARERS.set(sid, bearer)
                                        if DEBUG_AUTH:
                                            _log_auth(
                                                "SSE session bind: session_id=", sid[:8],
                                                "token_fp=", token_fingerprint(bearer)[:8]
                                            )
                                break
            await original_send(message)
        
        token_ctx = PVIZ_REQUEST_BEARER.set(bearer)
        session_ctx = PVIZ_SESSION_ID.set(None)
        try:
            await self.app(scope, receive, intercepting_send)
        finally:
            PVIZ_REQUEST_BEARER.reset(token_ctx)
            PVIZ_SESSION_ID.reset(session_ctx)
        return

    # Extract session_id from query string (for /messages endpoint)
    session_id = ""
    try:
        qs = (scope.get("query_string") or b"").decode("utf-8", errors="replace")
        for part in qs.split("&"):
            if part.startswith("session_id="):
                session_id = part.split("=", 1)[1]
                break
    except Exception:
        session_id = ""

    # Bind token when present
    if bearer and session_id:
        SESSION_BEARERS.set(session_id, bearer)

    # Fallback: if token missing but we have session_id, try store
    if (not bearer) and session_id:
        bearer = SESSION_BEARERS.get(session_id)

    token_ctx = PVIZ_REQUEST_BEARER.set(bearer)
    session_ctx = PVIZ_SESSION_ID.set(session_id if session_id else None)

    if DEBUG_AUTH and (path.endswith("/sse") or path.endswith("/messages") or "/messages" in path):
        _log_auth(
            "AUTH_BIND:",
            "path=", path,
            "bearer_present=", bool(bearer),
            "session_id=", session_id[:8] if session_id else "none",
            "token_fp=", token_fingerprint(bearer)[:8] if bearer else "none",
        )

    try:
        await self.app(scope, receive, send)
    finally:
        PVIZ_REQUEST_BEARER.reset(token_ctx)
        PVIZ_SESSION_ID.reset(session_ctx)

# -----------------------------------------------------------------------------
# MCP transport security configuration
# -----------------------------------------------------------------------------

def _configure_mcp_transport_security() -> None:
    _log_ts("=" * 80)
    _log_ts("[pviz_mcp_http] TRANSPORT SECURITY CONFIGURATION (DEBUG)")
    _log_ts("=" * 80)

    allow_any = _bool_env("PVIZ_ALLOW_ANY_HOST", False)
    _log_ts(f"[pviz_mcp_http] PVIZ_ALLOW_ANY_HOST = {allow_any}")

    default_hosts = "mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server"
    default_origins = "https://mcp.pvizgenerator.com"

    allowed_hosts = _split_csv_env("MCP_ALLOWED_HOSTS", default=default_hosts)
    allowed_origins = _split_csv_env("MCP_ALLOWED_ORIGINS", default=default_origins)

    _log_ts(f"[pviz_mcp_http] Initial allowed_hosts: {allowed_hosts}")
    _log_ts(f"[pviz_mcp_http] Initial allowed_origins: {allowed_origins}")

    if allow_any:
        allowed_hosts = ["*"]
        allowed_origins = ["*"]
        _log_ts("[pviz_mcp_http] ALLOW_ANY enabled, overriding to: ['*']")

    allowed_hosts = _expand_host_variants_for_mcp(allowed_hosts)
    _log_ts(f"[pviz_mcp_http] Expanded allowed_hosts: {allowed_hosts}")

    try:
        from mcp.server.transport_security import TransportSecuritySettings

        _log_ts("[pviz_mcp_http] Successfully imported TransportSecuritySettings")

        dns_rebinding = _bool_env("MCP_ENABLE_DNS_REBINDING_PROTECTION", True)
        _log_ts(f"[pviz_mcp_http] DNS rebinding protection: {dns_rebinding}")

        ts = TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            enable_dns_rebinding_protection=dns_rebinding,
        )
        _log_ts("[pviz_mcp_http] Created TransportSecuritySettings object")
        _log_ts(f"[pviz_mcp_http]   ts.allowed_hosts = {ts.allowed_hosts}")
        _log_ts(f"[pviz_mcp_http]   ts.allowed_origins = {ts.allowed_origins}")

        before = getattr(mcp.settings, "transport_security", None)
        _log_ts(f"[pviz_mcp_http]   BEFORE: mcp.settings.transport_security = {before}")

        mcp.settings.transport_security = ts

        actual = getattr(mcp.settings, "transport_security", None)
        _log_ts(f"[pviz_mcp_http]   mcp.settings.transport_security = {actual}")
        if actual:
            _log_ts(f"[pviz_mcp_http]   actual.allowed_hosts = {getattr(actual, 'allowed_hosts', None)}")
            _log_ts(f"[pviz_mcp_http]   actual.allowed_origins = {getattr(actual, 'allowed_origins', None)}")

        _log_ts("[pviz_mcp_http] ✓ Transport security configuration applied")

    except Exception as e:
        _log_ts("[pviz_mcp_http] ✗ ERROR: Failed to configure transport security")
        _log_ts(f"[pviz_mcp_http]   Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)

    _log_ts("=" * 80)
    _log_ts("")


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

async def health_check(request: Request):
    return JSONResponse({"status": "healthy", "service": "pviz-mcp-server", "transport": "sse"})


async def info_endpoint(request: Request):
    return JSONResponse(
        {
            "name": "pviz-dependency-analyzer",
            "description": "MCP server for polyglot dependency analysis with cost management",
            "transport": "sse",
            "version": os.getenv("PVIZ_MCP_VERSION", "2.0.0"),
            "backend_api": os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com"),
            "endpoints": {
                "health": "/health",
                "mcp_base": "/mcp",
                "mcp_sse": "/mcp/sse",
                "mcp_messages": "/mcp/messages",
            },
            "auth_model": "option_a_forward_user_bearer",
        }
    )


async def mcp_redirect(request: Request):
    return RedirectResponse(url="/mcp/", status_code=307)


async def oauth_not_supported(request: Request):
    return JSONResponse(
        {
            "error": "oauth_not_supported",
            "message": "This MCP server does not advertise OAuth metadata on .well-known endpoints.",
        },
        status_code=404,
    )


# -----------------------------------------------------------------------------
# Configure MCP transport security BEFORE creating the MCP ASGI app
# -----------------------------------------------------------------------------

_configure_mcp_transport_security()

print("[pviz_mcp_http] Creating SSE app from mcp.sse_app()...", file=sys.stderr)
mcp_asgi_app = mcp.sse_app()
print(f"[pviz_mcp_http] SSE app created: {type(mcp_asgi_app)}", file=sys.stderr)

# -----------------------------------------------------------------------------
# Wrap ONLY the mounted /mcp app with SSE-safe ASGI middleware
# -----------------------------------------------------------------------------

mcp_wrapped = mcp_asgi_app
mcp_wrapped = MCPAuthBindMiddleware(mcp_wrapped)
#mcp_wrapped = DebugMcpMessagesMiddleware(mcp_wrapped, enabled=_bool_env("MCP_DEBUG_HTTP", False))

# -----------------------------------------------------------------------------
# Starlette app (top-level) – remains a real Starlette instance
# -----------------------------------------------------------------------------

routes = [
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/", endpoint=info_endpoint, methods=["GET"]),
    Route("/mcp", endpoint=mcp_redirect, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource/mcp", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server", endpoint=oauth_not_supported, methods=["GET"]),
    Mount("/mcp", app=mcp_wrapped),
]

app = Starlette(debug=_bool_env("DEBUG", False), routes=routes)

# -----------------------------------------------------------------------------
# Starlette Host allowlist
# -----------------------------------------------------------------------------

starlette_allowed_hosts = _split_csv_env(
    "ALLOWED_HOSTS",
    default="mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server",
)
if _bool_env("PVIZ_ALLOW_ANY_HOST", False):
    starlette_allowed_hosts = ["*"]

starlette_allowed_hosts = _starlette_safe_hosts(starlette_allowed_hosts)

print(f"[pviz_mcp_http] Starlette allowed_hosts: {starlette_allowed_hosts}", file=sys.stderr)

if starlette_allowed_hosts:
    # This is SSE-safe.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=starlette_allowed_hosts)

# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------

cors_origins = _split_csv_env("CORS_ORIGINS", default="")
if cors_origins:
    allow_credentials = "*" not in cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    print(f"[pviz_mcp_http] CORS enabled for origins: {cors_origins}", file=sys.stderr)

print("[pviz_mcp_http] Server initialization complete", file=sys.stderr)
print(file=sys.stderr)


# -----------------------------------------------------------------------------
# Optional cleanup loop (safe, off by default)
# -----------------------------------------------------------------------------

async def _cleanup_loop() -> None:
    every_s = int(os.getenv("MCP_SESSION_CLEANUP_EVERY_S", "0") or "0")
    if every_s <= 0:
        return
    while True:
        try:
            removed = SESSION_BEARERS.cleanup()
            if DEBUG_AUTH:
                _log_auth("AUTH_BIND cleanup removed=", removed)
        except Exception as e:
            print(
                f"[pviz_mcp_http] AUTH_BIND cleanup error: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
        await asyncio.sleep(every_s)


@app.on_event("startup")
async def _on_startup() -> None:
    every_s = int(os.getenv("MCP_SESSION_CLEANUP_EVERY_S", "0") or "0")
    if every_s > 0:
        asyncio.create_task(_cleanup_loop())


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    log_level = os.getenv("LOG_LEVEL", "info")

    print(f"[pviz_mcp_http] Starting uvicorn server on {host}:{port}", file=sys.stderr)

    uvicorn.run(
        "pviz_mcp_http:app",
        host=host,
        port=port,
        log_level=log_level,
        proxy_headers=True,
        reload=_bool_env("RELOAD", False),
    )
