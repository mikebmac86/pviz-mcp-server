"""
pviz MCP Server - Production Version
Integrates with existing FastAPI backend at api.pvizgenerator.com

This file defines MCP tools and backend orchestration.
For HTTP deployment (Docker/Caddy), run pviz_mcp_http.py as the ASGI app.
"""

from __future__ import annotations

import os
import asyncio
import random
import logging
from typing import Any, Dict, Optional, List
from urllib.parse import urlparse

import httpx
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
    "cancelled",  # tolerate spelling
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
# JWT / Adapter
# -----------------------------------------------------------------------------
def _load_jwt() -> str:
    """
    Loads JWT from PVIZ_JWT_TOKEN or PVIZ_JWT_TOKEN_FILE (docker secret style).
    """
    tok = os.getenv("PVIZ_JWT_TOKEN")
    if tok and tok.strip():
        return tok.strip()

    tok2 = None
    if hasattr(PvizAPIAdapter, "load_jwt_token_from_env"):
        tok2 = PvizAPIAdapter.load_jwt_token_from_env()  # type: ignore[attr-defined]
    if tok2 and tok2.strip():
        return tok2.strip()

    raise PvizAPIError("JWT token not configured (set PVIZ_JWT_TOKEN or PVIZ_JWT_TOKEN_FILE)")

def _adapter() -> PvizAPIAdapter:
    return PvizAPIAdapter(API_BASE_URL, _load_jwt())

# -----------------------------------------------------------------------------
# httpx client (shared)
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _normalize_repo_url(repo_url: str) -> str:
    repo_url = (repo_url or "").strip()
    if not repo_url:
        raise PvizAPIError("repo_url is required")

    # Shorthand: owner/repo
    if "://" not in repo_url and repo_url.count("/") == 1:
        return f"https://github.com/{repo_url}"

    parsed = urlparse(repo_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise PvizAPIError("Invalid repo_url")

    return repo_url

async def _download_json_streaming(url: str) -> Dict[str, Any]:
    client = _get_client()
    total = 0
    chunks: List[bytes] = []

    async with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise PvizAPIError(
                    f"Artifact exceeds size limit ({total} > {MAX_ARTIFACT_BYTES}). "
                    "Increase PVIZ_MAX_ARTIFACT_BYTES or disable include_full_graph."
                )
            chunks.append(chunk)

    return httpx.Response(200, content=b"".join(chunks)).json()

async def _wait_for_terminal(job_id: str) -> Dict[str, Any]:
    api = _adapter()
    client = _get_client()
    sleep_s = max(0.1, POLL_MIN_SLEEP_S)

    for _ in range(MAX_POLL_ATTEMPTS):
        status = await api.get_job_status(client, job_id)
        state = api.get_job_status_value(status)

        if state in TERMINAL_STATES:
            return status

        jitter = (random.random() * 2 - 1) * (sleep_s * POLL_JITTER_RATIO)
        await asyncio.sleep(max(0.1, sleep_s + jitter))
        sleep_s = min(POLL_MAX_SLEEP_S, max(POLL_MIN_SLEEP_S, sleep_s * 1.4))

    raise PvizAPIError(f"Polling timeout for job_id={job_id}")

async def _get_artifact_url(api: PvizAPIAdapter, client: httpx.AsyncClient, job_id: str) -> Optional[str]:
    """
    Canonical artifact URL fetch:
      - Prefer adapter.get_artifact_url_for_job() if present (newer adapter revision)
      - Else call get_download_link() directly (legacy)
    """
    if hasattr(api, "get_artifact_url_for_job"):
        try:
            url = await api.get_artifact_url_for_job(client, job_id)  # type: ignore[attr-defined]
            if isinstance(url, str) and url.strip():
                return url.strip()
        except Exception:
            pass

    dl = await api.get_download_link(client, job_id)
    url = dl.get("url") or dl.get("s3_url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None

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
        return {"success": True, "status": "submitted", "job_id": job_id}

    status = await _wait_for_terminal(job_id)
    state = api.get_job_status_value(status)

    # Terminal failure envelopes
    if state != "completed":
        return {
            "success": False,
            "status": state,
            "job_id": job_id,
            "repo_url": status.get("repo_url") or repo_url,
            "details": status,
        }

    artifact_url = await _get_artifact_url(api, client, job_id)

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url") or repo_url,
        "completed_at": status.get("completed_at"),
        "tokens_charged": status.get("tokens_charged"),
        "s3_url": artifact_url,
    }

    if include_full_graph:
        if not artifact_url:
            raise PvizAPIError("Job completed but artifact URL is not available")
        result["dependency_graph"] = await _download_json_streaming(artifact_url)

    return result

@mcp.tool()
async def get_analysis_status(job_id: str) -> Dict[str, Any]:
    api = _adapter()
    return await api.get_job_status(_get_client(), job_id)

@mcp.tool()
async def retrieve_past_result(job_id: str, include_full_graph: bool = True) -> Dict[str, Any]:
    api = _adapter()
    client = _get_client()

    status = await api.get_job_status(client, job_id)
    state = api.get_job_status_value(status)

    if state != "completed":
        return {
            "success": False,
            "status": state,
            "job_id": job_id,
            "message": f"Job is not completed (status={state}).",
            "details": status,
        }

    artifact_url = await _get_artifact_url(api, client, job_id)

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at"),
        "tokens_charged": status.get("tokens_charged"),
        "s3_url": artifact_url,
    }

    if include_full_graph:
        if not artifact_url:
            raise PvizAPIError("Artifact URL not available for this completed job")
        result["dependency_graph"] = await _download_json_streaming(artifact_url)

    return result

# -----------------------------------------------------------------------------
# Entrypoint (stdio/dev). For Docker HTTP deployment, run pviz_mcp_http.py
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting pviz MCP server (stdio/dev)")
    logger.info(f"API endpoint: {API_BASE_URL}")
    try:
        mcp.run()
    finally:
        try:
            asyncio.run(_close_client())
        except Exception:
            pass
