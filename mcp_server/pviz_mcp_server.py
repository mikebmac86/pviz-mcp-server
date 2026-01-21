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
# Field Guide for Summary Sections
# -----------------------------------------------------------------------------
SUMMARY_FIELD_GUIDE = {
    "counts": "Basic counts of modules, edges, zones, and files",
    "loc": "Lines of code metrics and largest modules",
    "parse_status": "How many files parsed successfully vs errors",
    "edges": "Import/dependency relationship statistics",
    "hotspots": "Most central modules (importers/dependencies)",
    "zones": "Architectural zones or packages",
    "cycles": "Circular dependency analysis",
    "crosstalk": "Cross-language/boundary dependencies (env vars, HTTP, etc.)",
    "api_surface": "Function/class counts and documentation coverage",
    "environment": "Environment variables inventory and detected secrets",
    "http_contracts": "Backend API routes vs frontend calls with coverage",
    "testing": "Test file detection and test-to-source ratio",
    "change_risk": "Modules ranked by change impact (blast radius)",
    "imports": "Import statistics and third-party package usage",
    "meta": "Repository metadata and analysis info",
}

# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
class PvizAPIError(Exception):
    pass


class PvizErrorCode:
    # Generic / validation
    INVALID_PARAMETERS = "invalid_parameters"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    NETWORK_ERROR = "network_error"

    # Job lifecycle
    JOB_NOT_COMPLETED = "job_not_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCELED = "job_canceled"
    INSUFFICIENT_TOKENS = "insufficient_tokens"
    AWAITING_PAYMENT = "awaiting_payment"
    POLLING_TIMEOUT = "polling_timeout"

    # Artifact
    ARTIFACT_UNAVAILABLE = "artifact_unavailable"
    ARTIFACT_TOO_LARGE = "artifact_too_large"
    ARTIFACT_DOWNLOAD_FAILED = "artifact_download_failed"

    # Repo
    PRIVATE_REPOSITORY = "private_repository"


