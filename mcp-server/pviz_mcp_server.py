"""
pviz MCP Server - Production Version
Integrates with existing FastAPI backend at api.pvizgenerator.com

This server exposes pviz's dependency analysis capabilities to LLMs via MCP protocol.
"""

from __future__ import annotations

import os
import asyncio
import httpx
import random
import logging
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from api_adapter import PvizAPIAdapter

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("pviz-mcp")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# -----------------------------------------------------------------------------
# MCP init
# -----------------------------------------------------------------------------
mcp = FastMCP("pviz-dependency-analyzer")

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
API_BASE_URL = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com")
JWT_TOKEN = os.getenv("PVIZ_JWT_TOKEN")

POLL_MIN_SLEEP_S = float(os.getenv("PVIZ_POLL_MIN_SLEEP", "5"))
POLL_MAX_SLEEP_S = float(os.getenv("PVIZ_POLL_MAX_SLEEP", "30"))
POLL_JITTER_RATIO = float(os.getenv("PVIZ_POLL_JITTER_RATIO", "0.25"))
MAX_POLL_ATTEMPTS = int(os.getenv("PVIZ_MAX_POLL_ATTEMPTS", "60"))

MAX_ARTIFACT_BYTES = int(os.getenv("PVIZ_MAX_ARTIFACT_BYTES", str(50 * 1024 * 1024)))
DOWNLOAD_TIMEOUT_S = float(os.getenv("PVIZ_DOWNLOAD_TIMEOUT_S", "120"))

HTTP_TIMEOUT_S = float(os.getenv("PVIZ_HTTP_TIMEOUT_S", "30"))
HTTP_CONNECT_TIMEOUT_S = float(os.getenv("PVIZ_HTTP_CONNECT_TIMEOUT_S", "10"))

TERMINAL_SUCCESS = {"completed"}
TERMINAL_FAILURE = {
    "failed",
    "canceled",
    "cancelled",
    "insufficient_tokens",
    "awaiting_payment",
}
TERMINAL_STATES = TERMINAL_SUCCESS | TERMINAL_FAILURE

# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
class PvizAPIError(Exception):
    pass

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _require_jwt() -> str:
    if not JWT_TOKEN:
        raise PvizAPIError("PVIZ_JWT_TOKEN not set")
    return JWT_TOKEN

def _adapter() -> PvizAPIAdapter:
    return PvizAPIAdapter(API_BASE_URL, _require_jwt())

_client: Optional[httpx.AsyncClient] = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=HTTP_TIMEOUT_S,
                connect=HTTP_CONNECT_TIMEOUT_S,
            ),
            follow_redirects=True,
        )
    return _client

async def _close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None

def _normalize_repo_url(repo_url: str) -> str:
    repo_url = (repo_url or "").strip()
    if not repo_url:
        raise PvizAPIError("repo_url is required")
    if "://" not in repo_url and repo_url.count("/") == 1:
        return f"https://github.com/{repo_url}"
    parsed = urlparse(repo_url)
    if not parsed.scheme or not parsed.netloc:
        raise PvizAPIError("Invalid repo_url")
    return repo_url

async def _download_json_streaming(url: str) -> Dict[str, Any]:
    client = _get_client()
    total = 0
    chunks: List[bytes] = []

    async with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise PvizAPIError("Artifact exceeds size limit")
            chunks.append(chunk)

    return httpx.Response(200, content=b"".join(chunks)).json()

async def _wait_for_terminal(job_id: str) -> Dict[str, Any]:
    api = _adapter()
    client = _get_client()
    sleep_s = POLL_MIN_SLEEP_S

    for _ in range(MAX_POLL_ATTEMPTS):
        status = await api.get_job_status(client, job_id)
        state = api.get_job_status_value(status)
        if state in TERMINAL_STATES:
            return status

        jitter = (random.random() * 2 - 1) * (sleep_s * POLL_JITTER_RATIO)
        await asyncio.sleep(max(0.1, sleep_s + jitter))
        sleep_s = min(POLL_MAX_SLEEP_S, sleep_s * 1.4)

    raise PvizAPIError("Polling timeout")

# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool()
async def analyze_repository(
    repo_url: str,
    wait_for_completion: bool = True,
    include_full_graph: bool = False,
    pricing_choice: str = "tokens",
    questions: Optional[List[str]] = None,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    repo_url = _normalize_repo_url(repo_url)
    api = _adapter()
    client = _get_client()

    submit = await api.submit_analysis(
        client,
        repo_url=repo_url,
        pricing_choice=pricing_choice,
        questions=questions,
        github_token=github_token,
    )

    job_id = api.extract_job_id(submit)

    if not wait_for_completion:
        return {
            "success": True,
            "status": "submitted",
            "job_id": job_id,
        }

    status = await _wait_for_terminal(job_id)
    state = api.get_job_status_value(status)

    if state != "completed":
        return {
            "success": False,
            "status": state,
            "details": status,
        }

    download = await api.get_download_link(client, job_id)
    s3_url = download.get("url")

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at"),
        "tokens_charged": status.get("tokens_charged"),
        "s3_url": s3_url,
    }

    if include_full_graph and s3_url:
        result["dependency_graph"] = await _download_json_streaming(s3_url)

    return result

@mcp.tool()
async def get_analysis_status(job_id: str) -> Dict[str, Any]:
    api = _adapter()
    return await api.get_job_status(_get_client(), job_id)

@mcp.tool()
async def retrieve_past_result(
    job_id: str,
    include_full_graph: bool = True
) -> Dict[str, Any]:
    api = _adapter()
    client = _get_client()

    status = await api.get_job_status(client, job_id)
    if api.get_job_status_value(status) != "completed":
        return {"success": False, "status": status.get("status")}

    download = await api.get_download_link(client, job_id)
    s3_url = download.get("url")

    result = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at"),
        "tokens_charged": status.get("tokens_charged"),
        "s3_url": s3_url,
    }

    if include_full_graph and s3_url:
        result["dependency_graph"] = await _download_json_streaming(s3_url)

    return result

# Expose ASGI app for uvicorn
app = mcp.asgi_app()

# =============================================================================
# Entrypoint
# =============================================================================
if __name__ == "__main__":
    logger.info("Starting pviz MCP server")
    try:
        mcp.run()
    finally:
        asyncio.run(_close_client())
