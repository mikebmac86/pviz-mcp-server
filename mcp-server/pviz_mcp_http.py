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
import os
import sys
import time
from typing import Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from pviz_mcp_server import mcp

# ---------------------------------------------------------------------------
# Option A: shared request-scoped bearer + session store (single source of truth)
# ---------------------------------------------------------------------------
# NOTE:
#   Keep auth_context dependency-light to avoid circular imports.
#   auth_context should define:
#     - PVIZ_REQUEST_BEARER: ContextVar[Optional[str]]
#     - SESSION_BEARERS:     SessionTokenStore(ttl_s=...)
#
# If you're running as a package (e.g. mcp_server.pviz_mcp_http), you may need
# to switch to: from .auth_context import PVIZ_REQUEST_BEARER, SESSION_BEARERS
try:
    from auth_context import PVIZ_REQUEST_BEARER, SESSION_BEARERS  # type: ignore
except Exception:
    # Fallback import path for package layouts
    from .auth_context import PVIZ_REQUEST_BEARER, SESSION_BEARERS  # type: ignore


try:
    from demo_account import DemoAccountMiddleware  # type: ignore

    DEMO_AVAILABLE = True
except Exception:
    DEMO_AVAILABLE = False


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


class MCPAuthBindMiddleware(BaseHTTPMiddleware):
    """
    Binds Authorization: Bearer <token> to session_id (for /mcp/messages calls),
    and sets PVIZ_REQUEST_BEARER contextvar for downstream handling.

    Behavior:
      - If request has Authorization bearer AND has session_id query param:
            store (session_id -> bearer)
      - For any request, if bearer missing but session_id present:
            try to load bearer from store
      - Sets PVIZ_REQUEST_BEARER per request.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        auth = request.headers.get("authorization", "")
        bearer = _parse_bearer(auth)

        # session_id exists on /mcp/messages/?session_id=...
        session_id = request.query_params.get("session_id") or ""

        # Bind token when present
        if bearer and session_id:
            await SESSION_BEARERS.set(session_id, bearer)

        # Fallback: if token missing but we have session_id, try store
        if (not bearer) and session_id:
            bearer = await SESSION_BEARERS.get(session_id)

        # Set request-scoped bearer for the rest of the call stack
        token_ctx = PVIZ_REQUEST_BEARER.set(bearer)

        # Optional debug (safe: don't print token)
        if _bool_env("MCP_DEBUG_AUTH_BIND", False) and (
            request.url.path.startswith("/mcp/sse")
            or request.url.path.startswith("/mcp/messages")
        ):
            print(
                f"[pviz_mcp_http] AUTH_BIND path={request.url.path} "
                f"session_id={'yes' if session_id else 'no'} "
                f"auth_header={'yes' if bool(auth) else 'no'} "
                f"bearer_bound={'yes' if bool(bearer) else 'no'}",
                file=sys.stderr,
            )

        try:
            return await call_next(request)
        finally:
            PVIZ_REQUEST_BEARER.reset(token_ctx)


# ---------------------------------------------------------------------------
# Existing helpers
# ---------------------------------------------------------------------------

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

        if ":" not in h and h != "*":
            out.append(f"{h}:*")
            out.append(f"{h}:443")
            out.append(f"{h}:80")

    return _dedupe_preserve_order(out)


def _configure_mcp_transport_security() -> None:
    """
    PATCHED VERSION WITH EXTENSIVE DEBUGGING
    """
    print("=" * 80, file=sys.stderr)
    print("[pviz_mcp_http] TRANSPORT SECURITY CONFIGURATION (DEBUG)", file=sys.stderr)
    print("=" * 80, file=sys.stderr)

    allow_any = _bool_env("PVIZ_ALLOW_ANY_HOST", False)
    print(f"[pviz_mcp_http] PVIZ_ALLOW_ANY_HOST = {allow_any}", file=sys.stderr)

    default_hosts = "mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server"
    default_origins = "https://mcp.pvizgenerator.com"

    allowed_hosts = _split_csv_env("MCP_ALLOWED_HOSTS", default=default_hosts)
    allowed_origins = _split_csv_env("MCP_ALLOWED_ORIGINS", default=default_origins)

    print(f"[pviz_mcp_http] Initial allowed_hosts: {allowed_hosts}", file=sys.stderr)
    print(f"[pviz_mcp_http] Initial allowed_origins: {allowed_origins}", file=sys.stderr)

    if allow_any:
        allowed_hosts = ["*"]
        allowed_origins = ["*"]
        print(
            f"[pviz_mcp_http] ALLOW_ANY enabled, overriding to: ['*']",
            file=sys.stderr,
        )

    allowed_hosts = _expand_host_variants_for_mcp(allowed_hosts)
    print(f"[pviz_mcp_http] Expanded allowed_hosts: {allowed_hosts}", file=sys.stderr)

    try:
        from mcp.server.transport_security import TransportSecuritySettings

        print(
            f"[pviz_mcp_http] Successfully imported TransportSecuritySettings",
            file=sys.stderr,
        )

        dns_rebinding = _bool_env("MCP_ENABLE_DNS_REBINDING_PROTECTION", True)
        print(f"[pviz_mcp_http] DNS rebinding protection: {dns_rebinding}", file=sys.stderr)

        ts = TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            enable_dns_rebinding_protection=dns_rebinding,
        )
        print(
            f"[pviz_mcp_http] Created TransportSecuritySettings object",
            file=sys.stderr,
        )
        print(f"[pviz_mcp_http]   ts.allowed_hosts = {ts.allowed_hosts}", file=sys.stderr)
        print(
            f"[pviz_mcp_http]   ts.allowed_origins = {ts.allowed_origins}",
            file=sys.stderr,
        )

        # Inspect the mcp object
        print(f"[pviz_mcp_http] Inspecting mcp object:", file=sys.stderr)
        print(f"[pviz_mcp_http]   type(mcp) = {type(mcp)}", file=sys.stderr)
        print(
            f"[pviz_mcp_http]   hasattr(mcp, 'settings') = {hasattr(mcp, 'settings')}",
            file=sys.stderr,
        )
        print(
            f"[pviz_mcp_http]   hasattr(mcp, '_settings') = {hasattr(mcp, '_settings')}",
            file=sys.stderr,
        )

        if hasattr(mcp, "settings"):
            print(
                f"[pviz_mcp_http]   type(mcp.settings) = {type(mcp.settings)}",
                file=sys.stderr,
            )
            print(
                f"[pviz_mcp_http]   hasattr(mcp.settings, 'transport_security') = {hasattr(mcp.settings, 'transport_security')}",
                file=sys.stderr,
            )
            if hasattr(mcp.settings, "transport_security"):
                before = mcp.settings.transport_security
                print(
                    f"[pviz_mcp_http]   BEFORE: mcp.settings.transport_security = {before}",
                    file=sys.stderr,
                )
                if before:
                    print(
                        f"[pviz_mcp_http]   BEFORE: allowed_hosts = {getattr(before, 'allowed_hosts', 'N/A')}",
                        file=sys.stderr,
                    )

        # Try to set it
        set_method = None
        if hasattr(mcp, "settings") and hasattr(mcp.settings, "transport_security"):
            mcp.settings.transport_security = ts
            set_method = "mcp.settings.transport_security"
        elif hasattr(mcp, "_settings") and hasattr(mcp._settings, "transport_security"):
            mcp._settings.transport_security = ts
            set_method = "mcp._settings.transport_security"
        else:
            setattr(mcp, "transport_security", ts)
            set_method = "setattr(mcp, 'transport_security')"

        print(f"[pviz_mcp_http] Set transport security via: {set_method}", file=sys.stderr)

        # Verify it was actually set
        print(f"[pviz_mcp_http] VERIFICATION:", file=sys.stderr)
        if hasattr(mcp, "settings"):
            actual = getattr(mcp.settings, "transport_security", None)
            print(
                f"[pviz_mcp_http]   mcp.settings.transport_security = {actual}",
                file=sys.stderr,
            )
            if actual:
                print(
                    f"[pviz_mcp_http]   actual.allowed_hosts = {getattr(actual, 'allowed_hosts', 'N/A')}",
                    file=sys.stderr,
                )
                print(
                    f"[pviz_mcp_http]   actual.allowed_origins = {getattr(actual, 'allowed_origins', 'N/A')}",
                    file=sys.stderr,
                )

                # Check if it's the same object we just created
                if actual is ts:
                    print(
                        f"[pviz_mcp_http]   ✓ Configuration SUCCESSFULLY applied (same object)",
                        file=sys.stderr,
                    )
                elif getattr(actual, "allowed_hosts", None) == allowed_hosts:
                    print(
                        f"[pviz_mcp_http]   ✓ Configuration SUCCESSFULLY applied (matching hosts)",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[pviz_mcp_http]   ✗ WARNING: Different configuration active!",
                        file=sys.stderr,
                    )
                    print(
                        f"[pviz_mcp_http]     Expected: {allowed_hosts}",
                        file=sys.stderr,
                    )
                    print(
                        f"[pviz_mcp_http]     Actual: {getattr(actual, 'allowed_hosts', None)}",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"[pviz_mcp_http]   ✗ WARNING: transport_security is None after setting!",
                    file=sys.stderr,
                )

        if hasattr(mcp, "_settings"):
            actual = getattr(mcp._settings, "transport_security", None)
            print(f"[pviz_mcp_http]   mcp._settings.transport_security = {actual}", file=sys.stderr)

    except Exception as e:
        print(
            f"[pviz_mcp_http] ✗ ERROR: Failed to configure transport security",
            file=sys.stderr,
        )
        print(
            f"[pviz_mcp_http]   Exception: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        import traceback

        traceback.print_exc(file=sys.stderr)

    print("=" * 80, file=sys.stderr)
    print(file=sys.stderr)


async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "pviz-mcp-server", "transport": "sse"})


async def info_endpoint(request):
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


async def mcp_redirect(request):
    return RedirectResponse(url="/mcp/", status_code=307)


async def oauth_not_supported(request):
    return JSONResponse(
        {
            "error": "oauth_not_supported",
            "message": "This MCP server does not advertise OAuth metadata on .well-known endpoints.",
        },
        status_code=404,
    )


class DebugMcpMessagesMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if path.startswith("/mcp/messages"):
            # capture headers
            headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
            method = scope.get("method", "?")
            qs = scope.get("query_string", b"").decode(errors="replace")

            # drain full body
            chunks = []
            more = True
            while more:
                msg = await receive()
                if msg["type"] != "http.request":
                    continue
                chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)

            body = b"".join(chunks)
            preview = body[:300].decode(errors="replace")

            # NOTE: Do NOT log Authorization token values.
            auth_present = "authorization" in {k.lower(): v for k, v in headers.items()}

            print(f"[pviz_mcp_http] DEBUG {method} {path}?{qs}", file=sys.stderr)
            print(
                f"[pviz_mcp_http] DEBUG headers: content-type={headers.get('content-type')} "
                f"len={headers.get('content-length')} auth={'yes' if auth_present else 'no'}",
                file=sys.stderr,
            )
            print(
                f"[pviz_mcp_http] DEBUG body_len={len(body)} body_preview={preview!r}",
                file=sys.stderr,
            )

            # replay body to downstream app
            sent = False

            async def replay_receive():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            return await self.app(scope, replay_receive, send)

        return await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Configure MCP transport security BEFORE creating the MCP ASGI app
# ---------------------------------------------------------------------------
_configure_mcp_transport_security()

# MCP SDK SSE ASGI app (this is what clients talk to at /mcp/*)
print(f"[pviz_mcp_http] Creating SSE app from mcp.sse_app()...", file=sys.stderr)
mcp_asgi_app = DebugMcpMessagesMiddleware(mcp.sse_app())
print(f"[pviz_mcp_http] SSE app created: {type(mcp_asgi_app)}", file=sys.stderr)

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
# Option A middleware MUST be before anything that might need the bearer
# ---------------------------------------------------------------------------
app.add_middleware(MCPAuthBindMiddleware)

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

if DEMO_AVAILABLE:
    app.add_middleware(DemoAccountMiddleware)
    print(f"[pviz_mcp_http] DemoAccountMiddleware enabled", file=sys.stderr)

print(f"[pviz_mcp_http] Server initialization complete", file=sys.stderr)
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
            if _bool_env("MCP_DEBUG_AUTH_BIND", False):
                print(f"[pviz_mcp_http] AUTH_BIND cleanup removed={removed}", file=sys.stderr)
        except Exception as e:
            print(f"[pviz_mcp_http] AUTH_BIND cleanup error: {type(e).__name__}: {e}", file=sys.stderr)
        await asyncio.sleep(every_s)


@app.on_event("startup")
async def _on_startup() -> None:
    # Start cleanup loop only if configured
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
