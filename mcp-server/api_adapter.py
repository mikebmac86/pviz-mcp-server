import os
from typing import Dict, Any, Optional, List
import httpx
from urllib.parse import urlparse
import time

def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_jwt_from_env() -> Optional[str]:
    tok = os.getenv("PVIZ_JWT_TOKEN")
    if tok and tok.strip():
        return tok.strip()

    tok_file = os.getenv("PVIZ_JWT_TOKEN_FILE")
    if tok_file and tok_file.strip():
        try:
            raw = _read_text_file(tok_file.strip())
            tok2 = (raw or "").strip()
            if tok2:
                return tok2
        except Exception:
            return None
    return None


class PvizAPIAdapter:
    """
    Adapter for pviz FastAPI backend.
    Matches the actual production API structure.
    """

    def __init__(self, base_url: str, jwt_token: str):
        self.base_url = base_url.rstrip("/")
        tok = (jwt_token or "").strip()
        if not tok:
            env_tok = _load_jwt_from_env()
            if env_tok:
                tok = env_tok
        self.jwt_token = tok

    # ------------------------------------------------------------------
    # Auth / headers
    # ------------------------------------------------------------------
    @staticmethod
    def load_jwt_token_from_env() -> Optional[str]:
        """
        Supports both:
          - PVIZ_JWT_TOKEN (direct)
          - PVIZ_JWT_TOKEN_FILE (Docker secret style)
        """
        tok = os.getenv("PVIZ_JWT_TOKEN")
        if tok and tok.strip():
            return tok.strip()

        path = os.getenv("PVIZ_JWT_TOKEN_FILE")
        if path and path.strip():
            try:
                with open(path.strip(), "r", encoding="utf-8") as f:
                    v = f.read().strip()
                    return v if v else None
            except Exception:
                return None

        return None

    def get_headers(self) -> Dict[str, str]:
        """Return auth headers for API requests."""
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
        }

    # ========================================================================
    # ACCOUNT & BALANCE METHODS
    # ========================================================================

    async def get_account_info(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """
        Get account information.

        Endpoint: GET /auth/me
        """
        endpoint = f"{self.base_url}/auth/me"
        response = await client.get(endpoint, headers=self.get_headers(), timeout=30.0)
        response.raise_for_status()
        return response.json()

    async def get_token_balance(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """
        Get token balance and overview.

        Endpoint: GET /tokens/overview
        """
        endpoint = f"{self.base_url}/tokens/overview"
        response = await client.get(endpoint, headers=self.get_headers(), timeout=30.0)
        response.raise_for_status()
        data = response.json()

        # Normalize for easy access
        return {
            # Your backend uses current_balance (per your earlier frontend typings)
            "balance": data.get("current_balance", 0),
            "plan": data.get("plan", "free"),
            "trial": data.get("trial"),
            "products": data.get("products", []),
            "full_overview": data,
        }

    async def check_sufficient_balance(self, client: httpx.AsyncClient, required_tokens: int) -> Dict[str, Any]:
        """
        Check if user has sufficient token balance.
        """
        balance_data = await self.get_token_balance(client)
        current_balance = int(balance_data.get("balance") or 0)

        can_afford = current_balance >= int(required_tokens)

        result: Dict[str, Any] = {
            "can_afford": can_afford,
            "current_balance": current_balance,
            "required": int(required_tokens),
        }

        if not can_afford:
            result["shortfall"] = int(required_tokens) - current_balance

        return result

    # ========================================================================
    # COST ESTIMATION
    # ========================================================================

    async def estimate_cost(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        github_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Estimate analysis cost for a repository.

        Endpoint: POST /estimate/github
        """
        endpoint = f"{self.base_url}/estimate/github"
        repo_spec = self._parse_repo_url(repo_url)

        payload: Dict[str, Any] = {"repo_spec": repo_spec}
        if github_token:
            payload["github_token"] = github_token

        response = await client.post(endpoint, headers=self.get_headers(), json=payload, timeout=30.0)
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # JOB SUBMISSION
    # ========================================================================

    async def submit_analysis(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        languages: Optional[List[str]] = None,
        pricing_choice: str = "tokens",
        questions: Optional[List[str]] = None,
        github_token: Optional[str] = None,
        expected_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Submit a new analysis job.

        Endpoint: POST /jobs/github

        NOTE:
          - languages is currently NOT USED if your backend doesn’t filter by language yet.
          - expected_tokens: if None, we estimate first (authoritative on backend anyway, but helps UX).
        """
        endpoint = f"{self.base_url}/jobs/github"
        repo_spec = self._parse_repo_url(repo_url)

        if expected_tokens is None:
            estimate = await self.estimate_cost(client, repo_url, github_token)
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
        if pricing_choice == "trial_credit":
            payload["use_trial_credit_requested"] = True

        response = await client.post(endpoint, headers=self.get_headers(), json=payload, timeout=30.0)
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # JOB STATUS
    # ========================================================================

    async def get_job_status(self, client: httpx.AsyncClient, job_id: str) -> Dict[str, Any]:
        """
        Check status of an analysis job.

        Endpoint: GET /jobs/{job_id}
        """
        endpoint = f"{self.base_url}/jobs/{job_id}"
        response = await client.get(endpoint, headers=self.get_headers(), timeout=30.0)
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # JOB HISTORY
    # ========================================================================

    async def get_job_history(self, client: httpx.AsyncClient, limit: int = 10, skip: int = 0) -> Dict[str, Any]:
        """
        Get recent job history.

        Endpoint: GET /jobs

        Backend often supports pagination via ?skip=&limit=.
        We pass them through; if backend ignores them, we still slice defensively.
        """
        endpoint = f"{self.base_url}/jobs"

        limit = max(1, min(int(limit), 50))
        skip = max(0, int(skip))

        response = await client.get(
            endpoint,
            headers=self.get_headers(),
            params={"skip": skip, "limit": limit},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        # Normalize response - backend can return items or jobs
        if isinstance(data, list):
            jobs = data
            total = len(data)
        elif isinstance(data, dict) and "items" in data:
            jobs = data.get("items") or []
            total = int(data.get("total") or len(jobs))
        elif isinstance(data, dict) and "jobs" in data:
            jobs = data.get("jobs") or []
            total = int(data.get("total") or len(jobs))
        else:
            jobs = []
            total = 0

        # Defensive slice
        jobs = jobs[:limit]

        return {"total": total, "jobs": jobs}

    # ========================================================================
    # DOWNLOAD RESULTS
    # ========================================================================

    async def get_download_link(self, client: httpx.AsyncClient, job_id: str) -> Dict[str, Any]:
        """
        Get presigned download link for completed job.

        Endpoint: GET /jobs/{job_id}/download-link
        """
        endpoint = f"{self.base_url}/jobs/{job_id}/download-link"

        # Add timestamp to force fresh link
        response = await client.get(
            endpoint,
            headers=self.get_headers(),
            params={"ts": int(time.time())},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # GAP FIX: One canonical way to get the artifact URL
    # ========================================================================

    async def get_artifact_url_for_job(self, client: httpx.AsyncClient, job_id: str) -> Optional[str]:
        """
        Canonical artifact URL resolver.

        Your status endpoint does NOT include a presigned URL (per your docstring),
        so the only reliable way is:
          1) GET /jobs/{id}   -> confirm status=completed
          2) GET /jobs/{id}/download-link -> return url

        Returns:
          - url string if available
          - None if not completed or no url present
        """
        status = await self.get_job_status(client, job_id)
        if (status.get("status") or "").lower() != "completed":
            return None

        dl = await self.get_download_link(client, job_id)
        url = dl.get("url") or dl.get("s3_url")
        return url if isinstance(url, str) and url.strip() else None

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    def extract_job_id(self, submit_response: Dict[str, Any]) -> str:
        """Extract job ID from submission response."""
        if "job_id" in submit_response and isinstance(submit_response["job_id"], str):
            return submit_response["job_id"]

        raise ValueError(f"Could not find job_id in response: {submit_response}")

    def extract_s3_url(self, status_response: Dict[str, Any]) -> Optional[str]:
        """
        Back-compat helper.

        NOTE: Status response does not include a presigned URL in your backend.
        Prefer: demonstrate this explicitly by returning None.
        Server should call get_download_link() or use get_artifact_url_for_job().
        """
        return None

    def get_job_status_value(self, status_response: Dict[str, Any]) -> str:
        """
        Return the backend's real status value as-is (normalized to lowercase).

        IMPORTANT:
          Do NOT map to "processing/pending" here — the MCP server’s polling logic
          needs the original statuses to determine terminal states reliably.
        """
        v = status_response.get("status", "unknown")
        return v.lower() if isinstance(v, str) else "unknown"

    def _parse_repo_url(self, repo_url: str) -> Dict[str, Any]:
        """
        Parse GitHub URL into repo_spec format.

        Handles:
        - "owner/repo"
        - "https://github.com/owner/repo"
        - "https://github.com/owner/repo/tree/branch"
        - "https://github.com/owner/repo/tree/branch/subpath"
        """
        raw = (repo_url or "").strip()

        # Handle "owner/repo" format (no URL)
        if "/" in raw and not raw.startswith(("http://", "https://")):
            clean_repo = raw.replace(".git", "")
            return {"provider": "github", "repo": clean_repo}

        # Parse full URL
        try:
            parsed = urlparse(raw)

            # Verify it's GitHub-ish
            host = (parsed.hostname or "").lower()
            if not host or "github.com" not in host:
                # Not a GitHub URL, treat as repo name
                return {"provider": "github", "repo": raw}

            # Parse path parts
            path_parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(path_parts) < 2:
                return {"provider": "github", "repo": raw}

            owner = path_parts[0]
            repo = path_parts[1].replace(".git", "")

            repo_spec: Dict[str, Any] = {"provider": "github", "repo": f"{owner}/{repo}"}

            # Branch + subpath
            if len(path_parts) >= 4 and path_parts[2] in ("tree", "blob"):
                repo_spec["branch"] = path_parts[3]
                if len(path_parts) > 4:
                    repo_spec["subpath"] = "/".join(path_parts[4:])

            return repo_spec

        except Exception:
            return {"provider": "github", "repo": raw}


# ==============================================================================
# TESTING YOUR ADAPTER
# ==============================================================================

async def test_adapter():
    """Test your API adapter configuration."""
    adapter = PvizAPIAdapter(
        base_url=os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com"),
        jwt_token=PvizAPIAdapter.load_jwt_token_from_env() or "test-token",
    )

    print("=" * 60)
    print("Testing pviz API Adapter")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # Test 1: Account info
        print("\n1️⃣  Testing get_account_info (GET /auth/me)...")
        try:
            account = await adapter.get_account_info(client)
            print(f"✅ Account: {account.get('email')} ({account.get('plan')} plan)")
            print(f"   Verified: {account.get('is_verified')}")
        except Exception as e:
            print(f"❌ Failed: {e}")
            print("   Check your JWT token!")
            return

        # Test 2: Token balance
        print("\n2️⃣  Testing get_token_balance (GET /tokens/overview)...")
        try:
            balance = await adapter.get_token_balance(client)
            print(f"✅ Balance: {balance['balance']} tokens")
            print(f"   Plan: {balance['plan']}")
            if balance.get("trial"):
                trial = balance["trial"]
                print(f"   Trial active: {trial.get('active')}")
        except Exception as e:
            print(f"❌ Failed: {e}")
            return

        # Test 3: Cost estimation
        print("\n3️⃣  Testing estimate_cost (POST /estimate/github)...")
        test_repo = "facebook/react"
        try:
            estimate = await adapter.estimate_cost(client, test_repo)
            print(f"✅ Estimate for {test_repo}:")
            print(f"   Tokens needed: {estimate.get('tokens_needed')}")
            print(f"   SLOC: {estimate.get('sloc'):,}")
            print(f"   Files: {estimate.get('file_count')}")
            print(f"   Can afford: {estimate.get('can_afford')}")
        except Exception as e:
            print(f"❌ Failed: {e}")
            return

        # Test 4: Job history
        print("\n4️⃣  Testing get_job_history (GET /jobs)...")
        try:
            history = await adapter.get_job_history(client, limit=5, skip=0)
            print(f"✅ Found {history['total']} total jobs")
            print(f"   Returned jobs: {len(history['jobs'])}")
            for job in history["jobs"][:3]:
                print(f"   - {job.get('repo_url')} → {job.get('status')}")
        except Exception as e:
            print(f"❌ Failed: {e}")
            return

        # Test 5: Repo URL parsing
        print("\n5️⃣  Testing repo URL parsing...")
        test_urls = [
            "django/django",
            "https://github.com/facebook/react",
            "https://github.com/vuejs/vue/tree/main/src",
        ]
        for url in test_urls:
            spec = adapter._parse_repo_url(url)
            print(f"✅ {url}")
            print(f"   → {spec}")

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        print("\nAdapter is configured correctly and ready to use.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_adapter())
