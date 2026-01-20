"""
HTTP/SSE Transport Wrapper for pviz MCP Server

Routes:
  - GET  /health         : health check
  - GET  /               : info/capabilities
  - GET  /mcp            : redirect -> /mcp/ (so clients don't hit 307->404)
  - /mcp/*               : MCP SSE transport (served by MCP SDK)
      - GET  /mcp/sse
      - POST /mcp/messages

Deploy behind Caddy or any reverse proxy.
"""

from __future__ import annotations

import os
from typing import List

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from pviz_mcp_server import mcp

try:
    from demo_account import DemoAccountMiddleware  # type: ignore

    DEMO_AVAILABLE = True
except Exception:
    DEMO_AVAILABLE = False


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


def _expand_host_variants(hosts: List[str]) -> List[str]:
    """
    Some host validators treat "example.com" and "example.com:443" differently.
    Also, some support a port wildcard like "example.com:*".
    We include a few variants defensively.
    """
    out: List[str] = []
    for h in hosts:
        if not h:
            continue
        out.append(h)

        # If host has no port, add a wildcard-port variant (if the SDK supports it)
        if ":" not in h and h != "*":
            out.append(f"{h}:*")
            out.append(f"{h}:443")
            out.append(f"{h}:80")

    # De-dupe preserving order
    seen = set()
    deduped: List[str] = []
    for h in out:
        if h not in seen:
            seen.add(h)
            deduped.append(h)
    return deduped


def _configure_mcp_transport_security() -> None:
    """
    IMPORTANT:
    The MCP Python SDK has its own transport security validation which is what
    is producing the 421 + "Invalid Host header" logs.

    We must configure MCP's transport security allowed hosts (and optionally origins).
    """
    # Debug escape hatch (do NOT leave enabled in production)
    allow_any = _bool_env("PVIZ_ALLOW_ANY_HOST", False)

    # Defaults that match your deployment
    default_hosts = "mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server"
    default_origins = "https://mcp.pvizgenerator.com"

    allowed_hosts = _split_csv_env("MCP_ALLOWED_HOSTS", default=default_hosts)
    allowed_origins = _split_csv_env("MCP_ALLOWED_ORIGINS", default=default_origins)

    if allow_any:
        allowed_hosts = ["*"]
        allowed_origins = ["*"]

    allowed_hosts = _expand_host_variants(allowed_hosts)

    # The MCP SDK uses TransportSecuritySettings on the FastMCP settings.
    # This code assumes pviz_mcp_server.mcp is a FastMCP instance (as your traceback shows).
    try:
        from mcp.server.transport_security import TransportSecuritySettings  # type: ignore

        ts = TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            # If the SDK supports this flag, keep it enabled by default.
            # You can disable temporarily with MCP_ENABLE_DNS_REBINDING_PROTECTION=0
            enable_dns_rebinding_protection=_bool_env(
                "MCP_ENABLE_DNS_REBINDING_PROTECTION", True
            ),
        )

        # Assign onto the FastMCP settings object
        if hasattr(mcp, "settings") and hasattr(mcp.settings, "transport_security"):
            mcp.settings.transport_security = ts  # type: ignore[attr-defined]
        elif hasattr(mcp, "_settings") and hasattr(mcp._settings, "transport_security"):
            mcp._settings.transport_security = ts  # type: ignore[attr-defined]
        else:
            # Last-resort: attempt direct attribute
            setattr(mcp, "transport_security", ts)

    except Exception as e:
        # If this fails, we *still* want the app to boot (so you can see logs),
        # but SSE will likely keep failing until the SDK config is applied.
        print(f"[pviz_mcp_http] WARN: failed to configure MCP transport security: {e!r}")


async def health_check(request):
    return JSONResponse(
        {"status": "healthy", "service": "pviz-mcp-server", "transport": "sse"}
    )


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


# ---------------------------------------------------------------------------
# Configure MCP transport security BEFORE creating the MCP ASGI app
# ---------------------------------------------------------------------------
_configure_mcp_transport_security()

# MCP SDK SSE ASGI app (this is what clients talk to at /mcp/*)
mcp_asgi_app = mcp.sse_app()

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
# Starlette Host allowlist (nice to keep consistent, but MCP SDK is the real gate)
# ---------------------------------------------------------------------------
starlette_allowed_hosts = _split_csv_env(
    "ALLOWED_HOSTS",
    default="mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server",
)
if _bool_env("PVIZ_ALLOW_ANY_HOST", False):
    starlette_allowed_hosts = ["*"]

if starlette_allowed_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_expand_host_variants(starlette_allowed_hosts),
    )

# ---------------------------------------------------------------------------
# CORS only if explicitly configured
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

if DEMO_AVAILABLE:
    app.add_middleware(DemoAccountMiddleware)

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    log_level = os.getenv("LOG_LEVEL", "info")

    uvicorn.run(
        "pviz_mcp_http:app",
        host=host,
        port=port,
        log_level=log_level,
        proxy_headers=True,
        reload=_bool_env("RELOAD", False),
    )
