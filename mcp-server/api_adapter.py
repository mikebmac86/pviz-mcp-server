# mcp-server/api_adapter.py
from __future__ import annotations

import os
import time
import hashlib
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse

import httpx


# ==============================================================================
# Token loading (single source of truth) + non-sensitive fingerprinting
# ==============================================================================

def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_jwt_token_with_source() -> Tuple[str, str]:
    """
    Load JWT token from:
      1) PVIZ_JWT_TOKEN (direct env)
      2) PVIZ_JWT_TOKEN_FILE (docker secret file path)

    Returns: (token, source) where source is 'env' or 'file'
    Raises: ValueError if not configured.
    """
    tok = os.getenv("PVIZ_JWT_TOKEN")
    if tok and tok.strip():
        return tok.strip(), "env"

    tok_file = os.getenv("PVIZ_JWT_TOKEN_FILE")
    if tok_file and tok_file.strip():
        raw = _read_text_file(tok_file.strip())
        tok2 = (raw or "").strip()
        if tok2:
            return tok2, "file"

    raise ValueError("JWT not configured: set PVIZ_JWT_TOKEN or PVIZ_JWT_TOKEN_FILE")


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
    ):
        self.base_url = (base_url or "").rstrip("/")

        tok = (jwt_token or "").strip()
        src = "explicit"
        if not tok:
            if not allow_env_fallback:
                raise ValueError("jwt_token was not provided and allow_env_fallback=False")
            tok, src = load_jwt_token_with_source()

        self.jwt_token = tok
        self.jwt_source = src  # 'explicit' | 'env' | 'file'
        self.enable_no_cache_headers = bool(enable_no_cache_headers)

    # ------------------------------------------------------------------
    # Headers / params helpers
    # ------------------------------------------------------------------
    def _headers(self, *, force_no_cache: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {self.jwt_token}",
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

    def debug_token_info(self) -> Dict[str, Any]:
        """
        Non-sensitive debug info to confirm which JWT and API base URL the MCP server is using.
        """
        return {
            "base_url": self.base_url,
            "token_source": self.jwt_source,
            "token_fingerprint": token_fingerprint(self.jwt_token),
            "no_cache_headers_enabled": self.enable_no_cache_headers,
        }

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
        endpoint = f"{self.base_url}/auth/me"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/tokens/balance"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json() or {}
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
        endpoint = f"{self.base_url}/tokens/overview"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json() or {}
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
        endpoint = f"{self.base_url}/tokens/transactions"
        skip_i = max(0, int(skip or 0))
        limit_i = max(1, min(int(limit or 50), 200))
        params: Dict[str, Any] = {"skip": skip_i, "limit": limit_i}

        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

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
        endpoint = f"{self.base_url}/trial/entitlement"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/trial/ledger"
        skip_i = max(0, int(skip or 0))
        limit_i = max(1, min(int(limit or 50), 200))
        params: Dict[str, Any] = {"skip": skip_i, "limit": limit_i}

        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

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
        endpoint = f"{self.base_url}/store/products"
        params: Dict[str, Any] = {"active_only": 1 if active_only else 0}

        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=False),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

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
        endpoint = f"{self.base_url}/store/orders"
        limit_i = max(1, min(int(limit or 20), 100))
        skip_i = max(0, int(skip or 0))
        params: Dict[str, Any] = {"limit": limit_i, "skip": skip_i}

        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

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
        endpoint = f"{self.base_url}/store/orders/{order_id}"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/estimate/github"
        repo_spec = self._parse_repo_url(repo_url)
        payload: Dict[str, Any] = {"repo_spec": repo_spec}
        if github_token:
            payload["github_token"] = github_token

        resp = await client.post(
            endpoint,
            headers=self._headers(force_no_cache=False),
            json=payload,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/jobs/github"
        repo_spec = self._parse_repo_url(repo_url)

        if expected_tokens is None:
            estimate = await self.estimate_cost(client, repo_url, github_token, timeout_s=timeout_s)
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

        resp = await client.post(
            endpoint,
            headers=self._headers(force_no_cache=False),
            json=payload,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/jobs/{job_id}"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/jobs"
        limit_i = max(1, min(int(limit), 50))
        skip_i = max(0, int(skip))

        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache({"skip": skip_i, "limit": limit_i}, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

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
        endpoint = f"{self.base_url}/jobs/{job_id}/cancel"
        resp = await client.post(
            endpoint,
            headers=self._headers(force_no_cache=False),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/jobs/{job_id}/artifact-links"
        params = {"prefer": prefer}
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(params, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
            if isinstance(af, dict) and isinstance(af.get("standard"), dict):
                if prefer == "standard":
                    af = {"standard": af.get("standard"), "compressed": None}
                elif prefer == "compressed":
                    af = {"standard": af.get("standard"), "compressed": af.get("compressed")}
                return {
                    "job_id": job_id,
                    "status": job.get("status"),
                    "artifact_formats": af,
                    "source": "job_detail",
                }

        # 2) Fallback: explicit artifact-links endpoint
        try:
            links = await self.get_artifact_links(
                client, job_id, prefer=prefer, timeout_s=timeout_s, force_fresh=force_fresh
            )
            af2 = links.get("artifact_formats")
            if isinstance(af2, dict) and isinstance(af2.get("standard"), dict):
                normalized = af2
            else:
                normalized = links

            # Basic sanity check
            if isinstance(normalized, dict) and isinstance(normalized.get("standard"), dict):
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
        endpoint = f"{self.base_url}/jobs/{job_id}/llm-report"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
        endpoint = f"{self.base_url}/jobs/{job_id}/llm-result"
        resp = await client.get(
            endpoint,
            headers=self._headers(force_no_cache=True),
            params=self._maybe_bust_cache(None, force_fresh=force_fresh),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}

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
