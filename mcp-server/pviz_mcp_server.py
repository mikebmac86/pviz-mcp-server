"""
pviz MCP Server - Local MCP (Production API Backend)

This MCP server runs locally (stdio) and integrates with the existing FastAPI
backend at api.pvizgenerator.com.

It exposes PViz's dependency analysis capabilities to LLMs via the MCP protocol.
"""

from __future__ import annotations

import os
import asyncio
import httpx
import random
import logging
from typing import Any, Dict, Optional, List
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
API_BASE_URL = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com").rstrip("/")

POLL_MIN_SLEEP_S = float(os.getenv("PVIZ_POLL_MIN_SLEEP", "5"))
POLL_MAX_SLEEP_S = float(os.getenv("PVIZ_POLL_MAX_SLEEP", "30"))
POLL_JITTER_RATIO = float(os.getenv("PVIZ_POLL_JITTER_RATIO", "0.25"))
MAX_POLL_ATTEMPTS = int(os.getenv("PVIZ_MAX_POLL_ATTEMPTS", "60"))

MAX_ARTIFACT_BYTES = int(os.getenv("PVIZ_MAX_ARTIFACT_BYTES", str(50 * 1024 * 1024)))
DOWNLOAD_TIMEOUT_S = float(os.getenv("PVIZ_DOWNLOAD_TIMEOUT_S", "120"))

HTTP_TIMEOUT_S = float(os.getenv("PVIZ_HTTP_TIMEOUT_S", "30"))
HTTP_CONNECT_TIMEOUT_S = float(os.getenv("PVIZ_HTTP_CONNECT_TIMEOUT_S", "10"))

# Cache hardening toggles
# - PVIZ_NO_CACHE=1 adds no-cache headers on the shared client
# - PVIZ_FORCE_FRESH_IDENTITY=1 forces a brand new client (no pooled connections)
#   for identity-sensitive endpoints like account/balance/history
NO_CACHE_DEFAULT = os.getenv("PVIZ_NO_CACHE", "1").strip().lower() not in ("0", "false", "no")
FORCE_FRESH_IDENTITY = os.getenv("PVIZ_FORCE_FRESH_IDENTITY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Note: Adapter already supports cache busting via force_fresh. We keep this
# only for any direct HTTP calls in the future.
CACHE_BUSTER_DEFAULT = os.getenv("PVIZ_CACHE_BUSTER", "1").strip().lower() not in ("0", "false", "no")

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
# Private Repo Detection
# -----------------------------------------------------------------------------
async def _handle_private_repo_error(error_detail: str, status_code: int) -> Optional[Dict[str, Any]]:
    """
    Detect if error indicates private repo and return helpful response.

    Returns None if not a private repo error, otherwise returns error dict.
    """
    private_indicators = [
        "not found",
        "repository not found",
        "could not find",
        "does not exist",
        "authentication required",
        "private",
        "access denied",
        "permission denied",
    ]

    detail_lower = (error_detail or "").lower()
    is_private = (status_code in (400, 404)) and any(indicator in detail_lower for indicator in private_indicators)

    if is_private:
        return {
            "success": False,
            "error": "private_repository",
            "message": (
                "This repository appears to be private or does not exist.\n\n"
                "To analyze private repositories, you need to provide a GitHub Personal Access Token (PAT).\n\n"
                "To create a PAT:\n"
                "1. Go to GitHub Settings → Developer Settings → Personal Access Tokens → Tokens (classic)\n"
                "2. Click 'Generate new token (classic)'\n"
                "3. Give it a descriptive name (e.g., 'Pviz Analysis')\n"
                "4. Select the 'repo' scope for full repository access\n"
                "5. Click 'Generate token' and copy it immediately\n"
                "6. Call this function again with the github_token parameter\n\n"
                "Example: analyze_repository(repo_url='owner/repo', github_token='ghp_your_token_here')"
            ),
            "requires_github_token": True,
            "help_url": "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token",
        }

    return None


# -----------------------------------------------------------------------------
# Adapter factory
# -----------------------------------------------------------------------------
def _adapter() -> PvizAPIAdapter:
    """
    Create adapter per call so it always pulls the latest token from env/file.

    NOTE:
    - PvizAPIAdapter is the single source of truth for JWT loading (env/file).
    - This module intentionally does NOT implement a parallel JWT loader.
    """
    return PvizAPIAdapter(API_BASE_URL)


# -----------------------------------------------------------------------------
# HTTP client (shared) + cache control + explicit clearing
# -----------------------------------------------------------------------------
_client: Optional[httpx.AsyncClient] = None


def _default_headers() -> Dict[str, str]:
    """
    Headers to strongly discourage caching by proxies/CDNs.
    httpx itself does not cache, but intermediate layers might.
    """
    if not NO_CACHE_DEFAULT:
        return {}

    return {
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=HTTP_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S),
        follow_redirects=True,
        headers=_default_headers(),
    )


