pviz API Endpoints - Complete Documentation
This file documents the actual endpoints used by your FastAPI backend at api.pvizgenerator.com.
Based on your TypeScript API client, here are the confirmed endpoints:

Authentication
All API requests require JWT authentication via Bearer token:
bashAuthorization: Bearer YOUR_JWT_TOKEN_HERE
How to get your JWT token:

Sign up at https://pvizgenerator.com
Go to Dashboard → Settings → API Keys
Copy your JWT token
Set as PVIZ_JWT_TOKEN environment variable


Core Endpoints
1. Account Information
Method: GET
URL: {BASE_URL}/auth/me
Auth: Required (Bearer token)
Response:
json{
  "id": "uuid-string",
  "email": "user@example.com",
  "plan": "free" | "pro" | "enterprise",
  "is_verified": true,
  "is_active": true,
  "is_admin": false
}
cURL Example:
bashcurl https://api.pvizgenerator.com/auth/me \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

2. Token Balance & Overview
Method: GET
URL: {BASE_URL}/tokens/overview
Auth: Required (Bearer token)
Response:
json{
  "current_balance": 42,
  "plan": "pro",
  "products": [
    {
      "id": "prod_123",
      "sku": "tokens_100",
      "name": "100 Tokens",
      "tokens_granted": 100,
      "price_cents": 1000,
      "currency": "usd"
    }
  ],
  "trial": {
    "active": true,
    "ends_at": "2026-02-10T00:00:00Z",
    "mid_repo_credits_available": 3,
    "trial_tokens_available": 10
  }
}
Key Fields:

current_balance - Available tokens for analysis
trial.trial_tokens_available - Trial credits remaining
products - Available token packs to purchase


3. Cost Estimation
Method: POST
URL: {BASE_URL}/estimate/github
Auth: Required (Bearer token)
Request Body:
json{
  "repo_spec": {
    "provider": "github",
    "repo": "django/django",
    "branch": "main",           // optional
    "subpath": "src/app"       // optional
  },
  "github_token": "ghp_..."    // optional, for private repos
}
Response:
json{
  "normalized_repo_url": "https://github.com/django/django",
  "branch": "main",
  "subpath": null,
  "file_count": 1234,
  "sloc": 456789,
  "tokens_needed": 5,
  "user_token_balance": 42,
  "can_afford": true,
  "estimated_cost_cents": 500,
  "repo_pack": {
    "product_id": "prod_456",
    "sku": "repo_large",
    "name": "Large Repo Analysis",
    "price_cents": 500,
    "sloc_min": 100000,
    "sloc_max": 500000
  },
  "coverage_percent": 87.5,
  "language_breakdown": {
    "Python": 123456,
    "JavaScript": 45678,
    "CSS": 12345
  }
}
Key Fields:

tokens_needed - Cost in tokens
can_afford - Whether user has enough tokens
language_breakdown - SLOC by language
repo_pack - Suggested one-off purchase option (if can't afford with tokens)

cURL Example:
bashcurl -X POST https://api.pvizgenerator.com/estimate/github \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_spec": {
      "provider": "github",
      "repo": "facebook/react"
    }
  }'

4. Submit Analysis Job
Method: POST
URL: {BASE_URL}/jobs/github
Auth: Required (Bearer token)
Request Body:
json{
  "repo_spec": {
    "provider": "github",
    "repo": "django/django",
    "branch": "main",
    "subpath": null
  },
  "expected_tokens": 5,
  "pricing_choice": "tokens" | "trial_credit" | "one_off_analysis",
  "use_trial_credit_requested": false,
  "questions": [
    "What are the main architectural patterns?",
    "Are there any circular dependencies?"
  ],
  "estimated_repo_pack_id": "prod_456",
  "github_token": "ghp_..."
}
Pricing Choices:

"tokens" - Use account token balance (default)
"trial_credit" - Use trial credits (requires use_trial_credit_requested: true)
"one_off_analysis" - One-time purchase via Stripe checkout

