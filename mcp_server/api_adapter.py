# mcp-server/api_adapter.py
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import sys
import httpx

from .auth_context import PVIZ_REQUEST_BEARER  # type: ignore

_HAS_REQUEST_BEARER = True

# ==============================================================================
# Token loading (single source of truth) + non-sensitive fingerprinting
# ==============================================================================


def _read_text_file(path: str) -> str:
    """
    NOTE: currently unused (kept for potential future PVIZ_JWT_TOKEN_FILE support).
    """
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_jwt_token_with_source() -> Tuple[str, str]:
    """
    Load JWT token from:
      1) PVIZ_JWT_TOKEN (direct env)

    Returns: (token, source) where source is 'env'
    Raises: ValueError if not configured.
    """
    tok = os.getenv("PVIZ_JWT_TOKEN")
    if tok and tok.strip():
        return tok.strip(), "env"

    raise ValueError("JWT not configured: set PVIZ_JWT_TOKEN")


def token_fingerprint(tok: str) -> str:
    """
    Non-sensitive fingerprint for debugging that does not leak the full token.
    """
    t = (tok or "").strip().encode("utf-8")
    if not t:
        return "none"
    return hashlib.sha256(t).hexdigest()[:12]


def _default_no_cache_headers() -> Dict[str, str]:
    # Useful for identity/billing endpoints where stale intermediaries are possible.
    return {
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }


