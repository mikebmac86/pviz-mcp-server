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

# MCP SDK imports
from mcp.server.fastmcp import FastMCP

# Import API adapter (centralized backend contract)
from api_adapter import PvizAPIAdapter

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger("pviz-mcp")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# -----------------------------------------------------------------------------
# MCP server init
# -----------------------------------------------------------------------------
mcp = FastMCP("pviz-dependency-analyzer")

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
API_BASE_URL = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com")
JWT_TOKEN = os.getenv("PVIZ_JWT_TOKEN")  # user's JWT token for API access

# Polling controls
POLLING_INTERVAL = int(os.getenv("PVIZ_POLL_INTERVAL", "5"))  # seconds
MAX_POLL_ATTEMPTS = int(os.getenv("PVIZ_MAX_POLL_ATTEMPTS", "60"))  # 5 min max

# Backoff controls for status polling (helps avoid synchronized polling)
POLL_MIN_SLEEP_S = float(os.getenv("PVIZ_POLL_MIN_SLEEP", str(POLLING_INTERVAL)))
POLL_MAX_SLEEP_S = float(os.getenv("PVIZ_POLL_MAX_SLEEP", "30"))
POLL_JITTER_RATIO = float(os.getenv("PVIZ_POLL_JITTER_RATIO", "0.25"))

# Download controls
MAX_ARTIFACT_BYTES = int(os.getenv("PVIZ_MAX_ARTIFACT_BYTES", str(50 * 1024 * 1024)))  # 50 MB default
DOWNLOAD_TIMEOUT_S = float(os.getenv("PVIZ_DOWNLOAD_TIMEOUT_S", "120"))

# httpx client controls
HTTP_TIMEOUT_S = float(os.getenv("PVIZ_HTTP_TIMEOUT_S", "30"))
HTTP_CONNECT_TIMEOUT_S = float(os.getenv("PVIZ_HTTP_CONNECT_TIMEOUT_S", "10"))
HTTP_POOL_MAX_CONN = int(os.getenv("PVIZ_HTTP_MAX_CONNECTIONS", "50"))
HTTP_POOL_MAX_KEEPALIVE = int(os.getenv("PVIZ_HTTP_MAX_KEEPALIVE", "20"))

# Status values (backend-aligned, but tolerant)
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
    """Custom exception for pviz API errors."""


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _require_jwt() -> str:
    if not JWT_TOKEN:
        raise PvizAPIError("PVIZ_JWT_TOKEN environment variable not set")
    return JWT_TOKEN


def _adapter() -> PvizAPIAdapter:
    """Return a configured API adapter (centralized backend contract)."""
    token = _require_jwt()
    return PvizAPIAdapter(base_url=API_BASE_URL, jwt_token=token)


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """
    Shared AsyncClient for connection pooling across tool calls.
    """
    global _client
    if _client is None or _client.is_closed:
        timeout = httpx.Timeout(
            timeout=HTTP_TIMEOUT_S,
            connect=HTTP_CONNECT_TIMEOUT_S,
            read=HTTP_TIMEOUT_S,
            write=HTTP_TIMEOUT_S,
            pool=HTTP_TIMEOUT_S,
        )
        limits = httpx.Limits(max_connections=HTTP_POOL_MAX_CONN, max_keepalive_connections=HTTP_POOL_MAX_KEEPALIVE)
        _client = httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True)
    return _client


async def _close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        finally:
            _client = None


def _normalize_repo_url(repo_url: str) -> str:
    """
    Light validation/normalization.
    Accepts full https URLs or GitHub shorthand 'owner/repo'.
    """
    repo_url = (repo_url or "").strip()
    if not repo_url:
        raise PvizAPIError("repo_url is required")

    # Shorthand: "owner/repo"
    if "://" not in repo_url and repo_url.count("/") == 1:
        return f"https://github.com/{repo_url}"

    parsed = urlparse(repo_url)
    if parsed.scheme not in ("https", "http"):
        raise PvizAPIError(f"Unsupported repo_url scheme: {parsed.scheme!r}")
    if not parsed.netloc:
        raise PvizAPIError("repo_url missing host")

    # Prefer https for safety; keep http only if explicitly provided.
    if parsed.scheme == "http":
        logger.warning("repo_url uses http; https is recommended")

    return repo_url


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    except Exception:
        pass
    return default