Response (Success):
json{
  "job_id": "job_abc123",
  "status": "queued_precheck"
}
Response (Insufficient Tokens):
json{
  "job_id": null,
  "status": "insufficient_tokens",
  "reason": "insufficient_tokens",
  "actions": {
    "buy_tokens_url": "/tokens",
    "repo_purchase_product_id": "prod_456",
    "repo_purchase_sku": "repo_large",
    "repo_purchase_price_cents": 500,
    "currency": "usd"
  }
}
Response (Awaiting Payment):
json{
  "job_id": "job_abc123",
  "status": "awaiting_payment",
  "checkout_url": "https://checkout.stripe.com/..."
}

5. Job Status
Method: GET
URL: {BASE_URL}/jobs/{job_id}
Auth: Required (Bearer token)
Response (Queued):
json{
  "id": "job_abc123",
  "repo_url": "https://github.com/django/django",
  "branch": "main",
  "status": "queued_precheck",
  "created_at": "2026-01-10T12:00:00Z",
  "estimated_tokens": 5,
  "tokens_charged": null
}
Response (Running):
json{
  "id": "job_abc123",
  "repo_url": "https://github.com/django/django",
  "branch": "main",
  "status": "running",
  "created_at": "2026-01-10T12:00:00Z",
  "pre_file_count": 1234,
  "pre_sloc": 456789,
  "estimated_tokens": 5,
  "tokens_charged": null
}
Response (Completed):
json{
  "id": "job_abc123",
  "repo_url": "https://github.com/django/django",
  "branch": "main",
  "status": "completed",
  "created_at": "2026-01-10T12:00:00Z",
  "completed_at": "2026-01-10T12:05:30Z",
  "estimated_tokens": 5,
  "tokens_charged": 5,
  "artifact_path": "s3://pviz-results/job_abc123.json",
  "pre_file_count": 1234,
  "pre_sloc": 456789
}
Response (Failed):
json{
  "id": "job_abc123",
  "repo_url": "https://github.com/invalid/repo",
  "branch": "main",
  "status": "failed",
  "created_at": "2026-01-10T12:00:00Z",
  "error_code": "repo_not_found",
  "error_message": "Could not clone repository: 404 Not Found",
  "tokens_charged": 0
}

6. Job Download Link
Method: GET
URL: {BASE_URL}/jobs/{job_id}/download-link
Auth: Required (Bearer token)
Query: Optional ?ts={timestamp} to force fresh link
Response:
json{
  "url": "https://s3.amazonaws.com/pviz-results/job_abc123.json?AWSAccessKeyId=...&Expires=...",
  "expires_at": "2026-01-10T13:00:00Z"
}
Note: The presigned URL expires after ~1 hour. Call this endpoint again to get a fresh URL.

7. Job History
Method: GET
URL: {BASE_URL}/jobs
Auth: Required (Bearer token)
Query: Optional ?skip=0&limit=10
Response:
json{
  "total": 47,
  "items": [
    {
      "id": "job_abc123",
      "repo_url": "https://github.com/django/django",
      "branch": "main",
      "status": "completed",
      "created_at": "2026-01-10T12:00:00Z",
      "completed_at": "2026-01-10T12:05:30Z",
      "estimated_tokens": 5,
      "tokens_charged": 5
    },
    {
      "id": "job_xyz789",
      "repo_url": "https://github.com/facebook/react",
      "branch": "main",
      "status": "failed",
      "created_at": "2026-01-09T15:30:00Z",
      "error_code": "timeout"
    }
  ]
}
Alternative response format (older versions):
json{
  "total": 47,
  "jobs": [...]  // Same as "items" above
}

Status Values
StatusPhaseMeaningawaiting_paymentPre-analysisNeeds Stripe payment to proceedinsufficient_tokensPre-analysisNot enough tokens, needs purchasequeued_precheckPre-analysisIn queue for initial validationrunningAnalysisAnalysis is actively processingcompletedDoneAnalysis finished successfullyfailedDoneAnalysis failed with errorcancel_requestedCancelingUser requested cancellationcanceledDoneJob was canceled
Normalized statuses for MCP adapter:

"queued_precheck" → "processing"
"running" → "processing"
"completed" → "completed"
"failed", "canceled" → "failed"
"awaiting_payment", "insufficient_tokens" → "pending"