def _next_step(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Machine-readable hint for the next tool call."""
    return {"tool": tool_name, "args": args}


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
            "error_code": PvizErrorCode.PRIVATE_REPOSITORY,
            "error": "private_repository",
            "message": (
                "This repository appears to be private or does not exist.\n\n"
                "To analyze private repositories, provide a GitHub Personal Access Token (PAT).\n\n"
                "To create a PAT:\n"
                "1. GitHub Settings → Developer Settings → Personal Access Tokens → Tokens (classic)\n"
                "2. Generate new token (classic)\n"
                "3. Name it (e.g., 'Pviz Analysis')\n"
                "4. Select the 'repo' scope\n"
                "5. Generate and copy the token\n"
                "6. Call analyze_repository() again with github_token\n\n"
                "Example: analyze_repository(repo_url='owner/repo', github_token='ghp_...')"
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
    """Normalize and validate repository URL."""
    repo_url = (repo_url or "").strip()
    if not repo_url:
        raise PvizAPIError("repo_url is required and cannot be empty")

    # GitHub shorthand
    if "://" not in repo_url and repo_url.count("/") == 1:
        return f"https://github.com/{repo_url}"

    parsed = urlparse(repo_url)
    if not parsed.scheme or not parsed.netloc:
        raise PvizAPIError(f"Invalid repo_url format: '{repo_url}'. Expected full URL or 'owner/repo' shorthand.")
    return repo_url


def _validate_prefer_artifact(prefer: str) -> str:
    """Validate prefer_artifact parameter."""
    prefer = (prefer or "").strip().lower()
    if prefer not in ("standard", "compressed"):
        raise PvizAPIError(
            f"Invalid prefer_artifact value: '{prefer}'. Must be 'standard' or 'compressed'."
        )
    return prefer


async def _download_json_streaming(url: str) -> Dict[str, Any]:
    """Download and parse JSON artifact with size limits."""
    client = _get_client()
    total = 0
    chunks: List[bytes] = []

    try:
        async with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_ARTIFACT_BYTES:
                    raise PvizAPIError(
                        f"Artifact exceeds size limit: {total} bytes > {MAX_ARTIFACT_BYTES} bytes"
                    )
                chunks.append(chunk)
    except httpx.HTTPStatusError as e:
        raise PvizAPIError(f"Failed to download artifact: HTTP {e.response.status_code}") from e
    except httpx.TimeoutException as e:
        raise PvizAPIError(f"Artifact download timed out after {DOWNLOAD_TIMEOUT_S}s") from e

    try:
        return httpx.Response(200, content=b"".join(chunks)).json()
    except Exception as e:
        raise PvizAPIError(f"Downloaded artifact is not valid JSON: {e}") from e


def _extract_summary_from_artifact(artifact: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract summary section from artifact.

    Returns None if summary not found or artifact is invalid.
    """
    if not isinstance(artifact, dict):
        return None

    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        return None

    return summary


def _terminal_error_code(raw_status: str) -> str:
    s = (raw_status or "").lower().strip()
    if s in ("failed",):
        return PvizErrorCode.JOB_FAILED
    if s in ("canceled", "cancelled"):
        return PvizErrorCode.JOB_CANCELED
    if s == "insufficient_tokens":
        return PvizErrorCode.INSUFFICIENT_TOKENS
    if s == "awaiting_payment":
        return PvizErrorCode.AWAITING_PAYMENT
    # fallback
    return PvizErrorCode.JOB_FAILED


async def _wait_for_terminal(job_id: str) -> Dict[str, Any]:
    """Poll job status until terminal state or timeout."""
    api = _adapter()
    client = _get_client()
    sleep_s = max(0.1, POLL_MIN_SLEEP_S)

    for attempt in range(MAX_POLL_ATTEMPTS):
        status = await api.get_job_status(client, job_id)

        state = (api.get_job_status_value(status) or "").lower().strip()
        raw_state = (status.get("status") or "").lower().strip() if isinstance(status, dict) else ""

        if state in ("completed", "failed"):
            return status
        if raw_state in TERMINAL_STATES:
            return status

        # Exponential backoff with jitter
        jitter = (random.random() * 2 - 1) * (sleep_s * POLL_JITTER_RATIO)
        await asyncio.sleep(max(0.1, sleep_s + jitter))
        sleep_s = min(POLL_MAX_SLEEP_S, sleep_s * 1.4)

    # NOTE: this is the behavior that caused “impatient duplicate submits”.
    # Make the guidance unambiguous.
    raise PvizAPIError(
        f"Polling timeout after {MAX_POLL_ATTEMPTS} attempts. "
        f"Job may still be running. NEXT STEP: use get_analysis_status(job_id='{job_id}'). "
        f"DO NOT submit a duplicate analysis for the same repo while this job is running."
    )


def _pick_artifact_url(artifact_formats: Dict[str, Any], *, prefer: str) -> tuple[Optional[str], str]:
    """
    Pick artifact URL based on preference.

    Returns: (url, actual_format_used)
    Raises: PvizAPIError if requested format is missing
    """
    if not isinstance(artifact_formats, dict):
        return None, "none"

    std = artifact_formats.get("standard") if isinstance(artifact_formats.get("standard"), dict) else None
    cmp_ = artifact_formats.get("compressed") if isinstance(artifact_formats.get("compressed"), dict) else None

    if prefer == "compressed":
        if not cmp_:
            raise PvizAPIError(
                f"Compressed artifact requested but not available. "
                f"Standard format available: {std is not None}. "
                f"This may indicate a job processing issue. Try prefer_artifact='standard'."
            )
        return cmp_.get("url"), "compressed"

    if not std:
        raise PvizAPIError(
            f"Standard artifact requested but not available. "
            f"Compressed format available: {cmp_ is not None}. "
            f"This may indicate a job processing issue. Try prefer_artifact='compressed'."
        )
    return std.get("url"), "standard"


# =============================================================================
# MCP TOOLS - CACHE / DEBUG
# =============================================================================
@mcp.tool()
async def clear_http_cache() -> Dict[str, Any]:
    """
    Clear in-process HTTP client state (connection pools, keep-alives).

    USE THIS TOOL WHEN:
    - You changed JWT credentials (env/file) and want to ensure a fresh client.
    - You suspect pooled connections or intermediate caching is causing stale identity/billing data.

    RETURNS:
    - success + a short message. The next API call will create a new HTTP client.
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

    USE THIS TOOL WHEN:
    - Token/billing/account info looks wrong
    - You suspect env-vs-file token drift
    - You want to confirm MCP is pointed at the right API base URL

    RETURNS:
    - adapter_debug: token source and fingerprint (no secret)
    - configuration: key runtime settings
    """
    api = _adapter()
    return {
        "success": True,
        "adapter_debug": api.debug_token_info(),
        "configuration": {
            "api_base_url": API_BASE_URL,
            "no_cache_headers_enabled": NO_CACHE_DEFAULT,
            "force_fresh_identity_enabled": FORCE_FRESH_IDENTITY,
            "cache_buster_enabled": CACHE_BUSTER_DEFAULT,
            "max_poll_attempts": MAX_POLL_ATTEMPTS,
            "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        },
    }


@mcp.tool()
async def billing_diagnostics(limit_transactions: int = 20) -> Dict[str, Any]:
    """
    One-shot identity + billing diagnostics.

    USE THIS TOOL WHEN:
    - Your token balance looks "wrong" or inconsistent between endpoints
    - You want a single call that compares identity + multiple balance/ledger sources

    RETURNS:
      - /auth/me
      - /tokens/balance
      - /tokens/overview
      - /tokens/transactions (recent)
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

    USE THIS TOOL WHEN:
    - You need to confirm which account the current JWT corresponds to.
    - You’re debugging token drift / wrong-identity issues.

    RETURNS:
    - Account payload from PViz API, or a structured error with suggestion.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        result = await api.get_account_info(client, force_fresh=True)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve account information",
            "details": str(e),
            "suggestion": "Check your authentication with debug_auth_fingerprint()",
        }
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

    USE THIS TOOL WHEN:
    - You need current balance quickly without ledger details.

    RETURNS:
    - Balance payload from PViz API, or a structured error.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        result = await api.get_token_balance(client, force_fresh=True)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve token balance",
            "details": str(e),
            "suggestion": "Check your authentication with debug_auth_fingerprint()",
        }
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

    USE THIS TOOL WHEN:
    - You want balance + product/trial context.

    RETURNS:
    - Overview payload from PViz API, or a structured error.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        result = await api.get_token_overview(client, force_fresh=True)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve token overview",
            "details": str(e),
            "suggestion": "Check your authentication with debug_auth_fingerprint()",
        }
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

    USE THIS TOOL WHEN:
    - You want recent charges/credits and their reasons.

    RETURNS:
    - Ledger payload from PViz API, or a structured error.
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        result = await api.get_token_transactions(client, skip=skip, limit=limit, force_fresh=True)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve token transactions",
            "details": str(e),
            "suggestion": "Check your authentication with debug_auth_fingerprint()",
        }
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

    USE THIS TOOL WHEN:
    - You want to validate the user can afford an operation before submitting it.

    RETURNS:
    - Backend decision payload (sufficient / insufficient), or structured error.
    """
    if not isinstance(required_tokens, int) or required_tokens < 0:
        return {
            "success": False,
            "error_code": PvizErrorCode.INVALID_PARAMETERS,
            "error": "Invalid parameter",
            "details": "required_tokens must be a positive integer",
        }

    api = _adapter()
    client = await _get_identity_client()
    try:
        result = await api.check_sufficient_balance(client, required_tokens, force_fresh=True)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to check balance",
            "details": str(e),
            "suggestion": "Check your authentication with debug_auth_fingerprint()",
        }
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

    USE THIS TOOL WHEN:
    - User asks “how many tokens will this cost?”
    - You want to show expected token charge before starting an analysis.

    RETURNS:
    - Token estimate payload, or structured error.
    """
    try:
        repo_url = _normalize_repo_url(repo_url)
    except PvizAPIError as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.INVALID_PARAMETERS,
            "error": "Invalid repository URL",
            "details": str(e),
        }

    api = _adapter()
    client = _get_client()

    try:
        result = await api.estimate_cost(client, repo_url, github_token)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
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

        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": f"HTTP {e.response.status_code}",
            "details": detail,
        }
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to estimate cost",
            "details": str(e),
        }


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

    USE THIS TOOL WHEN:
    - User asks “what were my recent jobs?”
    - You need to find a job_id to use with retrieve_past_result().

    RETURNS:
    - Job history list with paging metadata (backend-defined).
    """
    api = _adapter()
    client = await _get_identity_client()
    try:
        result = await api.get_job_history(client, limit=limit, skip=skip, force_fresh=True)
        if not isinstance(result, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": result,
            }
        return result
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve job history",
            "details": str(e),
            "suggestion": "Check your authentication with debug_auth_fingerprint()",
        }
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

    WHEN TO USE THIS TOOL:
    - User asks to analyze a repository / “run PViz” / “scan this repo”
    - User wants a fresh analysis job (not retrieving an old one)

    ⚠️ IMPORTANT — WAIT / POLLING BEHAVIOR:
    - If wait_for_completion=True (default):
        This tool BLOCKS and automatically polls the backend for up to:
        MAX_POLL_ATTEMPTS × (roughly 5–30s backoff)  (commonly ~1–5 minutes).
        DO NOT call analyze_repository again for the same repo while waiting.
        DO NOT manually poll with get_analysis_status() while this call is running.
        If it times out, you will receive a timeout response containing job_id;
        then use get_analysis_status(job_id=...) to continue monitoring.
    - If wait_for_completion=False:
        Returns immediately with a job_id. You MUST poll using get_analysis_status(job_id)
        until status becomes a terminal state.

    EXAMPLE QUERIES:
    - "Analyze https://github.com/pallets/flask"
    - "Run PViz on owner/repo"
    - "Analyze this repo and give me a summary"

    Args:
        repo_url: Repository URL or 'owner/repo' shorthand for GitHub
        wait_for_completion: If True (default), blocks and polls until done (or timeout).
                            If False, returns job_id immediately (manual polling required).
        include_full_graph: If True, download and include full dependency graph (large payload).
        pricing_choice: Payment method ("tokens" or other backend options)
        questions: Optional list of questions to ask about the repository
        github_token: GitHub PAT for private repositories
        prefer_artifact: "standard" or "compressed" format

    Returns:
        - On submission-only: {success, status="submitted", job_id, next_step}
        - On completion: {success, status="completed", summary, (optional) dependency_graph, artifact_url}
        - On timeout: structured error with job_id + next_step
    """
    try:
        repo_url = _normalize_repo_url(repo_url)
        prefer_artifact = _validate_prefer_artifact(prefer_artifact)
    except PvizAPIError as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.INVALID_PARAMETERS,
            "error": "Invalid parameters",
            "details": str(e),
        }

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

        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": f"HTTP {e.response.status_code}",
            "details": detail,
        }
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to submit analysis",
            "details": str(e),
        }

    job_id = api.extract_job_id(submit)

    if not wait_for_completion:
        return {
            "success": True,
            "status": "submitted",
            "job_id": job_id,
            "repo_url": repo_url,
            "message": "Analysis submitted. Poll with get_analysis_status(job_id=...) until completed.",
            "next_step": _next_step("get_analysis_status", {"job_id": job_id}),
        }

    try:
        status = await _wait_for_terminal(job_id)
    except PvizAPIError as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.POLLING_TIMEOUT,
            "status": "timeout",
            "job_id": job_id,
            "error": str(e),
            "message": "Polling timed out. The job may still be running. Do not resubmit; check status instead.",
            "next_step": _next_step("get_analysis_status", {"job_id": job_id}),
        }

    raw_status = (status.get("status") or "").lower().strip()
    normalized = (api.get_job_status_value(status) or "").lower().strip()

    is_completed = (raw_status == "completed") or (normalized == "completed")
    if not is_completed:
        terminal = raw_status or normalized or "unknown"
        return {
            "success": False,
            "error_code": _terminal_error_code(terminal),
            "status": terminal,
            "job_id": job_id,
            "details": status,
            "message": f"Analysis ended in non-completed state: {terminal}",
            "next_step": _next_step("get_analysis_status", {"job_id": job_id}),
        }

    try:
        artifacts = await api.get_job_artifacts(client, job_id, prefer="both", force_fresh=True)
        artifact_formats = artifacts.get("artifact_formats") if isinstance(artifacts, dict) else None
        preferred_url, actual_format = _pick_artifact_url(artifact_formats or {}, prefer=prefer_artifact)
    except PvizAPIError as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.ARTIFACT_UNAVAILABLE,
            "status": "completed",
            "job_id": job_id,
            "error": "Artifact retrieval failed",
            "details": str(e),
            "message": "Job completed, but artifacts were unavailable in the requested format.",
        }

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at") or datetime.utcnow().isoformat(),
        "tokens_charged": status.get("tokens_charged"),
        "artifact_formats": artifact_formats,
        "artifact_url": [preferred_url, actual_format],  # Keep for backward compatibility
        "artifact_source": artifacts.get("source") if isinstance(artifacts, dict) else None,
    }

    # Download and include summary
    if preferred_url:
        try:
            full_artifact = await _download_json_streaming(preferred_url)

            summary = _extract_summary_from_artifact(full_artifact)
            if summary:
                result["summary"] = summary
                result["_field_guide"] = SUMMARY_FIELD_GUIDE

            if include_full_graph:
                result["dependency_graph"] = full_artifact
        except PvizAPIError as e:
            logger.warning(f"Failed to download artifact for summary extraction: {e}")
            result["summary"] = None
            result["error_code"] = PvizErrorCode.ARTIFACT_DOWNLOAD_FAILED
            result["summary_error"] = str(e)
            result["message"] = "Artifact download failed; summary could not be extracted."
    else:
        result["summary"] = None
        result["error_code"] = PvizErrorCode.ARTIFACT_UNAVAILABLE
        result["message"] = "No artifact URL available to extract summary."

    return result


@mcp.tool()
async def get_analysis_status(job_id: str) -> Dict[str, Any]:
    """
    Get the current status of an analysis job.

    WHEN TO USE THIS TOOL:
    - You have a job_id and want to check progress / final status.
    - analyze_repository(wait_for_completion=False) returned a job_id.
    - analyze_repository() timed out and told you to check status manually.

    EXAMPLE QUERIES:
    - "What’s the status of job abc123?"
    - "Is my analysis done yet?"

    Returns:
    - Backend job status payload with additional helpful message + next_step hints when relevant.
    """
    if not job_id or not isinstance(job_id, str):
        return {
            "success": False,
            "error_code": PvizErrorCode.INVALID_PARAMETERS,
            "error": "Invalid parameter",
            "details": "job_id must be a non-empty string",
        }

    api = _adapter()
    try:
        status = await api.get_job_status(_get_client(), job_id, force_fresh=True)
        if not isinstance(status, dict):
            return {
                "success": False,
                "error_code": PvizErrorCode.NETWORK_ERROR,
                "error": "Invalid response from API",
                "details": status,
            }

        raw_status = (status.get("status") or "").lower().strip()

        # Add machine-readable guidance
        if raw_status == "in_progress":
            status["message"] = (
                "Analysis is still running. Keep polling this tool with the same job_id. "
                "Do not submit a new analysis for the same repo while this is running."
            )
            status["next_step"] = _next_step("get_analysis_status", {"job_id": job_id})
            status["error_code"] = PvizErrorCode.JOB_NOT_COMPLETED
        elif raw_status in TERMINAL_FAILURE:
            status["message"] = f"Analysis ended with terminal failure status: {raw_status}"
            status["error_code"] = _terminal_error_code(raw_status)
        elif raw_status == "completed":
            status["message"] = "Analysis completed. Use retrieve_past_result(job_id=...) to fetch results."
            status["next_step"] = _next_step("retrieve_past_result", {"job_id": job_id, "include_full_graph": False})
        else:
            # Unknown status, still provide a safe next step
            status["message"] = f"Status: {raw_status or 'unknown'}. You can poll again."
            status["next_step"] = _next_step("get_analysis_status", {"job_id": job_id})

        return status
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve job status",
            "details": str(e),
            "job_id": job_id,
        }


@mcp.tool()
async def retrieve_past_result(
    job_id: str,
    include_full_graph: bool = True,
    prefer_artifact: str = "compressed",  # "standard" | "compressed"
) -> Dict[str, Any]:
    """
    Retrieve results from a completed analysis job.

    WHEN TO USE THIS TOOL:
    - User asks to “get/show/retrieve” results for a prior job_id
    - You already have a completed job_id and want summary or full artifact

    IMPORTANT:
    - If the job is not completed yet, this tool will NOT retry analysis; it will
      tell you the current status and point you to get_analysis_status(job_id=...).

    EXAMPLE QUERIES:
    - "Show me results for job abc123"
    - "Get the summary for my last analysis"
    - "Retrieve the full graph for job abc123"

    Args:
        job_id: The job ID from a previous analysis
        include_full_graph: If True, download and include full dependency graph (can be large)
        prefer_artifact: "standard" or "compressed" format

    Returns:
        - On success: summary (+ optional dependency_graph) and artifact_url
        - If not completed: structured response with next_step=get_analysis_status
    """
    if not job_id or not isinstance(job_id, str):
        return {
            "success": False,
            "error_code": PvizErrorCode.INVALID_PARAMETERS,
            "error": "Invalid parameter",
            "details": "job_id must be a non-empty string",
        }

    try:
        prefer_artifact = _validate_prefer_artifact(prefer_artifact)
    except PvizAPIError as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.INVALID_PARAMETERS,
            "error": "Invalid parameter",
            "details": str(e),
        }

    api = _adapter()
    client = _get_client()

    try:
        status = await api.get_job_status(client, job_id, force_fresh=True)
        raw_status = (status.get("status") or "").lower().strip()

        if raw_status != "completed":
            # Treat as a controlled, guided non-terminal response (not a “hard error”).
            return {
                "success": True,
                "status": raw_status or "unknown",
                "job_id": job_id,
                "error_code": PvizErrorCode.JOB_NOT_COMPLETED,
                "message": (
                    f"Job is not completed (current status: {raw_status or 'unknown'}). "
                    "NEXT STEP: use get_analysis_status(job_id=...) to monitor progress."
                ),
                "details": status,
                "next_step": _next_step("get_analysis_status", {"job_id": job_id}),
            }
    except Exception as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.NETWORK_ERROR,
            "error": "Failed to retrieve job status",
            "details": str(e),
            "job_id": job_id,
            "suggestion": "Verify the job_id is correct with get_job_history()",
            "next_step": _next_step("get_job_history", {"limit": 10, "skip": 0}),
        }

    try:
        artifacts = await api.get_job_artifacts(client, job_id, prefer=prefer_artifact, force_fresh=True)
        artifact_formats = artifacts.get("artifact_formats") if isinstance(artifacts, dict) else None
        preferred_url, actual_format = _pick_artifact_url(artifact_formats or {}, prefer=prefer_artifact)
    except PvizAPIError as e:
        return {
            "success": False,
            "error_code": PvizErrorCode.ARTIFACT_UNAVAILABLE,
            "status": "completed",
            "job_id": job_id,
            "error": "Artifact retrieval failed",
            "details": str(e),
        }

    result: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": status.get("repo_url"),
        "completed_at": status.get("completed_at"),
        "tokens_charged": status.get("tokens_charged"),
        "artifact_formats": artifact_formats,
        "artifact_url": [preferred_url, actual_format],  # Keep for backward compatibility
        "artifact_source": artifacts.get("source") if isinstance(artifacts, dict) else None,
    }

    if preferred_url:
        try:
            full_artifact = await _download_json_streaming(preferred_url)

            summary = _extract_summary_from_artifact(full_artifact)
            if summary:
                result["summary"] = summary
                result["_field_guide"] = SUMMARY_FIELD_GUIDE

            if include_full_graph:
                result["dependency_graph"] = full_artifact
        except PvizAPIError as e:
            logger.warning(f"Failed to download artifact for job {job_id}: {e}")
            result["error_code"] = (
                PvizErrorCode.ARTIFACT_TOO_LARGE
                if "exceeds size limit" in str(e).lower()
                else PvizErrorCode.ARTIFACT_DOWNLOAD_FAILED
            )
            result["summary"] = None
            result["summary_error"] = str(e)
            result["message"] = "Artifact download failed; summary could not be extracted."
    else:
        result["error_code"] = PvizErrorCode.ARTIFACT_UNAVAILABLE
        result["summary"] = None
        result["message"] = "No artifact URL available to extract summary."

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