def _extract_error_message(payload: MappingLike) -> str:
    for k in ("error", "message", "detail", "reason"):
        v = payload.get(k) if isinstance(payload, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "Unknown error"


class MappingLike(dict):
    pass


def _raise_for_httpx_submit(e: httpx.HTTPStatusError) -> None:
    status_code = getattr(e.response, "status_code", None)
    if status_code in (401, 403):
        raise PvizAPIError("Unauthorized request (check PVIZ_JWT_TOKEN)") from e
    raise PvizAPIError(f"Backend error (HTTP {status_code}): {e}") from e


def _raise_for_httpx_poll(e: httpx.HTTPStatusError, job_id: str) -> None:
    status_code = getattr(e.response, "status_code", None)
    if status_code in (401, 403):
        raise PvizAPIError("Unauthorized polling request (check PVIZ_JWT_TOKEN)") from e
    if status_code == 404:
        raise PvizAPIError(f"Unknown job_id: {job_id}") from e
    # transient-ish
    logger.warning(f"HTTP status error while polling job {job_id}: HTTP {status_code}: {e}")


async def _download_json_streaming(url: str) -> Dict[str, Any]:
    """
    Download JSON from a URL with streaming + size cap.
    """
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
                    f"Artifact too large ({total} bytes) exceeds PVIZ_MAX_ARTIFACT_BYTES={MAX_ARTIFACT_BYTES}. "
                    "Use summary-only mode or increase the limit."
                )
            chunks.append(chunk)

    raw = b"".join(chunks)
    try:
        return httpx.Response(200, content=raw).json()
    except Exception as e:
        raise PvizAPIError(f"Downloaded artifact is not valid JSON: {e}") from e


async def _wait_for_analysis(job_id: str, *, max_attempts: int = MAX_POLL_ATTEMPTS) -> Dict[str, Any]:
    """
    Poll backend until analysis reaches a terminal state.

    Returns the final status payload (terminal).
    Raises for transport/auth issues.
    """
    api = _adapter()
    client = _get_client()

    sleep_s = max(0.1, POLL_MIN_SLEEP_S)

    for attempt in range(max_attempts):
        try:
            status_data = await api.get_job_status(client, job_id)
            state = api.get_job_status_value(status_data) or "unknown"

            if state in TERMINAL_STATES:
                logger.info(f"Analysis {job_id} reached terminal state: {state}")
                return status_data

            logger.info(f"Analysis {job_id} still {state}, waiting... ({attempt + 1}/{max_attempts})")

        except httpx.HTTPStatusError as e:
            _raise_for_httpx_poll(e, job_id)
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error while polling job {job_id}: {e}")

        # Sleep with jitter, then increase towards max.
        jitter = (random.random() * 2 - 1) * (sleep_s * POLL_JITTER_RATIO)
        await asyncio.sleep(max(0.1, sleep_s + jitter))
        sleep_s = min(POLL_MAX_SLEEP_S, max(POLL_MIN_SLEEP_S, sleep_s * 1.4))

    raise PvizAPIError(
        f"Analysis timed out after {max_attempts} attempts "
        f"(~{max_attempts * max(0.1, POLL_MIN_SLEEP_S):.0f}s min)."
    )


