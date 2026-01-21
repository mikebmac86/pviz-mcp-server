"""
HTTP/SSE Transport Wrapper for pviz MCP Server - PATCHED WITH DEBUGGING (Option A)

Option A goal:
  - Hosted MCP does NOT use a static PVIZ_JWT_TOKEN/PVIZ_JWT_TOKEN_FILE secret.
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
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from .auth_context import PVIZ_REQUEST_BEARER, SESSION_BEARERS  # type: ignore
from .pviz_mcp_server import mcp

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


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return float(v.strip())
    except Exception:
        return default


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
DEBUG_TRANSPORT_SECURITY = _bool_env("MCP_DEBUG_TRANSPORT_SECURITY", True)  # keep your current default behavior


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

    Behavior:
      - If request has Authorization bearer AND has session_id query param:
            store (session_id -> bearer)
      - For any request, if bearer missing but session_id present:
            try to load bearer from store
      - Sets PVIZ_REQUEST_BEARER per request.
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

        # Extract session_id from query string (avoid Request() creation to stay lean)
        session_id = ""
        try:
            qs = (scope.get("query_string") or b"").decode("utf-8", errors="replace")
            # tiny parse, only care about session_id=
            # format: a=b&session_id=...&c=d
            for part in qs.split("&"):
                if part.startswith("session_id="):
                    session_id = part.split("=", 1)[1]
                    break
        except Exception:
            session_id = ""

        # Bind token when present
        if bearer and session_id:
            await SESSION_BEARERS.set(session_id, bearer)

        # Fallback: if token missing but we have session_id, try store
        if (not bearer) and session_id:
            bearer = await SESSION_BEARERS.get(session_id)

        token_ctx = PVIZ_REQUEST_BEARER.set(bearer)

        path = scope.get("path", "")
        if DEBUG_AUTH and (path.startswith("/mcp/sse") or path.startswith("/mcp/messages")):
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


# -----------------------------------------------------------------------------
# ASGI middleware (SSE-safe): Debug /mcp/messages body without breaking streaming
# -----------------------------------------------------------------------------

class DebugMcpMessagesMiddleware:
    """
    ASGI-native debug middleware that is SAFE for StreamingResponse / SSE.

    IMPORTANT:
      - Do NOT implement with BaseHTTPMiddleware. It breaks streaming and can
        produce: "AssertionError: Unexpected message: {'type': 'http.response.start', ...}"
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
        if not self.enabled or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp/messages"):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        qs = (scope.get("query_string") or b"").decode("utf-8", errors="replace")

        headers: Dict[str, str] = {}
        for k, v in (scope.get("headers") or []):
            try:
                headers[k.decode("latin-1").lower()] = v.decode("latin-1")
            except Exception:
                continue

        auth_present = "authorization" in headers

        # Drain full request body, then replay downstream
        chunks: List[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg.get("type") != "http.request":
                continue
            chunks.append(msg.get("body") or b"")
            more = bool(msg.get("more_body"))

        body = b"".join(chunks)
        preview = self._preview_bytes(body)

        _log_http(f"DEBUG {method} {path}?{qs}")
        _log_http(
            "DEBUG headers:",
            f"content-type={headers.get('content-type')}",
            f"len={headers.get('content-length')}",
            f"auth={'yes' if auth_present else 'no'}",
        )
        _log_http(f"DEBUG body_len={len(body)} body_preview={preview!r}")

        sent = False

        async def replay_receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


# -----------------------------------------------------------------------------
# MCP transport security configuration (unchanged logic; cleaned output gating)
# -----------------------------------------------------------------------------

def _configure_mcp_transport_security() -> None:
    """
    Configure MCP SDK transport security (allowed hosts/origins).
    """

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

        # Apply to mcp settings
        set_method = None
        if hasattr(mcp, "settings") and hasattr(mcp.settings, "transport_security"):
            before = getattr(mcp.settings, "transport_security", None)
            _log_ts(f"[pviz_mcp_http]   BEFORE: mcp.settings.transport_security = {before}")
            mcp.settings.transport_security = ts
            set_method = "mcp.settings.transport_security"
        elif hasattr(mcp, "_settings") and hasattr(mcp._settings, "transport_security"):
            before = getattr(mcp._settings, "transport_security", None)
            _log_ts(f"[pviz_mcp_http]   BEFORE: mcp._settings.transport_security = {before}")
            mcp._settings.transport_security = ts
            set_method = "mcp._settings.transport_security"
        else:
            setattr(mcp, "transport_security", ts)
            set_method = "setattr(mcp, 'transport_security')"

        _log_ts(f"[pviz_mcp_http] Set transport security via: {set_method}")

        # Verification
        if hasattr(mcp, "settings"):
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

async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "pviz-mcp-server", "transport": "sse"})