Error Codes
HTTP StatusErrorMeaning401UnauthorizedInvalid or missing JWT token402Payment RequiredInsufficient tokens (may also return 200 with status: "insufficient_tokens")404Not FoundJob ID or resource not found422Validation ErrorInvalid request parameters (Pydantic validation)429Too Many RequestsRate limit exceeded500Internal Server ErrorServer error

Dependency Graph Schema
The downloaded JSON follows the pviz-llm-bundle@v1.1 schema:
json{
  "schema_version": "pviz-llm-bundle@v1.1",
  "repository": {
    "url": "https://github.com/django/django",
    "analyzed_at": "2026-01-10T12:05:00Z",
    "branch": "main"
  },
  "summary": {
    "total_modules": 409,
    "total_dependencies": 1038,
    "languages": {
      "Python": 364,
      "JavaScript": 43
    },
    "circular_dependency_groups": 0
  },
  "modules": [
    {
      "path": "src/index.py",
      "language": "Python",
      "loc": 156,
      "functions": 8,
      "classes": 2,
      "dependencies": [
        {
          "path": "src/utils.py",
          "type": "import"
        }
      ]
    }
  ],
  "scc_analysis": {
    "circular_dependency_groups": []
  }
}

Testing Your Configuration
Run this test script to verify all endpoints work:
bashexport PVIZ_JWT_TOKEN="your-token"
export PVIZ_API_URL="https://api.pvizgenerator.com"

# Test adapter
python api_adapter.py
Expected output:
✅ Account: user@example.com (pro plan)
✅ Balance: 42 tokens
✅ Estimate for facebook/react: Tokens needed: 3
✅ Found 15 total jobs
✅ All tests passed!

Additional Information Needed
All confirmed:

✅ Base URL - https://api.pvizgenerator.com
✅ Authentication - Bearer JWT tokens
✅ Account endpoint - GET /auth/me
✅ Token balance - GET /tokens/overview
✅ Cost estimation - POST /estimate/github
✅ Submit job - POST /jobs/github
✅ Job status - GET /jobs/{id}
✅ Download link - GET /jobs/{id}/download-link
✅ Job history - GET /jobs
✅ Job cancellation - POST /jobs/{id}/cancel


Rate Limits
Your API implements the following rate limits (extracted from backend code):
Authentication Endpoints:

Login (POST /auth/login): 10 attempts per 5 minutes
Signup (POST /auth/signup): 3 signups per hour
Logout (POST /auth/logout): 20 logouts per minute
Email Verification (POST /auth/verify): 10 attempts per 5 minutes
Resend Verification (POST /auth/verify/resend): 3 resends per 5 minutes
Password Reset Request (POST /auth/forgot-password): 5 requests per hour
Password Reset (POST /auth/reset-password): 10 attempts per 5 minutes

Analysis Endpoints:

Current User (GET /auth/me): 300 requests per minute
Cost Estimation (POST /estimate/github): 10 estimates per 5 minutes
Submit Job (POST /jobs/github): 20 jobs per hour
Download Link (GET /jobs/{id}/download-link): 10 requests per minute

Other Endpoints:

Stripe Webhooks (POST /webhooks/stripe): 100 requests per minute

Rate Limit Scope: Per user (based on IP address or JWT token)
Rate Limit Response (429 Too Many Requests):
json{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Please try again in X seconds",
  "retry_after": 300
}

Repository Limits
Based on your backend analysis engine:

Maximum SLOC: No hard limit (practical limit ~500,000 SLOC for reasonable processing time)
File count: No hard limit documented
Concurrent jobs per user: Not explicitly limited (backend worker capacity determines throughput)
Languages supported: Python, TypeScript, JavaScript, Java, Go, and more

Private Repositories:

Require GitHub personal access token
Token used only during analysis, never stored


Webhooks
Status: Not currently documented in TypeScript client
If webhook support exists:

Check backend for POST /webhooks/register or similar
Would allow job completion notifications instead of polling

Current approach: MCP server polls job status until completion

Additional Notes
Job Cancellation:

Endpoint: POST /jobs/{job_id}/cancel
Returns: {"status": "canceled"} or {"status": "cancel_requested"}
Cooperative cancellation (running jobs complete current step)

Purchase History:

Endpoint: GET /billing/history?skip=0&limit=50
Not needed for MCP server (users can view in dashboard)
Could be added as optional MCP tool if desired