async def _submit_analysis(
    *,
    repo_url: str,
    languages: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
    pricing_choice: Optional[str] = None,
    questions: Optional[List[str]] = None,
    github_token: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    One canonical submit path that tolerates adapter signature drift.

    Returns: (job_id, raw_submit_payload)
    """
    api = _adapter()
    client = _get_client()

    # Prefer keyword-based call; if adapter differs, fall back carefully.
    try:
        submit_payload = await api.submit_analysis(
            client,
            repo_url=repo_url,
            languages=languages,
            options=options,
            pricing_choice=pricing_choice,
            questions=questions,
            github_token=github_token,
        )
    except TypeError:
        # Adapter might not accept some kwargs depending on backend variant.
        # Retry with the minimal, most common subset.
        submit_payload = await api.submit_analysis(
            client,
            repo_url=repo_url,
            languages=languages,
            options=options,
        )
    except httpx.HTTPStatusError as e:
        _raise_for_httpx_submit(e)
    except httpx.HTTPError as e:
        raise PvizAPIError(f"Failed to submit analysis: {e}") from e

    try:
        job_id = api.extract_job_id(submit_payload)
    except Exception as e:
        raise PvizAPIError(f"API did not return a job ID: {submit_payload}") from e

    return job_id, submit_payload


# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool()
async def analyze_repository(
    repo_url: str,
    languages: Optional[List[str]] = None,
    wait_for_completion: bool = True,
    include_full_graph: bool = False,
    pricing_choice: Optional[str] = None,
    questions: Optional[List[str]] = None,
    github_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze a Git repository and return its dependency graph.

    Args:
        repo_url: Git repository URL (or 'owner/repo' shorthand for GitHub)
        languages: Optional list of languages to analyze
        wait_for_completion: If True, waits until terminal and returns results
        include_full_graph: If True, downloads and returns the artifact JSON
        pricing_choice: Optional pricing mode if backend supports it
        questions: Optional list of question prompts (backend-dependent)
        github_token: Optional GitHub token for private repos (backend-dependent)
    """
    _require_jwt()
    repo_url = _normalize_repo_url(repo_url)

    logger.info(f"Starting analysis for {repo_url}")

    job_id, submit_payload = await _submit_analysis(
        repo_url=repo_url,
        languages=languages,
        options={"include_metrics": True, "detect_circular": True},
        pricing_choice=pricing_choice,
        questions=questions,
        github_token=github_token,
    )

    if not wait_for_completion:
        return {
            "success": True,
            "status": "submitted",
            "job_id": job_id,
            "repo_url": repo_url,
            "submit": submit_payload,
            "message": "Analysis started. Use get_analysis_status() to check progress.",
        }

    final_status = await _wait_for_analysis(job_id)
    api = _adapter()
    state = api.get_job_status_value(final_status) or "unknown"

    # Terminal state handling
    if state not in TERMINAL_SUCCESS:
        return {
            "success": False,
            "status": state,
            "job_id": job_id,
            "repo_url": repo_url,
            "error": _extract_error_message(final_status if isinstance(final_status, dict) else {}),
            "details": final_status,
            "message": f"Analysis did not complete successfully (state={state}).",
        }

    s3_url = api.extract_s3_url(final_status)
    if not s3_url:
        raise PvizAPIError("Analysis completed but no artifact URL returned")

    response: Dict[str, Any] = {
        "success": True,
        "status": "completed",
        "job_id": job_id,
        "repo_url": repo_url,
        "s3_url": s3_url,
        "generated_at": final_status.get("completed_at", datetime.utcnow().isoformat()) if isinstance(final_status, dict) else datetime.utcnow().isoformat(),
        "summary": (final_status.get("summary", {}) if isinstance(final_status, dict) else {}) or {},
        "details": final_status,
    }

    if include_full_graph:
        logger.info(f"Downloading full graph from {s3_url}")
        response["dependency_graph"] = await _download_json_streaming(s3_url)

    return response


@mcp.tool()
async def get_analysis_status(job_id: str) -> Dict[str, Any]:
    """
    Check the status of a running analysis job.
    """
    _require_jwt()
    api = _adapter()
    client = _get_client()
    try:
        return await api.get_job_status(client, job_id)
    except httpx.HTTPStatusError as e:
        _raise_for_httpx_poll(e, job_id)
        raise PvizAPIError(f"Failed to fetch status for job {job_id}") from e
    except httpx.HTTPError as e:
        raise PvizAPIError(f"Failed to fetch status for job {job_id}: {e}") from e


@mcp.tool()
async def download_dependency_graph(s3_url: str) -> Dict[str, Any]:
    """
    Download the complete dependency graph from S3 (streaming + size cap).
    """
    if not s3_url or not isinstance(s3_url, str):
        raise PvizAPIError("s3_url must be a non-empty string")
    logger.info(f"Downloading dependency graph from {s3_url}")
    return await _download_json_streaming(s3_url)


@mcp.tool()
async def get_repository_metrics(repo_url: str, languages: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get high-level metrics for a repository without downloading the full graph.
    """
    repo_url = _normalize_repo_url(repo_url)

    result = await analyze_repository(
        repo_url=repo_url,
        languages=languages,
        wait_for_completion=True,
        include_full_graph=False,
    )

    if not result.get("success"):
        # Pass through the terminal failure payload
        return result

    summary: Dict[str, Any] = (result.get("summary") or {}) if isinstance(result, dict) else {}
    # Normalize numeric fields safely
    total_modules = _coerce_int(
        summary.get("total_modules")
        or summary.get("total_nodes")
        or summary.get("module_count")
        or summary.get("nodes_count")
        or summary.get("node_count")
        or 0,
        0,
    )
    total_edges = _coerce_int(
        summary.get("total_edges")
        or summary.get("edge_count")
        or summary.get("edges_count")
        or 0,
        0,
    )
    circular_components = _coerce_int(
        summary.get("circular_components")
        or summary.get("circular_count")
        or summary.get("cycles")
        or 0,
        0,
    )

    languages_breakdown = (
        summary.get("languages")
        or summary.get("bundled_by_lang")
        or summary.get("language_breakdown")
        or {}
    )

    return {
        "success": True,
        "status": "completed",
        "repo_url": repo_url,
        "total_modules": total_modules,
        "total_edges": total_edges,
        "circular_components": circular_components,
        "languages": languages_breakdown,
        "mode": summary.get("mode") or "zones",
        "generated_at": result.get("generated_at"),
        "s3_url": result.get("s3_url"),
        "summary": summary,  # keep raw for forward-compat
    }


@mcp.tool()
async def compare_repositories(repo_url_1: str, repo_url_2: str) -> Dict[str, Any]:
    """
    Compare the architectures of two repositories (summary-only, parallel).
    """
    repo_url_1 = _normalize_repo_url(repo_url_1)
    repo_url_2 = _normalize_repo_url(repo_url_2)

    results = await asyncio.gather(
        get_repository_metrics(repo_url_1),
        get_repository_metrics(repo_url_2),
        return_exceptions=True,
    )

    if isinstance(results[0], Exception) or isinstance(results[1], Exception):
        raise PvizAPIError("Failed to analyze one or both repositories")

    m1, m2 = results  # type: ignore[assignment]

    if not m1.get("success") or not m2.get("success"):
        return {
            "success": False,
            "status": "failed",
            "repo_1": m1,
            "repo_2": m2,
            "message": "One or both analyses did not complete successfully.",
        }

    mod_diff = _coerce_int(m1.get("total_modules")) - _coerce_int(m2.get("total_modules"))
    edge_diff = _coerce_int(m1.get("total_edges")) - _coerce_int(m2.get("total_edges"))
    cyc_diff = _coerce_int(m1.get("circular_components")) - _coerce_int(m2.get("circular_components"))

    return {
        "success": True,
        "status": "completed",
        "comparison": {
            "repo_1": {"url": repo_url_1, "metrics": m1},
            "repo_2": {"url": repo_url_2, "metrics": m2},
            "differences": {
                "module_count_diff": mod_diff,
                "edge_count_diff": edge_diff,
                "circular_deps_diff": cyc_diff,
            },
        },
    }


@mcp.tool()
async def get_circular_dependencies(repo_url: str, languages: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Convenience tool: runs analysis and extracts circular-dependency signals.

    Note: This is best-effort. Prefer backend-provided cycle fields in summary when available.
    """
    repo_url = _normalize_repo_url(repo_url)

    # Prefer summary-only if cycles are in summary, otherwise fetch full graph.
    metrics = await get_repository_metrics(repo_url, languages=languages)
    if not metrics.get("success"):
        return metrics

    # If summary already includes cycles/circular_components, return it directly.
    circular_components = _coerce_int(metrics.get("circular_components"), 0)
    if circular_components > 0:
        return {
            "success": True,
            "status": "completed",
            "repo_url": repo_url,
            "circular_components": circular_components,
            "message": "Circular dependency signal present in summary (circular_components > 0).",
            "summary": metrics.get("summary", {}),
            "s3_url": metrics.get("s3_url"),
        }

    # Fallback: download full graph and inspect SCC annotations if present.
    full = await analyze_repository(
        repo_url=repo_url,
        languages=languages,
        wait_for_completion=True,
        include_full_graph=True,
    )
    if not full.get("success"):
        return full

    graph = full.get("dependency_graph") or {}
    nodes = graph.get("nodes") or {}

    circular_nodes: List[Dict[str, Any]] = []

    # nodes might be a dict keyed by id or a list of node objects
    if isinstance(nodes, dict):
        items = nodes.items()
        for node_id, node_data in items:
            if not isinstance(node_data, dict):
                continue
            scc_size = _coerce_int(node_data.get("scc_size"), 1)
            if scc_size > 1:
                circular_nodes.append(
                    {
                        "node": node_id,
                        "scc_id": node_data.get("scc_id"),
                        "scc_size": scc_size,
                        "file": node_data.get("file"),
                        "lang": node_data.get("lang"),
                    }
                )
    elif isinstance(nodes, list):
        for node_data in nodes:
            if not isinstance(node_data, dict):
                continue
            scc_size = _coerce_int(node_data.get("scc_size"), 1)
            if scc_size > 1:
                circular_nodes.append(
                    {
                        "node": node_data.get("node_id") or node_data.get("id") or node_data.get("file"),
                        "scc_id": node_data.get("scc_id"),
                        "scc_size": scc_size,
                        "file": node_data.get("file"),
                        "lang": node_data.get("lang"),
                    }
                )

    return {
        "success": True,
        "status": "completed",
        "repo_url": repo_url,
        "circular_dependencies_found": len(circular_nodes),
        "circular_deps": circular_nodes,
        "s3_url": full.get("s3_url"),
        "message": "Best-effort SCC-based circular dependency detection. Prefer backend cycle artifacts when available.",
    }


# =============================================================================
# MCP RESOURCES - Static documentation/schemas
# =============================================================================

@mcp.resource("pviz://schema")
def get_schema() -> str:
    """
    Return pviz dependency graph schema (documentation-only).
    Keep this short to reduce drift; rely on schema_version in artifacts.
    """
    return """
pviz Dependency Graph Schema (documentation)

Artifacts are versioned by `schema_version` in the root payload (e.g. `pviz-llm-bundle@v1.x`).
For precise field definitions, prefer the `schema_version` in the returned artifact, and the
backend-provided schema references.

Common root fields:
  - schema_version: string
  - meta: object (generated_at, mode, languages, bundled_by_lang, ...)
  - nodes: dict or list (implementation-dependent)
  - edges: list
  - zones: object (if mode="zones")
"""


@mcp.resource("pviz://examples")
def get_examples() -> str:
    """Return example queries and use cases."""
    return """
pviz MCP Server - Example Queries

1. Basic Analysis:
   "Analyze https://github.com/django/django and give me an overview of its architecture"

2. Circular Dependencies:
   "Check if https://github.com/facebook/react has any circular dependencies"

3. Comparison:
   "Compare the architectures of Vue.js and React"

4. Metrics Only:
   "What are the high-level metrics for https://github.com/tensorflow/tensorflow?"

5. Specific Languages:
   "Analyze the Python code in https://github.com/pallets/flask"

6. Async Workflow:
   "Start analyzing repo X (don't wait), then check status in 2 minutes"
"""


# =============================================================================
# MCP PROMPTS - Templates for common tasks
# =============================================================================

@mcp.prompt()
def analyze_codebase_prompt(repo_url: str) -> str:
    """Template prompt for comprehensive codebase analysis."""
    return f"""
Please perform a comprehensive analysis of the repository at {repo_url}.

Use the pviz tools to:
1. Get high-level metrics (module count, language breakdown, circular dependencies)
2. Identify any architectural issues (circular deps, high coupling)
3. Provide recommendations for improvement

Structure your response with:
- Executive Summary
- Key Metrics
- Issues Found
- Recommendations
""".strip()


@mcp.prompt()
def compare_projects_prompt(repo_url_1: str, repo_url_2: str) -> str:
    """Template prompt for comparing two codebases."""
    return f"""
Compare the architectures of these two repositories:
1. {repo_url_1}
2. {repo_url_2}

Focus on:
- Size and complexity differences
- Dependency management approaches
- Code organization quality
- Which one has better architectural practices
""".strip()


# =============================================================================
# Optional account/billing/history tools (pass-through; consistent envelopes)
# =============================================================================

@mcp.tool()
async def check_account_balance() -> Dict[str, Any]:
    """
    Check pviz account balance and information.
    """
    api = _adapter()
    client = _get_client()

    try:
        account = await api.get_account_info(client)
        balance_data = await api.get_token_balance(client)

        return {
            "success": True,
            "status": "completed",
            "email": account.get("email"),
            "plan": account.get("plan"),
            "token_balance": balance_data.get("balance", 0),
            "is_verified": account.get("is_verified", False),
            "trial": balance_data.get("trial"),
        }
    except httpx.HTTPStatusError as e:
        _raise_for_httpx_submit(e)
    except httpx.HTTPError as e:
        raise PvizAPIError(f"Failed to retrieve account information: {e}") from e


@mcp.tool()
async def estimate_analysis_cost(repo_url: str, github_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Estimate token cost before analyzing a repository.
    """
    api = _adapter()
    client = _get_client()
    repo_url = _normalize_repo_url(repo_url)

    try:
        estimate = await api.estimate_cost(client, repo_url, github_token)
        balance_data = await api.get_token_balance(client)
        current_balance = _coerce_int(balance_data.get("balance"), 0)

        tokens_needed = _coerce_int(estimate.get("tokens_needed"), 0)
        can_afford = bool(estimate.get("can_afford", tokens_needed <= current_balance))

        return {
            "success": True,
            "status": "completed",
            "repo_url": repo_url,
            "tokens_needed": tokens_needed,
            "current_balance": current_balance,
            "can_afford": can_afford,
            "estimate": estimate,
        }
    except httpx.HTTPStatusError as e:
        _raise_for_httpx_submit(e)
    except httpx.HTTPError as e:
        raise PvizAPIError(f"Failed to estimate cost: {e}") from e


@mcp.tool()
async def get_job_history(limit: int = 10) -> Dict[str, Any]:
    """
    Get recent analysis job history.
    """
    api = _adapter()
    client = _get_client()
    limit = min(max(1, int(limit)), 50)

    try:
        history = await api.get_job_history(client, limit)
        return {
            "success": True,
            "status": "completed",
            "total": history.get("total", 0),
            "jobs": history.get("jobs", []),
        }
    except httpx.HTTPStatusError as e:
        _raise_for_httpx_submit(e)
    except httpx.HTTPError as e:
        raise PvizAPIError(f"Failed to retrieve job history: {e}") from e


@mcp.tool()
async def retrieve_past_result(job_id: str, include_full_graph: bool = True) -> Dict[str, Any]:
    """
    Retrieve a previously completed analysis result by job_id.
    """
    api = _adapter()
    client = _get_client()

    try:
        status = await api.get_job_status(client, job_id)
        job_status = api.get_job_status_value(status) or "unknown"

        if job_status != "completed":
            return {
                "success": False,
                "status": job_status,
                "job_id": job_id,
                "message": f"Job {job_id} is not completed yet (status={job_status}).",
                "details": status,
            }

        download_data = await api.get_download_link(client, job_id)
        s3_url = download_data.get("url") or download_data.get("s3_url")

        if not s3_url:
            return {
                "success": False,
                "status": "no_download_url",
                "job_id": job_id,
                "message": "Download URL not available for this job.",
                "details": download_data,
            }

        resp: Dict[str, Any] = {
            "success": True,
            "status": "completed",
            "job_id": job_id,
            "repo_url": status.get("repo_url"),
            "completed_at": status.get("completed_at"),
            "tokens_charged": status.get("tokens_charged"),
            "s3_url": s3_url,
        }

        if include_full_graph:
            resp["dependency_graph"] = await _download_json_streaming(s3_url)

        return resp

    except httpx.HTTPStatusError as e:
        status_code = getattr(e.response, "status_code", None)
        if status_code == 404:
            return {
                "success": False,
                "status": "job_not_found",
                "job_id": job_id,
                "message": f"Job {job_id} not found. Use get_job_history() to see available jobs.",
            }
        _raise_for_httpx_submit(e)
    except httpx.HTTPError as e:
        raise PvizAPIError(f"Failed to retrieve past result: {e}") from e


@mcp.tool()
async def analyze_repository_with_confirmation(
    repo_url: str,
    pricing_choice: str = "tokens",
    questions: Optional[List[str]] = None,
    github_token: Optional[str] = None,
    skip_confirmation: bool = False,
    wait_for_completion: bool = True,
    include_full_graph: bool = False,
    languages: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Analyze a repository with a pre-flight cost check and a confirmation gate.

    This tool returns a confirmation payload on the first call (unless skip_confirmation=True).
    """
    api = _adapter()
    client = _get_client()
    repo_url = _normalize_repo_url(repo_url)

    # STEP 1: cost check and confirmation
    if not skip_confirmation:
        try:
            account = await api.get_account_info(client)
            balance_data = await api.get_token_balance(client)
            current_balance = _coerce_int(balance_data.get("balance"), 0)

            estimate = await api.estimate_cost(client, repo_url, github_token)
            tokens_needed = _coerce_int(estimate.get("tokens_needed"), 0)
            can_afford = bool(estimate.get("can_afford", tokens_needed <= current_balance))

            if not can_afford:
                return {
                    "success": False,
                    "status": "insufficient_tokens",
                    "repo_url": repo_url,
                    "tokens_needed": tokens_needed,
                    "current_balance": current_balance,
                    "shortfall": max(0, tokens_needed - current_balance),
                    "message": "Insufficient tokens to run analysis.",
                    "actions": {
                        "buy_tokens_url": "https://pvizgenerator.com/tokens",
                    },
                    "estimate": estimate,
                }

            return {
                "success": True,
                "status": "requires_confirmation",
                "repo_url": repo_url,
                "pricing_choice": pricing_choice,
                "tokens_needed": tokens_needed,
                "current_balance": current_balance,
                "charged_to": account.get("email"),
                "message": "Call again with skip_confirmation=True to proceed.",
                "estimate": estimate,
            }

        except Exception as e:
            logger.warning(f"Cost check failed: {e}; proceeding without confirmation gate.")
            # proceed

    # STEP 2: submit + wait via canonical analyze_repository
    return await analyze_repository(
        repo_url=repo_url,
        languages=languages,
        wait_for_completion=wait_for_completion,
        include_full_graph=include_full_graph,
        pricing_choice=pricing_choice,
        questions=questions,
        github_token=github_token,
    )


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if not JWT_TOKEN:
        logger.warning("PVIZ_JWT_TOKEN not set - server will fail on API calls")

    logger.info("Starting pviz MCP server")
    logger.info(f"API endpoint: {API_BASE_URL}")
    logger.info(f"Auth configured: {bool(JWT_TOKEN)}")

    try:
        mcp.run()
    finally:
        # Best-effort cleanup of the shared client
        try:
            asyncio.run(_close_client())
        except Exception:
            pass