class PvizAPIAdapter:
    """
    Adapter for pviz FastAPI backend.

    Option A behavior:
      - Prefer *per-request* bearer token (from PVIZ_REQUEST_BEARER) if available.
      - Fall back to explicit jwt_token passed to __init__.
      - Fall back to env token (PVIZ_JWT_TOKEN).

    Notes:
      - Artifact URLs should be obtained from:
          (A) GET /jobs/{id} -> artifact_formats (preferred)
          (B) GET /jobs/{id}/artifact-links?prefer=... (fallback)
    """

    def __init__(
        self,
        base_url: str,
        jwt_token: Optional[str] = None,
        *,
        allow_env_fallback: bool = True,
        enable_no_cache_headers: bool = True,
        prefer_request_bearer: bool = True,
        require_request_bearer: bool = False,
    ):
        # NOTE: allow_env_fallback is currently not enforced to preserve existing behavior
        # (env is always attempted when no explicit token is provided).
        self.base_url = (base_url or "").rstrip("/")
        self.enable_no_cache_headers = bool(enable_no_cache_headers)

        # Used only as fallback (local mode or remote mode with context loss)
        tok = (jwt_token or "").strip()
        src = "explicit" if tok else "none"

        # Env fallback (ALWAYS try, even in remote mode, for async context loss scenarios)
        if not tok:
            try:
                tok, src = load_jwt_token_with_source()
            except Exception:
                tok, src = "", "none"

        self.jwt_token = tok
        self.jwt_source = src  # 'explicit' | 'env' | 'none'

        self.prefer_request_bearer = bool(prefer_request_bearer)
        self.require_request_bearer = bool(require_request_bearer)

    # ------------------------------------------------------------------
    # Token selection
    # ------------------------------------------------------------------
    def _get_request_bearer(self) -> Optional[str]:
        if not _HAS_REQUEST_BEARER:
            return None
        try:
            # DEBUG: Check what we're getting
            v = PVIZ_REQUEST_BEARER.get()
            print(f"[DEBUG] _get_request_bearer: v={repr(v)}", file=sys.stderr, flush=True)
            
            if v and isinstance(v, str) and v.strip():
                print(f"[DEBUG] Using request bearer: {token_fingerprint(v)[:8]}", file=sys.stderr, flush=True)
                return v.strip()

            # Fallback: try session store (for async task context loss)
            from .auth_context import PVIZ_SESSION_ID, SESSION_BEARERS

            session_id = PVIZ_SESSION_ID.get()
            print(f"[DEBUG] session_id from contextvar: {repr(session_id)}", file=sys.stderr, flush=True)
            
            if session_id:
                tok = SESSION_BEARERS.get(session_id)
                print(f"[DEBUG] token from session store: {token_fingerprint(tok)[:8] if tok else 'none'}", file=sys.stderr, flush=True)
                if tok and isinstance(tok, str) and tok.strip():
                    return tok.strip()
        except Exception as e:
            print(f"[DEBUG] _get_request_bearer exception: {e}", file=sys.stderr, flush=True)
            pass
        return None

    def _choose_token(self) -> Tuple[str, str]:
        """
        Returns (token, source) where source is:
          - "request" if per-request bearer is available
          - otherwise self.jwt_source

        NOTE: require_request_bearer is not enforced here to preserve existing behavior.
        """
        req_tok = self._get_request_bearer() if self.prefer_request_bearer else None
        if req_tok:
            return req_tok, "request"

        if self.jwt_token and self.jwt_token.strip():
            return self.jwt_token.strip(), self.jwt_source

        raise ValueError(
            "JWT not configured: no per-request bearer available and no fallback token set. "
            "Set PVIZ_JWT_TOKEN environment variable or ensure the MCP client sends Authorization: Bearer <token>."
        )

    # ------------------------------------------------------------------
    # Headers / params helpers
    # ------------------------------------------------------------------
    def _headers(self, *, force_no_cache: bool = False) -> Dict[str, str]:
        tok, _src = self._choose_token()
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {tok}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if force_no_cache or self.enable_no_cache_headers:
            headers.update(_default_no_cache_headers())

        return headers

    def _maybe_bust_cache(self, params: Optional[Dict[str, Any]], *, force_fresh: bool) -> Dict[str, Any]:
        out = dict(params or {})
        if force_fresh:
            out["cb"] = int(time.time() * 1000)
        return out

    def _wrap_dict(self, data: Any) -> Dict[str, Any]:
        return data if isinstance(data, dict) else {"value": data}

    def _normalize_collection(
        self,
        data: Any,
        *,
        out_key: str,
        dict_keys: Tuple[str, ...],
        total_key: str = "total",
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Normalize backend responses that can be either:
          - list
          - dict with items under one of dict_keys
        into: {"total": <int>, out_key: <list>}
        """
        if isinstance(data, list):
            items = data
            if limit is not None:
                items = items[:limit]
            return {"total": len(data), out_key: items}

        if isinstance(data, dict):
            for k in dict_keys:
                if k in data:
                    items = data.get(k) or []
                    if isinstance(items, list) and limit is not None:
                        items = items[:limit]
                    total = data.get(total_key)
                    if total is None:
                        total = len(items) if isinstance(items, list) else 0
                    return {"total": int(total), out_key: items}

        return {"total": 0, out_key: []}

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
        force_no_cache: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        resp = await client.get(
            url,
            headers=self._headers(force_no_cache=force_no_cache),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        return resp.json()

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout_s: float = 30.0,
        force_fresh: bool = False,
        force_no_cache: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        resp = await client.post(
            url,
            headers=self._headers(force_no_cache=force_no_cache),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            json=json,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        return resp.json()

    def debug_token_info(self) -> Dict[str, Any]:
        """
        Non-sensitive debug info to confirm which JWT and API base URL the MCP server is using.

        IMPORTANT:
          - If a request-scoped token exists, it will be reported as token_source="request"
            with its own fingerprint.
          - Otherwise it reports the configured fallback token.
        """
        info: Dict[str, Any] = {
            "base_url": self.base_url,
            "no_cache_headers_enabled": self.enable_no_cache_headers,
            "prefer_request_bearer": self.prefer_request_bearer,
            "require_request_bearer": self.require_request_bearer,
            "request_bearer_supported": _HAS_REQUEST_BEARER,
        }

        try:
            tok, src = self._choose_token()
            info["token_source"] = src
            info["token_fingerprint"] = token_fingerprint(tok)
        except Exception as e:
            info["token_source"] = "none"
            info["token_fingerprint"] = "none"
            info["token_error"] = f"{type(e).__name__}: {e}"

        # Also include fallback fingerprint (useful to detect accidental env use)
        if self.jwt_token:
            info["fallback_token_source"] = self.jwt_source
            info["fallback_token_fingerprint"] = token_fingerprint(self.jwt_token)

        return info

    # ========================================================================
    # ACCOUNT
    # ========================================================================

    async def get_account_info(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /auth/me
        """
        data = await self._get_json(
            client,
            "/auth/me",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    # ========================================================================
    # TOKENS (balance + overview + ledger)
    # ========================================================================

    async def get_token_balance(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /tokens/balance
        """
        data = await self._get_json(
            client,
            "/tokens/balance",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        data = data or {}
        if not isinstance(data, dict):
            return {"value": data}
        return data

    async def get_token_overview(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /tokens/overview
        """
        data = await self._get_json(
            client,
            "/tokens/overview",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        data = data or {}
        if not isinstance(data, dict):
            data = {"value": data}

        return {
            "balance": data.get("current_balance", data.get("balance", 0)),
            "plan": data.get("plan", "free"),
            "trial": data.get("trial"),
            "products": data.get("products", []),
            "full_overview": data,
        }

    async def get_token_transactions(
        self,
        client: httpx.AsyncClient,
        *,
        skip: int = 0,
        limit: int = 50,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /tokens/transactions?skip=&limit=
        """
        skip_i = max(0, int(skip or 0))
        limit_i = max(1, min(int(limit or 50), 200))
        params: Dict[str, Any] = {"skip": skip_i, "limit": limit_i}

        data = await self._get_json(
            client,
            "/tokens/transactions",
            params=params,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )

        # Preserve existing tolerance for multiple response shapes
        if isinstance(data, list):
            return {"total": len(data), "transactions": data}

        if isinstance(data, dict):
            if "items" in data:
                items = data.get("items") or []
                return {"total": int(data.get("total") or len(items)), "transactions": items}
            if "transactions" in data:
                tx = data.get("transactions") or []
                return {"total": int(data.get("total") or len(tx)), "transactions": tx}

        return {"total": 0, "transactions": []}

    async def check_sufficient_balance(
        self,
        client: httpx.AsyncClient,
        required_tokens: int,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        Uses /tokens/overview and compares locally.
        """
        overview = await self.get_token_overview(client, timeout_s=timeout_s, force_fresh=force_fresh)
        current_balance = int(overview.get("balance") or 0)
        req = int(required_tokens)

        can_afford = current_balance >= req
        result: Dict[str, Any] = {
            "can_afford": can_afford,
            "current_balance": current_balance,
            "required": req,
        }
        if not can_afford:
            result["shortfall"] = req - current_balance
        return result

    # ========================================================================
    # TRIAL (entitlement + ledger)
    # ========================================================================

    async def get_trial_entitlement(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /trial/entitlement
        """
        data = await self._get_json(
            client,
            "/trial/entitlement",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    async def get_trial_ledger(
        self,
        client: httpx.AsyncClient,
        *,
        skip: int = 0,
        limit: int = 50,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /trial/ledger?skip=&limit=
        """
        skip_i = max(0, int(skip or 0))
        limit_i = max(1, min(int(limit or 50), 200))
        params: Dict[str, Any] = {"skip": skip_i, "limit": limit_i}

        data = await self._get_json(
            client,
            "/trial/ledger",
            params=params,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )

        if isinstance(data, list):
            return {"total": len(data), "entries": data}
        if isinstance(data, dict):
            if "items" in data:
                items = data.get("items") or []
                return {"total": int(data.get("total") or len(items)), "entries": items}
            if "entries" in data:
                items = data.get("entries") or []
                return {"total": int(data.get("total") or len(items)), "entries": items}
        return {"total": 0, "entries": []}

    # ========================================================================
    # STORE (products + orders)
    # ========================================================================

    async def list_products(
        self,
        client: httpx.AsyncClient,
        *,
        active_only: bool = True,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /store/products?active_only=1
        """
        params: Dict[str, Any] = {"active_only": 1 if active_only else 0}
        data = await self._get_json(
            client,
            "/store/products",
            params=params,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=False,
        )

        if isinstance(data, list):
            return {"total": len(data), "products": data}
        if isinstance(data, dict):
            if "items" in data:
                items = data.get("items") or []
                return {"total": int(data.get("total") or len(items)), "products": items}
            if "products" in data:
                items = data.get("products") or []
                return {"total": int(data.get("total") or len(items)), "products": items}
        return {"total": 0, "products": []}

    async def list_orders(
        self,
        client: httpx.AsyncClient,
        *,
        limit: int = 20,
        skip: int = 0,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /store/orders?limit=&skip=
        """
        limit_i = max(1, min(int(limit or 20), 100))
        skip_i = max(0, int(skip or 0))
        params: Dict[str, Any] = {"limit": limit_i, "skip": skip_i}

        data = await self._get_json(
            client,
            "/store/orders",
            params=params,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )

        if isinstance(data, list):
            return {"total": len(data), "orders": data}
        if isinstance(data, dict):
            if "items" in data:
                items = data.get("items") or []
                return {"total": int(data.get("total") or len(items)), "orders": items}
            if "orders" in data:
                items = data.get("orders") or []
                return {"total": int(data.get("total") or len(items)), "orders": items}
        return {"total": 0, "orders": []}

    async def get_order(
        self,
        client: httpx.AsyncClient,
        order_id: str,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /store/orders/{order_id}
        """
        data = await self._get_json(
            client,
            f"/store/orders/{order_id}",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    # ========================================================================
    # COST ESTIMATION + JOBS
    # ========================================================================

    async def estimate_cost(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        github_token: Optional[str] = None,
        *,
        timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
        """
        POST /estimate/github
        """
        repo_spec = self._parse_repo_url(repo_url)
        payload: Dict[str, Any] = {"repo_spec": repo_spec}
        if github_token:
            payload["github_token"] = github_token

        data = await self._post_json(
            client,
            "/estimate/github",
            json=payload,
            timeout_s=timeout_s,
            force_no_cache=False,
        )
        return self._wrap_dict(data)

    async def submit_analysis(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        languages: Optional[List[str]] = None,
        pricing_choice: str = "tokens",
        questions: Optional[List[str]] = None,
        github_token: Optional[str] = None,
        expected_tokens: Optional[int] = None,
        *,
        timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
        """
        POST /jobs/github
        """
        repo_spec = self._parse_repo_url(repo_url)

        if expected_tokens is None:
            estimate = await self.estimate_cost(
                client,
                repo_url,
                github_token=github_token,
                timeout_s=timeout_s,
            )
            expected_tokens = int(estimate.get("tokens_needed") or 0)

        payload: Dict[str, Any] = {
            "repo_spec": repo_spec,
            "expected_tokens": expected_tokens,
            "pricing_choice": pricing_choice,
        }
        if questions:
            payload["questions"] = questions
        if github_token:
            payload["github_token"] = github_token
        if languages:
            payload["languages"] = languages
        if pricing_choice == "trial_credit":
            payload["use_trial_credit_requested"] = True

        data = await self._post_json(
            client,
            "/jobs/github",
            json=payload,
            timeout_s=timeout_s,
            force_no_cache=False,
        )
        return self._wrap_dict(data)

    async def get_job_status(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /jobs/{job_id}
        """
        data = await self._get_json(
            client,
            f"/jobs/{job_id}",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    async def get_job_history(
        self,
        client: httpx.AsyncClient,
        limit: int = 10,
        skip: int = 0,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /jobs?limit=&skip=
        """
        limit_i = max(1, min(int(limit), 50))
        skip_i = max(0, int(skip))

        data = await self._get_json(
            client,
            "/jobs",
            params={"skip": skip_i, "limit": limit_i},
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )

        if isinstance(data, list):
            return {"total": len(data), "jobs": data[:limit_i]}
        if isinstance(data, dict) and "items" in data:
            jobs = data.get("items") or []
            total = int(data.get("total") or len(jobs))
            return {"total": total, "jobs": jobs[:limit_i]}
        if isinstance(data, dict) and "jobs" in data:
            jobs = data.get("jobs") or []
            total = int(data.get("total") or len(jobs))
            return {"total": total, "jobs": jobs[:limit_i]}
        return {"total": 0, "jobs": []}

    async def cancel_job(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
        """
        POST /jobs/{job_id}/cancel
        """
        data = await self._post_json(
            client,
            f"/jobs/{job_id}/cancel",
            json=None,
            timeout_s=timeout_s,
            force_no_cache=False,
        )
        return self._wrap_dict(data)

    # ------------------------------------------------------------------------
    # Artifacts (dual-format only; no legacy shim)
    # ------------------------------------------------------------------------

    async def get_artifact_links(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        prefer: str = "standard",  # "standard" | "compressed" | "both"
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /jobs/{job_id}/artifact-links?prefer=standard|compressed|both
        """
        data = await self._get_json(
            client,
            f"/jobs/{job_id}/artifact-links",
            params={"prefer": prefer},
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    async def get_job_artifacts(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
        prefer: str = "both",  # "standard" | "compressed" | "both"
    ) -> Dict[str, Any]:
        """
        Return artifact URLs for both formats (standard + compressed) when possible.

        Resolution order:
        1) GET /jobs/{id} -> job.artifact_formats (preferred; includes size + ratio)
        2) GET /jobs/{id}/artifact-links?prefer=... (explicit dual endpoint)

        If neither is available, raises ValueError with guidance.
        """
        # 1) Preferred: job detail (where frontend initiated)
        job: Optional[Dict[str, Any]] = None
        try:
            j = await self.get_job_status(client, job_id, timeout_s=timeout_s, force_fresh=force_fresh)
            job = j if isinstance(j, dict) else None
        except Exception:
            job = None

        if job:
            af = job.get("artifact_formats")
            if isinstance(af, dict) and (af.get("standard") or af.get("compressed")):
                if prefer == "standard":
                    result_af = {"standard": af.get("standard"), "compressed": None}
                elif prefer == "compressed":
                    result_af = {"standard": None, "compressed": af.get("compressed")}
                else:
                    result_af = {"standard": af.get("standard"), "compressed": af.get("compressed")}

                has_requested = (
                    (prefer == "standard" and result_af.get("standard"))
                    or (prefer == "compressed" and result_af.get("compressed"))
                    or (prefer == "both" and (result_af.get("standard") or result_af.get("compressed")))
                )

                if has_requested:
                    return {
                        "job_id": job_id,
                        "status": job.get("status"),
                        "artifact_formats": result_af,
                        "source": "job_detail",
                    }

        # 2) Fallback: explicit artifact-links endpoint
        try:
            links = await self.get_artifact_links(
                client, job_id, prefer=prefer, timeout_s=timeout_s, force_fresh=force_fresh
            )
            af2 = links.get("artifact_formats")
            normalized: Any = af2 if isinstance(af2, dict) else links

            if isinstance(normalized, dict) and (normalized.get("standard") or normalized.get("compressed")):
                return {
                    "job_id": job_id,
                    "status": None if not job else job.get("status"),
                    "artifact_formats": normalized,
                    "source": "artifact_links",
                }
        except Exception:
            pass

        raise ValueError(
            "No artifact URLs available via /jobs/{id} (artifact_formats) or /jobs/{id}/artifact-links. "
            "Ensure the backend exposes artifact_formats on job detail and/or implements /artifact-links."
        )

    async def get_llm_report(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /jobs/{job_id}/llm-report
        """
        data = await self._get_json(
            client,
            f"/jobs/{job_id}/llm-report",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    async def get_llm_result(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        timeout_s: float = 30.0,
        force_fresh: bool = True,
    ) -> Dict[str, Any]:
        """
        GET /jobs/{job_id}/llm-result
        """
        data = await self._get_json(
            client,
            f"/jobs/{job_id}/llm-result",
            params=None,
            timeout_s=timeout_s,
            force_fresh=force_fresh,
            force_no_cache=True,
        )
        return self._wrap_dict(data)

    # ========================================================================
    # Helper methods
    # ========================================================================

    def extract_job_id(self, submit_response: Dict[str, Any]) -> str:
        if "job_id" in submit_response and isinstance(submit_response["job_id"], str):
            return submit_response["job_id"]
        if "id" in submit_response and isinstance(submit_response["id"], str):
            return submit_response["id"]
        raise ValueError(f"Could not find job_id in response: {submit_response}")

    def get_job_status_value(self, status_response: Dict[str, Any]) -> str:
        v = status_response.get("status", "unknown")
        return v.lower() if isinstance(v, str) else "unknown"

    def _parse_repo_url(self, repo_url: str) -> Dict[str, Any]:
        raw = (repo_url or "").strip()

        # Handle "owner/repo"
        if "/" in raw and not raw.startswith(("http://", "https://")):
            clean_repo = raw.replace(".git", "")
            return {"provider": "github", "repo": clean_repo}

        # Parse URL
        try:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
            if not host or "github.com" not in host:
                return {"provider": "github", "repo": raw}

            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) < 2:
                return {"provider": "github", "repo": raw}

            owner = parts[0]
            repo = parts[1].replace(".git", "")
            repo_spec: Dict[str, Any] = {"provider": "github", "repo": f"{owner}/{repo}"}

            if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                repo_spec["branch"] = parts[3]
                if len(parts) > 4:
                    repo_spec["subpath"] = "/".join(parts[4:])

            return repo_spec
        except Exception:
            return {"provider": "github", "repo": raw}