def _get_client() -> httpx.AsyncClient:
    """
    Shared client for general use.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = _make_client()
    return _client


async def _close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_identity_client() -> httpx.AsyncClient:
    """
    Identity endpoints (account/balance/history) are the ones most likely to
    appear "wrong" if there is any caching or token drift.

    If FORCE_FRESH_IDENTITY is enabled:
      - close the shared client (clears pooled connections)
      - create a brand new client for this call
      - close it after use (via try/finally at callsite)
    """
    if not FORCE_FRESH_IDENTITY:
        return _get_client()

    await _close_client()
    return _make_client()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _normalize_repo_url(repo_url: str) -> str:
    repo_url = (repo_url or "").strip()
    if not repo_url:
        raise PvizAPIError("repo_url is required")

    # GitHub shorthand
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
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise PvizAPIError(f"Artifact exceeds size limit: {total} > {MAX_ARTIFACT_BYTES}")
            chunks.append(chunk)

    try:
        return httpx.Response(200, content=b"".join(chunks)).json()
    except Exception as e:
        raise PvizAPIError(f"Downloaded artifact is not valid JSON: {e}") from e


async def _wait_for_terminal(job_id: str) -> Dict[str, Any]:
    api = _adapter()
    client = _get_client()
    sleep_s = max(0.1, POLL_MIN_SLEEP_S)

    for _ in range(MAX_POLL_ATTEMPTS):
        status = await api.get_job_status(client, job_id)

        state = (api.get_job_status_value(status) or "").lower().strip()
        raw_state = (status.get("status") or "").lower().strip() if isinstance(status, dict) else ""

        if state in ("completed", "failed"):
            return status
        if raw_state in TERMINAL_STATES:
            return status

        jitter = (random.random() * 2 - 1) * (sleep_s * POLL_JITTER_RATIO)
        await asyncio.sleep(max(0.1, sleep_s + jitter))
        sleep_s = min(POLL_MAX_SLEEP_S, sleep_s * 1.4)

    raise PvizAPIError("Polling timeout")


def _pick_artifact_url(artifact_formats: Dict[str, Any], *, prefer: str) -> Optional[str]:
    """
    prefer: "standard" | "compressed"
    artifact_formats shape:
      {
        "standard": {"url": ...},
        "compressed": {"url": ...} | None
      }
    """
    if not isinstance(artifact_formats, dict):
        return None

    std = artifact_formats.get("standard") if isinstance(artifact_formats.get("standard"), dict) else None
    cmp_ = artifact_formats.get("compressed") if isinstance(artifact_formats.get("compressed"), dict) else None

    if prefer == "compressed":
        return (cmp_ or {}).get("url") or (std or {}).get("url")
    return (std or {}).get("url") or (cmp_ or {}).get("url")


# =============================================================================
# MCP TOOLS - CACHE / DEBUG
# =============================================================================
@mcp.tool()
async def clear_http_cache() -> Dict[str, Any]:
    """
    Clear in-process HTTP client state (connection pools, keep-alives).
    Useful after credential changes or to rule out stale pooled connections.
    """
    await _close_client()
    return {
        "success": True,
        "message": "HTTP client closed. A new client will be created on the next request.",
        "api_base_url": API_BASE_URL,
    }


@mcp.tool()
async def debug_auth_fingerprint() -> Dict[str, Any]:
    """
    Non-sensitive debug info to confirm which JWT and API base URL the MCP server is using.
    Helps catch env-vs-file token drift and wrong environment issues.

    NOTE: Token sourcing & fingerprinting are provided by PvizAPIAdapter.
    """
    api = _adapter()
    return {
        "success": True,
        "adapter_debug": api.debug_token_info(),
        "no_cache_headers_enabled": NO_CACHE_DEFAULT,
        "force_fresh_identity_enabled": FORCE_FRESH_IDENTITY,
        "cache_buster_enabled": CACHE_BUSTER_DEFAULT,
    }


@mcp.tool()
async def billing_diagnostics(limit_transactions: int = 20) -> Dict[str, Any]:
    """
    One-shot identity + billing diagnostics.

    Returns:
      - /auth/me
      - /tokens/balance
      - /tokens/overview (optional lightweight compare)
      - /tokens/transactions (recent)

    This is the best tool to run when balance looks "wrong".
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        me = await api.get_account_info(client, force_fresh=True)
        bal = await api.get_token_balance(client, force_fresh=True)
        ov = await api.get_token_overview(client, force_fresh=True)
        tx = await api.get_token_transactions(
            client,
            skip=0,
            limit=max(1, min(int(limit_transactions or 20), 200)),
            force_fresh=True,
        )

        bal_current = bal.get("current_balance") if isinstance(bal, dict) else None
        ov_current = ov.get("balance") if isinstance(ov, dict) else None

        return {
            "success": True,
            "account": me,
            "token_balance": bal,
            "token_overview": ov,
            "token_transactions": tx,
            "consistency": {
                "balance_endpoint": bal_current,
                "overview_endpoint": ov_current,
                "matches": (bal_current == ov_current) if (bal_current is not None and ov_current is not None) else None,
            },
        }
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


