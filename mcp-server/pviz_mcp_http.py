"""
HTTP/SSE Transport Wrapper for pviz MCP Server

Routes:
  - GET  /health         : health check
  - GET  /               : info/capabilities
  - GET  /mcp            : MCP discovery (what endpoints to call)
  - GET  /mcp/           : same as /mcp
  - GET  /mcp/sse        : SSE endpoint (served by MCP SDK)
  - POST /mcp/messages   : message endpoint (served by MCP SDK)

Deploy behind Caddy or any reverse proxy.
"""

from __future__ import annotations

import os
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route, Mount
from starlette.middleware.cors import CORSMiddleware

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
                "mcp_discovery": "/mcp",
                "mcp_sse": "/mcp/sse",
                "mcp_messages": "/mcp/messages",
            },
        }
    )


def _mcp_discovery_payload() -> dict:
    # “Base URL + relative endpoints” pattern is what many clients want.
    return {
        "transport": "sse",
        "endpoints": {
            "sse": "/mcp/sse",
            "messages": "/mcp/messages",
        },
        "notes": [
            "Use GET /mcp/sse to establish an SSE session.",
            "Send MCP messages via POST /mcp/messages.",
        ],
    }


async def mcp_discovery(request):
    # Return a stable discovery doc instead of redirecting to /mcp/ and 404ing.
    return JSONResponse(_mcp_discovery_payload())


async def mcp_discovery_slash(request):
    # Some clients/proxies will request /mcp/ explicitly. Keep it 200 and identical.
    return JSONResponse(_mcp_discovery_payload())


# Optional: respond explicitly to OAuth discovery probes (clients sometimes check these)
async def oauth_not_supported(request):
    return JSONResponse(
        {
            "error": "oauth_not_supported",
            "message": "This MCP server does not advertise OAuth metadata on .well-known endpoints.",
        },
        status_code=404,
    )


# MCP SDK SSE app
mcp_asgi_app = mcp.sse_app()

routes = [
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/", endpoint=info_endpoint, methods=["GET"]),

    # IMPORTANT: Handle /mcp and /mcp/ explicitly so clients don't see 307->404
    Route("/mcp", endpoint=mcp_discovery, methods=["GET", "POST"]),
    Route("/mcp/", endpoint=mcp_discovery_slash, methods=["GET", "POST"]),

    # Optional OAuth probe endpoints (keep 404 but with explicit JSON)
    Route("/.well-known/oauth-protected-resource", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource/mcp", endpoint=oauth_not_supported, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server", endpoint=oauth_not_supported, methods=["GET"]),

    # Mount the MCP transport under /mcp for /mcp/sse and /mcp/messages
    Mount("/mcp", app=mcp_asgi_app),
]

app = Starlette(debug=_bool_env("DEBUG", False), routes=routes)

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
