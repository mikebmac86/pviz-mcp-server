"""
API Adapter for pviz FastAPI Backend
Configured for actual production endpoints based on TypeScript API client

This adapter interfaces with:
- /auth/me - Account information
- /tokens/overview - Token balance
- /estimate/github - Cost estimation
- /jobs/github - Submit analysis
- /jobs/{id} - Job status
- /jobs - Job history
- /jobs/{id}/download-link - Download results
"""

import os
from typing import Dict, Any, Optional, List
import httpx
from urllib.parse import urlparse


class PvizAPIAdapter:
    """
    Adapter for pviz FastAPI backend.
    Matches the actual production API structure.
    """
    
    def __init__(self, base_url: str, jwt_token: str):
        self.base_url = base_url.rstrip('/')
        self.jwt_token = jwt_token
        
    def get_headers(self) -> Dict[str, str]:
        """Return auth headers for API requests."""
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json"
        }
    
    # ========================================================================
    # ACCOUNT & BALANCE METHODS
    # ========================================================================
    
    async def get_account_info(
        self,
        client: httpx.AsyncClient
    ) -> Dict[str, Any]:
        """
        Get account information.
        
        Endpoint: GET /auth/me
        
        Returns:
            {
                "id": "uuid",
                "email": "user@example.com",
                "plan": "free" | "pro" | "enterprise",
                "is_verified": bool,
                "is_active": bool,
                "is_admin": bool
            }
        """
        endpoint = f"{self.base_url}/auth/me"
        
        response = await client.get(
            endpoint,
            headers=self.get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    async def get_token_balance(
        self,
        client: httpx.AsyncClient
    ) -> Dict[str, Any]:
        """
        Get token balance and overview.
        
        Endpoint: GET /tokens/overview
        
        Returns:
            {
                "balance": int,
                "plan": str,
                "trial": {...} | None,
                "token_packs": [...],
                "repo_packs": [...],
                "full_overview": {...}
            }
        """
        endpoint = f"{self.base_url}/tokens/overview"
        
        response = await client.get(
            endpoint,
            headers=self.get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        
        # Normalize for easy access
        return {
            "balance": data.get("current_balance", 0),
            "plan": data.get("plan", "free"),
            "trial": data.get("trial"),
            "products": data.get("products", []),
            "full_overview": data
        }
    
    async def check_sufficient_balance(
        self,
        client: httpx.AsyncClient,
        required_tokens: int
    ) -> Dict[str, Any]:
        """
        Check if user has sufficient token balance.
        
        Args:
            client: HTTP client
            required_tokens: Tokens needed
            
        Returns:
            {
                "can_afford": bool,
                "current_balance": int,
                "required": int,
                "shortfall": int (if insufficient)
            }
        """
        balance_data = await self.get_token_balance(client)
        current_balance = balance_data["balance"]
        
        can_afford = current_balance >= required_tokens
        
        result = {
            "can_afford": can_afford,
            "current_balance": current_balance,
            "required": required_tokens
        }
        
        if not can_afford:
            result["shortfall"] = required_tokens - current_balance
        
        return result
    
    # ========================================================================
    # COST ESTIMATION
    # ========================================================================
    
    async def estimate_cost(
        self,
        client: httpx.AsyncClient,
        repo_url: str,
        github_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Estimate analysis cost for a repository.
        
        Endpoint: POST /estimate/github
        
        Args:
            client: HTTP client
            repo_url: Repository URL or "owner/repo"
            github_token: Optional GitHub token for private repos
            
        Returns:
            {
                "normalized_repo_url": str,
                "branch": str,
                "file_count": int,
                "sloc": int,
                "tokens_needed": int,
                "user_token_balance": int,
                "can_afford": bool,
                "estimated_cost_cents": int,
                "repo_pack": {...} | None,
                "coverage_percent": float | None,
                "language_breakdown": {...} | None
            }
        """
        endpoint = f"{self.base_url}/estimate/github"
        
        # Parse repo URL to repo_spec format
        repo_spec = self._parse_repo_url(repo_url)
        
        payload = {
            "repo_spec": repo_spec
        }
        
        if github_token:
            payload["github_token"] = github_token
        
        response = await client.post(
            endpoint,
            headers=self.get_headers(),
            json=payload,
            timeout=30.0
        )
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
        expected_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Submit a new analysis job.
        
        Endpoint: POST /jobs/github
        
        Args:
            client: HTTP client
            repo_url: Repository URL
            languages: NOT USED (backend doesn't support language filtering yet)
            pricing_choice: "tokens" | "trial_credit" | "one_off_analysis"
            questions: Optional list of LLM questions
            github_token: Optional GitHub token
            expected_tokens: Token estimate (if not provided, will estimate first)
            
        Returns:
            Success:
                {
                    "job_id": str,
                    "status": str
                }
            
            Insufficient tokens:
                {
                    "job_id": str | None,
                    "status": "insufficient_tokens",
                    "reason": "insufficient_tokens",
                    "actions": {
                        "buy_tokens_url": str,
                        "repo_purchase_product_id": str | None,
                        ...
                    }
                }
            
            Awaiting payment:
                {
                    "job_id": str,
                    "status": "awaiting_payment",
                    "checkout_url": str
                }
        """
        endpoint = f"{self.base_url}/jobs/github"
        
        # Parse repo URL to repo_spec format
        repo_spec = self._parse_repo_url(repo_url)
        
        # Get token estimate if not provided
        if expected_tokens is None:
            estimate = await self.estimate_cost(client, repo_url, github_token)
            expected_tokens = estimate.get("tokens_needed", 0)
        
        payload = {
            "repo_spec": repo_spec,
            "expected_tokens": expected_tokens,
            "pricing_choice": pricing_choice
        }
        
        if questions and len(questions) > 0:
            payload["questions"] = questions
        
        if github_token:
            payload["github_token"] = github_token
        
        if pricing_choice == "trial_credit":
            payload["use_trial_credit_requested"] = True
        
        response = await client.post(
            endpoint,
            headers=self.get_headers(),
            json=payload,
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    # ========================================================================
    # JOB STATUS
    # ========================================================================
    
    async def get_job_status(
        self,
        client: httpx.AsyncClient,
        job_id: str
    ) -> Dict[str, Any]:
        """
        Check status of an analysis job.
        
        Endpoint: GET /jobs/{job_id}
        
        Returns:
            {
                "id": str,
                "repo_url": str,
                "branch": str,
                "status": "awaiting_payment" | "queued_precheck" | "running" | 
                          "completed" | "failed" | "insufficient_tokens" | 
                          "cancel_requested" | "canceled",
                "created_at": str,
                "completed_at": str | None,
                "estimated_tokens": int | None,
                "tokens_charged": int | None,
                "artifact_path": str | None,
                "error_message": str | None,
                "checkout_url": str | None,
                ...
            }
        """
        endpoint = f"{self.base_url}/jobs/{job_id}"
        
        response = await client.get(
            endpoint,
            headers=self.get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    # ========================================================================
    # JOB HISTORY
    # ========================================================================
    
    async def get_job_history(
        self,
        client: httpx.AsyncClient,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Get recent job history.
        
        Endpoint: GET /jobs
        
        Returns:
            {
                "total": int,
                "jobs": [JobSummary, ...]
            }
        """
        endpoint = f"{self.base_url}/jobs"
        
        response = await client.get(
            endpoint,
            headers=self.get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        
        # Normalize response - backend can return items or jobs
        if isinstance(data, list):
            # Old format: just array
            jobs = data
            total = len(data)
        elif "items" in data:
            # New format: {total, items}
            jobs = data["items"]
            total = data.get("total", len(jobs))
        elif "jobs" in data:
            # Alternative format: {total, jobs}
            jobs = data["jobs"]
            total = data.get("total", len(jobs))
        else:
            jobs = []
            total = 0
        
        return {
            "total": total,
            "jobs": jobs[:limit]
        }
    
    # ========================================================================
    # DOWNLOAD RESULTS
    # ========================================================================
    
    async def get_download_link(
        self,
        client: httpx.AsyncClient,
        job_id: str
    ) -> Dict[str, Any]:
        """
        Get presigned download link for completed job.
        
        Endpoint: GET /jobs/{job_id}/download-link
        
        Returns:
            {
                "url": str,
                "expires_at": str
            }
        """
        endpoint = f"{self.base_url}/jobs/{job_id}/download-link"
        
        # Add timestamp to force fresh link
        timestamp = f"?ts={int(__import__('time').time())}"
        
        response = await client.get(
            endpoint + timestamp,
            headers=self.get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def extract_job_id(self, submit_response: Dict[str, Any]) -> str:
        """Extract job ID from submission response."""
        if "job_id" in submit_response:
            return submit_response["job_id"]
        
        raise ValueError(
            f"Could not find job_id in response: {submit_response}"
        )
    
    def extract_s3_url(self, status_response: Dict[str, Any]) -> Optional[str]:
        """
        Extract download URL from status response.
        
        Note: Your API doesn't include S3 URL in status response.
        Need to call get_download_link() separately.
        """
        # artifact_path is internal, not a download URL
        return None
    
    def get_job_status_value(self, status_response: Dict[str, Any]) -> str:
        """
        Extract and normalize status value.
        
        Maps your backend statuses to normalized values:
        - "completed" → completed
        - "failed", "canceled" → failed
        - "running", "queued_precheck" → processing
        - "awaiting_payment", "insufficient_tokens" → pending
        """
        status = status_response.get("status", "unknown").lower()
        
        # Map to normalized statuses
        if status == "completed":
            return "completed"
        elif status in ["failed", "canceled"]:
            return "failed"
        elif status in ["running", "queued_precheck"]:
            return "processing"
        elif status in ["awaiting_payment", "insufficient_tokens"]:
            return "pending"
        elif status == "cancel_requested":
            return "processing"  # Still processing, just marked for cancel
        
        return status
    
    def _parse_repo_url(self, repo_url: str) -> Dict[str, Any]:
        """
        Parse GitHub URL into repo_spec format.
        
        Handles:
        - "owner/repo"
        - "https://github.com/owner/repo"
        - "https://github.com/owner/repo/tree/branch"
        - "https://github.com/owner/repo/tree/branch/subpath"
        
        Returns:
            {
                "provider": "github",
                "repo": "owner/repo",
                "branch": "main" (optional),
                "subpath": "path/to/dir" (optional)
            }
        """
        raw = (repo_url or "").strip()
        
        # Handle "owner/repo" format (no URL)
        if "/" in raw and not raw.startswith("http"):
            # Simple "owner/repo" or "owner/repo.git"
            clean_repo = raw.replace(".git", "")
            return {
                "provider": "github",
                "repo": clean_repo
            }
        
        # Parse full URL
        try:
            parsed = urlparse(raw)
            
            # Verify it's GitHub
            if not parsed.hostname or "github.com" not in parsed.hostname:
                # Not a GitHub URL, treat as repo name
                return {
                    "provider": "github",
                    "repo": raw
                }
            
            # Parse path: /owner/repo or /owner/repo/tree/branch/subpath
            path_parts = [p for p in parsed.path.strip("/").split("/") if p]
            
            if len(path_parts) < 2:
                # Invalid GitHub URL
                return {
                    "provider": "github",
                    "repo": raw
                }
            
            owner = path_parts[0]
            repo = path_parts[1].replace(".git", "")
            
            repo_spec = {
                "provider": "github",
                "repo": f"{owner}/{repo}"
            }
            
            # Check for branch and subpath
            # Format: /owner/repo/tree/branch/subpath or /owner/repo/blob/branch/file
            if len(path_parts) >= 4 and path_parts[2] in ["tree", "blob"]:
                branch = path_parts[3]
                repo_spec["branch"] = branch
                
                # Subpath is everything after branch
                if len(path_parts) > 4:
                    subpath = "/".join(path_parts[4:])
                    repo_spec["subpath"] = subpath
            
            return repo_spec
            
        except Exception as e:
            # Fallback: treat as repo name
            return {
                "provider": "github",
                "repo": raw
            }


# ==============================================================================
# TESTING YOUR ADAPTER
# ==============================================================================

async def test_adapter():
    """Test your API adapter configuration."""
    import asyncio
    
    adapter = PvizAPIAdapter(
        base_url=os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com"),
        jwt_token=os.getenv("PVIZ_JWT_TOKEN", "test-token")
    )
    
    print("="*60)
    print("Testing pviz API Adapter")
    print("="*60)
    
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
            if balance.get('trial'):
                trial = balance['trial']
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
            history = await adapter.get_job_history(client, limit=5)
            print(f"✅ Found {history['total']} total jobs")
            print(f"   Recent jobs: {len(history['jobs'])}")
            for job in history['jobs'][:3]:
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
        
        print("\n" + "="*60)
        print("✅ All tests passed!")
        print("="*60)
        print("\nAdapter is configured correctly and ready to use.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_adapter())