# =============================================================================
# MCP TOOLS - ACCOUNT & TOKENS
# =============================================================================
@mcp.tool()
async def get_account_info() -> Dict[str, Any]:
    """
    Get account information including email, plan, and verification status.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        return await api.get_account_info(client, force_fresh=True)
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


@mcp.tool()
async def get_token_balance() -> Dict[str, Any]:
    """
    Get token balance (lightweight).
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        return await api.get_token_balance(client, force_fresh=True)
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


@mcp.tool()
async def get_token_overview() -> Dict[str, Any]:
    """
    Get token overview (balance + products + trial summary).
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        return await api.get_token_overview(client, force_fresh=True)
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


@mcp.tool()
async def get_token_transactions(skip: int = 0, limit: int = 50) -> Dict[str, Any]:
    """
    Get token transaction history (ledger).
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        return await api.get_token_transactions(client, skip=skip, limit=limit, force_fresh=True)
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


@mcp.tool()
async def check_sufficient_balance(required_tokens: int) -> Dict[str, Any]:
    """
    Check if user has sufficient token balance for an operation.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        return await api.check_sufficient_balance(client, required_tokens, force_fresh=True)
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


# =============================================================================
# MCP TOOLS - COST ESTIMATION
# =============================================================================
@mcp.tool()
async def estimate_cost(
    repo_url: str,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Estimate the cost (in tokens) for analyzing a repository before submitting.
    """
    repo_url = _normalize_repo_url(repo_url)
    api = _adapter()
    client = _get_client()

    try:
        return await api.estimate_cost(client, repo_url, github_token)
    except httpx.HTTPStatusError as e:
        detail = str(e)
        try:
            error_body = e.response.json()
            detail = error_body.get("detail", detail)
        except Exception:
            pass

        private_error = await _handle_private_repo_error(detail, e.response.status_code)
        if private_error:
            return private_error

        raise


