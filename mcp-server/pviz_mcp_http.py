"""
HTTP/SSE Transport Wrapper for pviz MCP Server

This module exposes the FastMCP server over ASGI using the MCP SDK's SSE transport.

Routes:
  - GET  /health         : simple health check (for docker/caddy)
  - GET  /               : info/capabilities
  - GET  /mcp/sse        : SSE endpoint (served by MCP SDK)
  - POST /mcp/messages   : message endpoint (served by MCP SDK)
    (Some clients may hit /mcp/messages/ as well; proxy/app should tolerate both.)

Deploy behind Caddy or any reverse proxy.
"""

from __future__ import annotations

import os
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
from starlette.middleware.cors import CORSMiddleware

# Import the MCP server instance (tools/resources/prompts live there)
from pviz_mcp_server import mcp

# Optional demo account middleware (keep if you have it)
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
    # Keep this tiny and stable; lots of infra depends on it.
    return JSONResponse(
        {
            "status": "healthy",
            "service": "pviz-mcp-server",
            "transport": "sse",
        }
    )


async def info_endpoint(request):
    # This is informational only; don’t make it a hard contract.
    return JSONResponse(
        {
            "name": "pviz-dependency-analyzer",
            "description": "MCP server for polyglot dependency analysis with cost management",
            "transport": "sse",
            "version": os.getenv("PVIZ_MCP_VERSION", "2.0.0"),
            "endpoints": {
                "health": "/health",
                # These are the canonical endpoints as mounted below:
                "mcp_sse": "/mcp/sse",
                "mcp_messages": "/mcp/messages",
            },
            "backend_api": os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com"),
            "notes": [
                "This service uses MCP SDK SSE transport.",
                "The actual tool list is defined in pviz_mcp_server.py (FastMCP).",
            ],
        }
    )


# MCP SDK SSE app
# IMPORTANT: This is the supported ASGI surface for FastMCP in your SDK build.
mcp_asgi_app = mcp.sse_app()

routes = [
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/", endpoint=info_endpoint, methods=["GET"]),
    # Mount the MCP SSE transport under /mcp so Caddy can reverse-proxy it cleanly.
    Mount("/mcp", app=mcp_asgi_app),
]

# Starlette will (by default) redirect slashed/unslashed paths for *defined* routes.
# For mounted apps, behavior can vary with proxies; keeping redirect_slashes=True helps.
app = Starlette(
    debug=_bool_env("DEBUG", False),
    routes=routes,
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

# Demo middleware (optional)
if DEMO_AVAILABLE:
    app.add_middleware(DemoAccountMiddleware)


if __name__ == "__main__":
    # Local dev runner (container should run via uvicorn CMD)
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    log_level = os.getenv("LOG_LEVEL", "info")
    reload = _bool_env("RELOAD", False)

    uvicorn.run(
        "pviz_mcp_http:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
        # Proxy headers are commonly useful behind Caddy/Nginx:
        proxy_headers=True,
    )