async def info_endpoint(request: Request) -> JSONResponse:
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


async def mcp_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/mcp/", status_code=307)


async def oauth_not_supported(request: Request) -> JSONResponse:
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

# MCP SDK SSE ASGI app (this is what clients talk to at /mcp/*)
print("[pviz_mcp_http] Creating SSE app from mcp.sse_app()...", file=sys.stderr)
mcp_asgi_app = mcp.sse_app()
print(f"[pviz_mcp_http] SSE app created: {type(mcp_asgi_app)}", file=sys.stderr)


# -----------------------------------------------------------------------------
# Starlette app
# -----------------------------------------------------------------------------

routes = [
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/", endpoint=info_endpoint, methods=["GET"]),
    Route("/mcp", endpoint=mcp_redirect, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource/mcp", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server", endpoint=oauth_not_supported, methods=["GET"]),
    Mount("/mcp", app=mcp_asgi_app),
]

app = Starlette(debug=_bool_env("DEBUG", False), routes=routes)

# ---------------------------------------------------------------------------
# IMPORTANT: Add ASGI-native middleware *by wrapping*, not Starlette BaseHTTPMiddleware
# ---------------------------------------------------------------------------

# Option A bearer/session binding (MUST be outermost so all downstream sees contextvar)
app = MCPAuthBindMiddleware(app)

# Optional message-body debugging for /mcp/messages (outermost of mcp_asgi_app is fine too,
# but we want to see traffic hitting the Starlette app)
app = DebugMcpMessagesMiddleware(app, enabled=_bool_env("MCP_DEBUG_HTTP", False))

# ---------------------------------------------------------------------------
# Starlette Host allowlist
# ---------------------------------------------------------------------------

starlette_allowed_hosts = _split_csv_env(
    "ALLOWED_HOSTS",
    default="mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server",
)
if _bool_env("PVIZ_ALLOW_ANY_HOST", False):
    starlette_allowed_hosts = ["*"]

starlette_allowed_hosts = _starlette_safe_hosts(starlette_allowed_hosts)

print(f"[pviz_mcp_http] Starlette allowed_hosts: {starlette_allowed_hosts}", file=sys.stderr)

if starlette_allowed_hosts:
    # This middleware is OK with SSE.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=starlette_allowed_hosts)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Optional cleanup loop (safe, off by default)
# ---------------------------------------------------------------------------

async def _cleanup_loop() -> None:
    every_s = int(os.getenv("MCP_SESSION_CLEANUP_EVERY_S", "0") or "0")
    if every_s <= 0:
        return
    while True:
        try:
            removed = await SESSION_BEARERS.cleanup()
            if DEBUG_AUTH:
                _log_auth("AUTH_BIND cleanup removed=", removed)
        except Exception as e:
            print(
                f"[pviz_mcp_http] AUTH_BIND cleanup error: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
        await asyncio.sleep(every_s)


# Starlette supports startup hooks, but our app has been wrapped. We need to attach the event
# before wrapping if we want it registered in Starlette. So we register on the original Starlette
# instance via lifespan-style workaround:
#
# Easiest approach: only start cleanup loop if enabled, and do it opportunistically in the server
# startup code when running as __main__ (local dev). In containerized prod, you can start a task
# in your Uvicorn startup elsewhere.
#
# If you want it inside Starlette lifecycle, move wrapping (app = ...) BELOW this @app.on_event block
# and keep a separate `starlette_app` variable.

# (Keeping behavior consistent with your existing deployment: cleanup loop is optional/off by default.)


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
