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
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route, Mount
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
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
    # Ensure /mcp works even if the transport expects /mcp/
    return RedirectResponse(url="/mcp/", status_code=307)


# Optional: respond explicitly to OAuth discovery probes (some clients check these)
async def oauth_not_supported(request):
    return JSONResponse(
        {
            "error": "oauth_not_supported",
            "message": "This MCP server does not advertise OAuth metadata on .well-known endpoints.",
        },
        status_code=404,
    )


# MCP SDK SSE ASGI app (this is what mcp-remote expects to talk to at /mcp/*)
mcp_asgi_app = mcp.sse_app()

routes = [
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/", endpoint=info_endpoint, methods=["GET"]),

    # Make /mcp a redirect, and let the mounted transport handle /mcp/*
    Route("/mcp", endpoint=mcp_redirect, methods=["GET"]),

    # Optional OAuth probe endpoints
    Route("/.well-known/oauth-protected-resource", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource/mcp", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server", endpoint=oauth_not_supported, methods=["GET"]),

    # Mount MCP transport under /mcp (handles /mcp/sse and /mcp/messages)
    Mount("/mcp", app=mcp_asgi_app),
]

app = Starlette(debug=_bool_env("DEBUG", False), routes=routes)

# ---------------------------------------------------------------------------
# Host allowlist (fixes "Invalid Host header" from the MCP SSE transport)
# ---------------------------------------------------------------------------
allowed_hosts_env = os.getenv(
    "ALLOWED_HOSTS",
    "mcp.pvizgenerator.com,localhost,127.0.0.1,pviz-mcp-server",
)
allowed_hosts = [h.strip() for h in allowed_hosts_env.split(",") if h.strip()]

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=allowed_hosts,
)

# CORS only if explicitly configured
cors_origins = os.getenv("CORS_ORIGINS")
if cors_origins:
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    allow_credentials = "*" not in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
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