# =============================================================================
# MCP TOOLS - JOB HISTORY
# =============================================================================
@mcp.tool()
async def get_job_history(
    limit: int = 10,
    skip: int = 0,
) -> Dict[str, Any]:
    """
    Get recent job history with pagination.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        return await api.get_job_history(client, limit=limit, skip=skip, force_fresh=True)
    finally:
        if FORCE_FRESH_IDENTITY:
            try:
                await client.aclose()
            except Exception:
                pass


# =============================================================================
# MCP TOOLS - REPOSITORY ANALYSIS
# =============================================================================
@mcp.tool()
async def analyze_repository(
    repo_url: str,
    wait_for_completion: bool = True,
    include_full_graph: bool = False,
    pricing_choice: str = "tokens",
    questions: Optional[List[str]] = None,
    github_token: Optional[str] = None,
    prefer_artifact: str = "compressed",  # "standard" | "compressed"
) -> Dict[str, Any]:
    """
    Submit a repository for dependency analysis.

    NOTE:
      - Legacy /download-link shim is removed.
      - Artifacts are resolved via:
          (A) GET /jobs/{id} -> artifact_formats
          (B) GET /jobs/{id}/artifact-links?prefer=...
    """
    repo_url = _normalize_repo_url(repo_url)
    api = _adapter()
    client = _get_client()

    try:
        submit = await api.submit_analysis(
            client,
            repo_url=repo_url,
            pricing_choice=pricing_choice,
            questions=questions,
            github_token=github_token,
        )
    except httpx.HTTPStatusError as e:
        detail = str(e)
        try:
            error_body = e.response.json()
            detail = error_body.get("detail", detail)
        except Exception:
            pass

        private_error = await _handle_private_repo_error(detail, e.response.status_code)
        if private_error:
            return private_error

        raise

    job_id = api.extract_job_id(submit)

    if not wait_for_completion:
        return {"success": True, "status": "submitted", "job_id": job_id, "repo_url": repo_url}

    status = await _wait_for_terminal(job_id)

    raw_status = (status.get("status") or "").lower().strip()
    normalized = (api.get_job_status_value(status) or "").lower().strip()

    is_completed = (raw_status == "completed") or (normalized == "completed")
    if not is_completed:
        return {"success": False, "status": raw_status or normalized or "unknown", "details": status}

    artifacts = await api.get_job_artifacts(client, job_id, prefer="both", force_fresh=True)
    artifact_formats = artifacts.get("artifact_formats") if isinstance(artifacts, dict) else None

    preferred_url = _pick_artifact_url(artifact_formats or {}, prefer=prefer_artifact)

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at") or datetime.utcnow().isoformat(),
        "tokens_charged": status.get("tokens_charged"),
        "artifact_formats": artifact_formats,
        "artifact_url": preferred_url,
        "artifact_source": artifacts.get("source") if isinstance(artifacts, dict) else None,
    }

    if include_full_graph and preferred_url:
        result["dependency_graph"] = await _download_json_streaming(preferred_url)

    return result


@mcp.tool()
async def get_analysis_status(job_id: str) -> Dict[str, Any]:
    """
    Get the current status of an analysis job.
    """
    api = _adapter()
    return await api.get_job_status(_get_client(), job_id, force_fresh=True)


@mcp.tool()
async def retrieve_past_result(
    job_id: str,
    include_full_graph: bool = True,
    prefer_artifact: str = "compressed",  # "standard" | "compressed"
) -> Dict[str, Any]:
    """
    Retrieve results from a completed analysis job.

    NOTE:
      - Legacy /download-link shim is removed.
      - Artifacts are resolved via:
          (A) GET /jobs/{id} -> artifact_formats
          (B) GET /jobs/{id}/artifact-links?prefer=...
    """
    api = _adapter()
    client = _get_client()

    status = await api.get_job_status(client, job_id, force_fresh=True)
    raw_status = (status.get("status") or "").lower().strip()

    if raw_status != "completed":
        return {"success": False, "status": raw_status or "unknown", "details": status}

    artifacts = await api.get_job_artifacts(client, job_id, prefer="both", force_fresh=True)
    artifact_formats = artifacts.get("artifact_formats") if isinstance(artifacts, dict) else None

    preferred_url = _pick_artifact_url(artifact_formats or {}, prefer=prefer_artifact)

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at"),
        "tokens_charged": status.get("tokens_charged"),
        "artifact_formats": artifact_formats,
        "artifact_url": preferred_url,
        "artifact_source": artifacts.get("source") if isinstance(artifacts, dict) else None,
    }

    if include_full_graph and preferred_url:
        result["dependency_graph"] = await _download_json_streaming(preferred_url)

    return result


# =============================================================================
# Entrypoint (stdio)
# =============================================================================
if __name__ == "__main__":
    logger.info("Starting pviz MCP server (stdio/local)")
    try:
        # Helpful one-time startup log (non-sensitive)
        try:
            api = _adapter()
            dbg = api.debug_token_info()
            logger.info(
                "MCP config: api_base=%s token_source=%s token_fp=%s no_cache=%s force_fresh_identity=%s cache_buster=%s",
                dbg.get("base_url"),
                dbg.get("token_source"),
                dbg.get("token_fingerprint"),
                NO_CACHE_DEFAULT,
                FORCE_FRESH_IDENTITY,
                CACHE_BUSTER_DEFAULT,
            )
        except Exception as e:
            logger.warning("MCP config debug unavailable: %s", e)

        mcp.run()
    finally:
        try:
            asyncio.run(_close_client())
        except Exception:
            pass
