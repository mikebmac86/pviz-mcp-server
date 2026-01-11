"""
HTTP Transport Wrapper for pviz MCP Server
Deploy this to cloud services (AWS Lambda, GCP Cloud Run, Azure Functions, etc.)
"""

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
import uvicorn
import os

# Import the MCP server
from pviz_mcp_server import mcp

# Import demo account middleware
try:
    from demo_account import DemoAccountMiddleware
    DEMO_AVAILABLE = True
except ImportError:
    DEMO_AVAILABLE = False


async def mcp_handler(request: Request):
    """
    Handle MCP protocol requests over HTTP.
    
    This endpoint receives JSON-RPC formatted MCP requests and
    returns responses in the same format.
    """
    payload = None
    try:
        payload = await request.json()
        result = await mcp.handle_http(payload)
        return JSONResponse(result)
    except Exception as e:
        # Return JSON-RPC shaped error (best-effort) so MCP clients don't choke.
        req_id = payload.get("id") if isinstance(payload, dict) else None
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": str(e),
                },
            },
            status_code=500,
        )


async def health_check(request: Request):
    """Health check endpoint for load balancers and monitoring."""
    return JSONResponse({
        "status": "healthy",
        "service": "pviz-mcp-server",
        "version": "1.0.0"
    })


async def info_endpoint(request: Request):
    """Return server information and capabilities."""
    return JSONResponse({
        "name": "pviz-dependency-analyzer",
        "description": "MCP server for polyglot dependency analysis with cost management",
        "version": "2.0.0",
        "capabilities": {
            "tools": [
                # Account & Balance Management
                "check_account_balance",
                "estimate_analysis_cost",
                "get_job_history",
                "retrieve_past_result",
                
                # Analysis Operations
                "analyze_repository",
                "get_analysis_status",
                "download_dependency_graph",
                
                # Analysis Features
                "get_circular_dependencies",
                "get_repository_metrics",
                "compare_repositories"
            ],
            "resources": [
                "pviz://schema",
                "pviz://examples"
            ],
            "prompts": [
                "analyze_codebase_prompt",
                "compare_projects_prompt"
            ]
        },
        "backend_api": os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com"),
        "features": {
            "cost_estimation": True,
            "balance_checking": True,
            "job_history": True,
            "result_retrieval": True,
            "trial_credits": True,
            "one_off_purchases": True
        }
    })


# Create Starlette app
app = Starlette(
    debug=os.getenv("DEBUG", "false").lower() == "true",
    routes=[
        Route("/mcp", endpoint=mcp_handler, methods=["POST"]),
        Route("/health", endpoint=health_check, methods=["GET"]),
        Route("/", endpoint=info_endpoint, methods=["GET"]),
    ]
)

# Add CORS middleware only if explicitly configured.
cors_origins = os.getenv("CORS_ORIGINS")
if cors_origins:
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    # If a user sets "*", do not allow credentials (unsafe / invalid in browsers).
    allow_credentials = "*" not in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["*"],
    )

# Add demo account middleware (if available and enabled)
if DEMO_AVAILABLE:
    app.add_middleware(DemoAccountMiddleware)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"🚀 Starting pviz MCP server on {host}:{port}")
    print(f"📡 MCP endpoint: http://{host}:{port}/mcp")
    print(f"💚 Health check: http://{host}:{port}/health")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info")
    )