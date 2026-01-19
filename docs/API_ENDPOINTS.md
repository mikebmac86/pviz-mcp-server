# PViz Generator API  
**Complete Backend API Documentation (Authoritative)**

**Base URL:**  
```
https://api.pvizgenerator.com
```

This document describes the **production FastAPI endpoints** exposed by the PViz Generator backend and consumed by the official TypeScript client and MCP adapter.  
All endpoints and behaviors documented here are **confirmed against the live backend**.

---

## Table of Contents

1. Authentication  
2. Core API Endpoints  
3. Status Values  
4. Error Codes  
5. Analysis Output Schema  
6. Rate Limits  
7. Repository & Language Limits  
8. Private Repositories  
9. Webhooks  
10. Testing Your Configuration  
11. Documentation Status  

---

## Authentication

All API requests require **JWT authentication** using the `Authorization` header:

```
Authorization: Bearer YOUR_JWT_TOKEN
```

### Obtaining a JWT Token

1. Sign up at https://pvizgenerator.com  
2. Navigate to Dashboard → Settings → API Keys  
3. Copy your JWT token  
4. Export it as an environment variable:

```bash
export PVIZ_JWT_TOKEN="your-token-here"
export PVIZ_API_URL="https://api.pvizgenerator.com"
```

---

## Core API Endpoints

### GET /auth/me

Returns authenticated account details.

**Request:**
```http
GET /auth/me
Authorization: Bearer YOUR_JWT_TOKEN
```

**Response (200 OK):**
```json
{
  "email": "user@example.com",
  "email_verified": true,
  "plan": "pro",
  "created_at": "2024-01-15T10:30:00Z"
}
```

---

### GET /tokens/overview

Returns token balance, plan, trial info, and purchasable products.

**Request:**
```http
GET /tokens/overview
Authorization: Bearer YOUR_JWT_TOKEN
```

**Response (200 OK):**
```json
{
  "balance": 1500,
  "plan": "pro",
  "trial": {
    "active": false,
    "credits_remaining": 0
  },
  "products": [
    {
      "id": "tokens_100",
      "name": "100 Analysis Tokens",
      "price": 10.00,
      "currency": "USD"
    }
  ]
}
```

---

### POST /estimate/github

Estimates repository analysis cost and coverage.

**Request:**
```http
POST /estimate/github
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "repo_url": "https://github.com/django/django",
  "github_token": "ghp_xxxxx"  // Optional, required for private repos
}
```

**Response (200 OK):**
```json
{
  "tokens_needed": 250,
  "sloc": 125000,
  "file_count": 1543,
  "can_afford": true,
  "supported_languages": ["Python"],
  "estimated_duration_seconds": 180
}
```

**Response (200 OK - Private repo without token):**
```json
{
  "success": false,
  "error": "private_repository",
  "message": "This repository is private. Please provide a GitHub Personal Access Token.",
  "requires_github_token": true
}
```

---

### POST /jobs/github

Submits an analysis job.

**Request:**
```http
POST /jobs/github
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "repo_url": "https://github.com/django/django",
  "github_token": "ghp_xxxxx",  // Optional
  "questions": [  // Optional
    "What are the main architectural patterns?",
    "Are there any circular dependencies?"
  ],
  "pricing_choice": "tokens"  // or "trial_credit"
}
```

**Response (201 Created):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "queued_precheck",
  "repo_url": "https://github.com/django/django",
  "tokens_charged": 250,
  "created_at": "2026-01-11T12:00:00Z"
}
```

---

### GET /jobs/{job_id}

Returns job status and metadata.

**Request:**
```http
GET /jobs/job_a1b2c3d4e5f6
Authorization: Bearer YOUR_JWT_TOKEN
```

**Response (200 OK - Running):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "running",
  "repo_url": "https://github.com/django/django",
  "progress": 45,
  "created_at": "2026-01-11T12:00:00Z",
  "started_at": "2026-01-11T12:00:15Z"
}
```

**Response (200 OK - Completed):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "completed",
  "repo_url": "https://github.com/django/django",
  "created_at": "2026-01-11T12:00:00Z",
  "started_at": "2026-01-11T12:00:15Z",
  "completed_at": "2026-01-11T12:03:45Z",
  "duration_seconds": 210,
  "artifact_available": true
}
```

---

### GET /jobs/{job_id}/download-link

Returns a presigned S3 URL for downloading the analysis artifact.

**Request:**
```http
GET /jobs/job_a1b2c3d4e5f6/download-link
Authorization: Bearer YOUR_JWT_TOKEN
```

**Response (200 OK):**
```json
{
  "download_url": "https://pviz-artifacts.s3.amazonaws.com/...",
  "expires_at": "2026-01-11T13:00:00Z",
  "expires_in_seconds": 3600
}
```

**Note:** Presigned URLs expire after 1 hour. Request a new link if expired.

---

### GET /jobs

Returns job history with pagination.

**Request:**
```http
GET /jobs?limit=10&skip=0
Authorization: Bearer YOUR_JWT_TOKEN
```

**Response (200 OK):**
```json
{
  "total": 42,
  "jobs": [
    {
      "job_id": "job_a1b2c3d4e5f6",
      "status": "completed",
      "repo_url": "https://github.com/django/django",
      "created_at": "2026-01-11T12:00:00Z",
      "completed_at": "2026-01-11T12:03:45Z"
    },
    {
      "job_id": "job_xyz789",
      "status": "failed",
      "repo_url": "https://github.com/invalid/repo",
      "created_at": "2026-01-10T15:30:00Z",
      "error": "Repository not found"
    }
  ]
}
```

---

### POST /jobs/{job_id}/cancel

Requests cancellation of a running job.

**Request:**
```http
POST /jobs/job_a1b2c3d4e5f6/cancel
Authorization: Bearer YOUR_JWT_TOKEN
```

**Response (200 OK):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "cancel_requested",
  "message": "Cancellation requested. Job will stop within 30 seconds."
}
```

**Note:** No refund for partially completed work.

---

## Status Values

Jobs progress through the following statuses:

| Status | Description |
|--------|-------------|
| `queued_precheck` | Job queued, awaiting initial validation |
| `running` | Analysis in progress |
| `completed` | Analysis finished successfully |
| `failed` | Analysis encountered an error |
| `cancel_requested` | User requested cancellation |
| `canceled` | Job canceled successfully |
| `awaiting_payment` | Insufficient tokens, payment required |
| `insufficient_tokens` | Not enough tokens to start analysis |

### Job Lifecycle

**Normal flow:**
```
queued_precheck → running → completed
```

**Failure paths:**
```
queued_precheck → failed               (validation error)
queued_precheck → insufficient_tokens  (balance check)
running → failed                       (processing error)
running → cancel_requested → canceled  (user cancellation)
```

### Typical Timings

- **Small repositories** (<10K SLOC): 30-90 seconds
- **Medium repositories** (10K-50K SLOC): 2-5 minutes
- **Large repositories** (50K-100K SLOC): 5-15 minutes
- **Very large repositories** (100K+ SLOC): 15-30 minutes

### Polling Recommendations

When waiting for job completion:

1. **Initial polling:** Check every 5 seconds for the first minute
2. **Extended polling:** After 1 minute, check every 10 seconds
3. **Timeout:** Consider timeout after 10 minutes for typical repos
4. **Backoff:** Increase interval to 15-30 seconds for large repos

**Example polling strategy:**
```python
import time

poll_interval = 5
max_attempts = 120  # 10 minutes at 5-second intervals
attempts = 0

while attempts < max_attempts:
    status = get_job_status(job_id)
    
    if status in ["completed", "failed", "canceled"]:
        break
    
    # Increase interval after 1 minute
    if attempts > 12:  # 12 * 5s = 1 minute
        poll_interval = 10
    
    time.sleep(poll_interval)
    attempts += 1
```

### Cancellation Behavior

- Status transitions to `cancel_requested` immediately
- Actual cancellation may take 10-30 seconds
- Jobs canceled during analysis show partial progress
- **No refunds** for partially completed work
- Download links unavailable for canceled jobs

---

## Error Codes

All API errors follow a consistent response format.

### Error Response Format

```json
{
  "detail": "Human-readable error message",
  "error_code": "MACHINE_READABLE_CODE",
  "request_id": "req_abc123xyz"
}
```

### Common Error Responses

#### 401 Unauthorized

Missing or invalid JWT token.

**Response:**
```json
{
  "detail": "Invalid or expired JWT token",
  "error_code": "INVALID_TOKEN"
}
```

**Common causes:**
- Token not provided in Authorization header
- Token expired (tokens don't expire, but check account status)
- Token malformed or corrupted
- Account suspended or deleted

**Fix:** Generate a new token from the dashboard.

---

#### 402 Payment Required

Insufficient tokens to complete the operation.

**Response:**
```json
{
  "detail": "Insufficient tokens. Required: 250, Available: 42",
  "error_code": "INSUFFICIENT_TOKENS",
  "required_tokens": 250,
  "current_balance": 42,
  "shortfall": 208
}
```

**Fix:** Purchase additional tokens or use trial credits if available.

---

#### 404 Not Found

Resource does not exist.

**Response:**
```json
{
  "detail": "Job not found: job_invalid123",
  "error_code": "JOB_NOT_FOUND"
}
```

**Common causes:**
- Job ID doesn't exist
- Job belongs to different account
- Repository URL invalid

---

#### 422 Validation Error

Request body validation failed.

**Response:**
```json
{
  "detail": [
    {
      "loc": ["body", "repo_url"],
      "msg": "Invalid GitHub repository URL",
      "type": "value_error"
    }
  ],
  "error_code": "VALIDATION_ERROR"
}
```

**Common causes:**
- Invalid repository URL format
- Missing required fields
- Invalid parameter types

---

#### 429 Too Many Requests

Rate limit exceeded.

**Response:**
```json
{
  "detail": "Rate limit exceeded: 20 job submissions per hour",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 1800,
  "limit": 20,
  "window": "1 hour"
}
```

**Fix:** Wait for the time specified in `retry_after` (seconds) before retrying.

---

#### 500 Internal Server Error

Unexpected server error.

**Response:**
```json
{
  "detail": "Internal server error occurred",
  "error_code": "INTERNAL_ERROR",
  "request_id": "req_abc123xyz"
}
```

**Fix:** Retry the request. If the error persists, contact support with the `request_id`.

---

### Error Handling Best Practices

```python
import httpx

try:
    response = httpx.post(
        f"{api_url}/jobs/github",
        headers={"Authorization": f"Bearer {token}"},
        json={"repo_url": repo_url}
    )
    response.raise_for_status()
    return response.json()
    
except httpx.HTTPStatusError as e:
    error_data = e.response.json()
    
    if e.response.status_code == 401:
        # Invalid token - regenerate
        raise AuthenticationError("Token invalid or expired")
        
    elif e.response.status_code == 402:
        # Insufficient tokens
        shortfall = error_data.get("shortfall", 0)
        raise InsufficientTokensError(f"Need {shortfall} more tokens")
        
    elif e.response.status_code == 429:
        # Rate limited
        retry_after = error_data.get("retry_after", 60)
        raise RateLimitError(f"Retry after {retry_after} seconds")
        
    else:
        raise APIError(f"API error: {error_data.get('detail')}")
```  

---

## Analysis Output Schema

Analysis artifacts follow the `pviz-llm-bundle@v1.1` schema, optimized for LLM ingestion and interpretation.

### Artifact Structure

**Top-level JSON schema:**

```json
{
  "metadata": {
    "repo_url": "https://github.com/django/django",
    "commit_sha": "abc123def456...",
    "analyzed_at": "2026-01-11T12:03:45Z",
    "pviz_version": "v1.1",
    "languages": ["Python"],
    "total_files": 1543,
    "total_sloc": 125000
  },
  "dependencies": [
    {
      "source": "django.core.handlers",
      "target": "django.http",
      "import_type": "module",
      "file_path": "django/core/handlers/base.py",
      "line_number": 12
    }
  ],
  "metrics": {
    "total_files": 1543,
    "total_sloc": 125000,
    "total_dependencies": 8234,
    "avg_dependencies_per_file": 5.3,
    "max_dependencies": 45,
    "modularity_score": 0.78,
    "coupling_score": 0.32
  },
  "circular_dependencies": [
    {
      "chain": [
        "django.db.models",
        "django.db.models.fields",
        "django.db.models.base",
        "django.db.models"
      ],
      "length": 4,
      "severity": "medium"
    }
  ],
  "architecture_summary": {
    "layer_count": 4,
    "main_packages": [
      "django.db",
      "django.core",
      "django.contrib"
    ],
    "architectural_patterns": [
      "MVC",
      "Plugin Architecture"
    ]
  }
}
```

### Field Definitions

#### Schema Structure

The artifact uses a **schema-encoded format** where field names are abbreviated and stored in a legend. This reduces size by ~50-60% while remaining lossless.
```json
{
  "schema_version": "pviz-llm-bundle@v1.1",
  "meta": {...},
  "nodes": {
    "schema": ["f", "n", "lang", ...],    // Abbreviated field names
    "legend": {"f": "file", "n": "name", ...},  // Field mappings
    "enums": {...},                        // Enum value mappings
    "type_strings": [...],                 // Deduplicated type hints
    "rows": {...}                          // Actual node data
  },
  "edges": [...],
  "node_order": [...],
  "summary": {...},
  "discovery": {...},
  "discovery_manifest": {...}
}
```

---

#### meta (Top-level metadata)

| Field | Type | Description |
|-------|------|-------------|
| `generated_at` | ISO8601 | Analysis timestamp |
| `mode` | string | Analysis mode ("zones", "full", etc.) |
| `language` | string | Primary or "polyglot" for multi-language |
| `languages` | array | All languages detected (e.g., ["python", "typescript"]) |
| `bundled_by_lang` | object | File count per language |
| `repo_root` | string | Local analysis path |
| `repo_name` | string | Repository identifier |
| `edges_meta` | object | Edge schema info and statistics |

**Example:**
```json
{
  "generated_at": "2026-01-18T07:15:28.821381Z",
  "mode": "zones",
  "language": "polyglot",
  "languages": ["go", "java", "javascript", "mjs", "python", "typescript"],
  "bundled_by_lang": {"python": 451, "typescript": 54, "javascript": 2},
  "repo_root": "/tmp/pviz-repo-abc123",
  "repo_name": "pviz-repo-abc123"
}
```

---

#### nodes (File/module metadata)

Nodes are stored in **schema-encoded format** with abbreviated field names. The `legend` maps abbreviations to full names.

**Common Node Fields (see `legend` for full names):**

| Abbrev | Full Name | Type | Description |
|--------|-----------|------|-------------|
| `f` | `file` | string | File path |
| `id` | `node_id` | string | Unique node identifier |
| `n` | `name` | string | Module/file name |
| `lang` | `language` | string | Primary language |
| `ext` | `file_ext` | string | File extension |
| `m` | `module_guess` | string | Inferred module path |
| `pkg` | `package` | string | Package name |
| `loc` | `loc` | integer | Lines of code |
| `sloc` | `sloc` | integer | Source lines of code |
| `ps` | `parse_status` | enum | "ok", "error", "partial" (encoded as 0, 1, 2) |
| `ic` | `importers_count` | integer | Files that import this |
| `dc` | `dependencies_count` | integer | Files this imports |
| `imp` | `imports` | array | List of imported modules |
| `pex` | `public_exports` | array | Exported symbols |
| `fn` | `functions` | array | Function names |
| `fnd` | `functions_detailed` | array | Detailed function metadata |
| `cl` | `classes` | array | Class names |
| `cld` | `classes_detailed` | array | Detailed class metadata |
| `g` | `globals` | array | Global variable names |
| `gd` | `globals_detailed` | array | Detailed global metadata |

**Example node (decoded):**
```json
{
  "file": "backend/api/auth.py",
  "node_id": "backend/api/auth.py",
  "name": "auth",
  "language": "python",
  "module_guess": "backend.api.auth",
  "loc": 245,
  "sloc": 198,
  "parse_status": "ok",
  "importers_count": 12,
  "dependencies_count": 5,
  "imports": ["fastapi", "jwt", "passlib"],
  "functions": ["login", "verify_token", "hash_password"],
  "functions_detailed": [...]
}
```

---

#### functions_detailed[] (Function metadata)

Functions are stored with abbreviated keys to save space.

| Abbrev | Full Name | Type | Description |
|--------|-----------|------|-------------|
| `n` | `name` | string | Function name |
| `ln` | `lineno` | integer | Line number where function starts |
| `doc` | `docstring` | string | Function docstring (if present) |
| `rt` | `return_type` | integer | Return type hint (index into `type_strings`) |
| `p` | `parameters` | array | Function parameters |
| `a` | `is_async` | 0/1 | Whether function is async (0=false, 1=true) |
| `g` | `is_generator` | 0/1 | Whether function is generator |
| `d` | `decorators` | array | Decorator names |

**Example function (decoded):**
```json
{
  "name": "verify_token",
  "lineno": 45,
  "docstring": "Verify JWT token and return user ID",
  "return_type": "Optional[str]",
  "parameters": [
    {"name": "token", "type_hint": "str", "kind": "positional_or_keyword"}
  ],
  "is_async": false,
  "is_generator": false
}
```

---

#### parameters[] (Function parameters)

| Abbrev | Full Name | Type | Description |
|--------|-----------|------|-------------|
| `n` | `name` | string | Parameter name |
| `t` | `type_hint` | integer | Type hint (index into `type_strings`) |
| `d` | `default` | string | Default value (if present) |
| `k` | `kind` | integer | Parameter kind (index into `param_kind` enum) |

**Parameter kinds (enum values):**
- `0` = `"positional_or_keyword"` (default)
- `1` = `"positional_only"`
- `2` = `"keyword_only"`
- `3` = `"var_positional"` (*args)
- `4` = `"var_keyword"` (**kwargs)

**Example parameter (decoded):**
```json
{
  "name": "user_id",
  "type_hint": "str",
  "default": null,
  "kind": "positional_or_keyword"
}
```

---

#### type_strings[] (Deduplicated type hints)

Array of unique type hints referenced throughout the artifact. Functions and parameters reference types by index.

**Example:**
```json
{
  "type_strings": [
    "str",
    "int",
    "bool",
    "Optional[str]",
    "Dict[str, Any]",
    "List[str]",
    ...
  ]
}
```

When a function has `"rt": 4`, the return type is `type_strings[4]` = `"Dict[str, Any]"`.

---

#### edges[] (Dependencies)

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | File/module being imported |
| `target` | string | File/module that imports |
| `kind` | string | Edge type ("import", "call", "inheritance", etc.) |
| `source_line` | integer | Line number in source file (if available) |
| `meta` | object | Additional edge metadata |

**Example:**
```json
{
  "source": "backend/api/auth.py",
  "target": "backend/services/user.py",
  "kind": "import",
  "source_line": 5
}
```

---

#### summary (High-level metrics)

| Field | Type | Description |
|-------|------|-------------|
| `total_files` | integer | Number of source files analyzed |
| `total_sloc` | integer | Total source lines of code |
| `total_nodes` | integer | Total nodes in graph |
| `total_edges` | integer | Total dependency edges |
| `languages` | object | File counts per language |
| `top_level_modules` | array | Root-level modules detected |

**Example:**
```json
{
  "total_files": 511,
  "total_sloc": 45230,
  "total_nodes": 511,
  "total_edges": 767,
  "languages": {
    "python": 451,
    "typescript": 54,
    "javascript": 2
  }
}
```

---

#### discovery_manifest (Entry points & tests)

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Discovery schema version |
| `entry_points` | array | Detected entry points (main functions, API servers, CLI tools) |
| `warnings` | array | Analysis warnings (dynamic imports, missing dependencies) |
| `src_roots` | array | Detected source root directories |

**Entry point structure:**
```json
{
  "confidence": 0.95,
  "evidence": ["has_if_dunder_main", "calls_main()"],
  "file": "backend/main.py",
  "id": "backend/main.py:main",
  "kind": "script_main_guard_call_main",
  "priority": 9,
  "source": "python_main_guard",
  "symbol": "main"
}
```

---

### Decoding Compressed Format

To decode the compressed format back to human-readable:

1. **Resolve abbreviated field names** using the `legend`
2. **Decode enum values** using the `enums` mappings
3. **Resolve type hint indices** using `type_strings` array
4. **Reconstruct nested objects** from array format

**Example decoding:**
```python
# Compressed
{"n": "func", "ln": 42, "rt": 5, "a": 1}

# Decoded (using legend and type_strings)
{
  "name": "func",
  "lineno": 42,
  "return_type": "Dict[str, Any]",  # type_strings[5]
  "is_async": true  # 1 = true
}
```

---

### Format Comparison

| Feature | Standard Format | Compressed Format |
|---------|----------------|-------------------|
| **Size** | ~3.5 MB | ~1.5 MB (58% smaller) |
| **Readability** | High (full field names) | Medium (requires legend) |
| **Token efficiency** | Lower | Higher (optimized for LLMs) |
| **Lossless** | Yes | Yes (perfect round-trip) |
| **Fields** | Full names | Abbreviated |
| **Type hints** | Repeated strings | Deduplicated array |
| **Enums** | String values | Integer indices |

Both formats contain identical information. Use **standard** for debugging, **compressed** for LLM consumption.
#### circular_dependencies[]

| Field | Type | Description |
|-------|------|-------------|
| `chain` | array | Ordered list of modules in cycle |
| `length` | integer | Number of modules in cycle |
| `severity` | string | "low", "medium", "high" |

### Example Queries for LLMs

With this schema, LLMs can answer:

**"What are the most coupled modules?"**
```
Sort dependencies by frequency, identify modules with 
highest in-degree + out-degree
```

**"Does this have circular dependencies?"**
```
Check if circular_dependencies[] is non-empty
```

**"How modular is this codebase?"**
```
Check metrics.modularity_score:
- 0.0-0.3: Low modularity
- 0.4-0.7: Medium modularity  
- 0.8-1.0: High modularity
```

**"What languages are used?"**
```
Check metadata.languages
```

### Artifact Size

- Typical artifact: 500 KB - 5 MB
- Large repositories (100K+ SLOC): 10-50 MB
- Artifacts are gzip-compressed for download
- Full dependency graphs available for repositories up to 200K SLOC

---

## Rate Limits

All endpoints are rate-limited to ensure fair usage and system stability.

### Per-Endpoint Limits

| Endpoint | Limit | Window | Header |
|----------|-------|--------|--------|
| POST /estimate/github | 10 requests | 5 minutes | `X-RateLimit-Limit: 10` |
| POST /jobs/github | 20 requests | 1 hour | `X-RateLimit-Limit: 20` |
| GET /jobs/{id}/download-link | 10 requests | 1 minute | `X-RateLimit-Limit: 10` |
| GET /auth/me | 300 requests | 1 minute | `X-RateLimit-Limit: 300` |
| GET /jobs | 60 requests | 1 minute | `X-RateLimit-Limit: 60` |
| GET /jobs/{id} | 120 requests | 1 minute | `X-RateLimit-Limit: 120` |

### Rate Limit Headers

Every response includes rate limit information:

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 20
X-RateLimit-Remaining: 15
X-RateLimit-Reset: 1736601600
```

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests allowed in window |
| `X-RateLimit-Remaining` | Requests remaining in current window |
| `X-RateLimit-Reset` | Unix timestamp when limit resets |

### Handling Rate Limits

When rate limited, the API returns HTTP 429:

```json
{
  "detail": "Rate limit exceeded: 20 job submissions per hour",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 1800,
  "limit": 20,
  "window": "1 hour"
}
```

**Best practices:**

```python
import time
import httpx

def call_api_with_retry(url, headers, json_data, max_retries=3):
    for attempt in range(max_retries):
        response = httpx.post(url, headers=headers, json=json_data)
        
        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 60)
            print(f"Rate limited. Waiting {retry_after} seconds...")
            time.sleep(retry_after)
            continue
            
        return response
    
    raise Exception("Max retries exceeded")
```

### Concurrent Request Limits

- Maximum **5 concurrent requests** per account
- Additional requests queued automatically
- Queue timeout: 30 seconds  

---

## Repository & Language Limits

Languages supported: Python, TypeScript, JavaScript, Java, Go.

---

## Private Repositories

Private GitHub repositories require a Personal Access Token (PAT) for analysis.

### Security Model

**Important:** GitHub tokens are:
- ✅ Transmitted over HTTPS only
- ✅ Used **only** to clone the repository
- ✅ **Never stored** in pviz databases
- ✅ **Never logged** by pviz systems
- ✅ Discarded immediately after cloning
- ❌ Not visible to pviz staff
- ❌ Not included in any artifacts

### Creating a GitHub Personal Access Token

**Step 1:** Navigate to GitHub Settings
```
GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
```

**Step 2:** Generate new token (classic)
- Click "Generate new token (classic)"
- Give it a descriptive name: "pviz-analysis"
- Set expiration: 7-90 days recommended

**Step 3:** Select scopes
- ✅ **repo** (Full control of private repositories)
  - Required to clone private repos
  - Grants read-only access is sufficient in practice
  
**Optional for fine-grained control:**
- Use fine-grained tokens with repository-specific access
- Grant "Contents: Read-only" permission
- Limit to specific repositories only

**Step 4:** Copy token immediately
- Token shown only once
- Store securely (password manager recommended)
- Format: `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### Using Private Repositories

#### Estimate Cost

```bash
curl -X POST https://api.pvizgenerator.com/estimate/github \
  -H "Authorization: Bearer YOUR_PVIZ_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/myorg/private-repo",
    "github_token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  }'
```

#### Submit Analysis

```bash
curl -X POST https://api.pvizgenerator.com/jobs/github \
  -H "Authorization: Bearer YOUR_PVIZ_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/myorg/private-repo",
    "github_token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "pricing_choice": "tokens"
  }'
```

### Error Handling

**Without token (private repo):**

```json
{
  "success": false,
  "error": "private_repository",
  "message": "This repository is private. Please provide a GitHub Personal Access Token with 'repo' scope.",
  "requires_github_token": true
}
```

**Invalid token:**

```json
{
  "detail": "GitHub authentication failed. Token may be invalid or expired.",
  "error_code": "GITHUB_AUTH_FAILED"
}
```

**Insufficient permissions:**

```json
{
  "detail": "GitHub token lacks required permissions. Grant 'repo' scope.",
  "error_code": "GITHUB_INSUFFICIENT_PERMISSIONS"
}
```

### Best Practices

1. **Use fine-grained tokens** when possible for better security
2. **Set token expiration** to limit exposure window
3. **Rotate tokens** every 30-90 days
4. **Revoke immediately** if compromised
5. **Never commit tokens** to version control
6. **Use environment variables** or secrets managers

### Token Scopes Reference

| Repository Type | Required Scope | Access Level |
|----------------|----------------|--------------|
| Public | None | No token needed |
| Private (your repos) | `repo` | Full control |
| Private (org repos) | `repo` | Must be org member |
| Fine-grained (recommended) | `Contents: Read` | Repository-specific |

### Frequently Asked Questions

**Q: Can pviz staff see my code?**  
A: No. Analysis happens in isolated containers that are destroyed after completion. Code is never stored or logged.

**Q: What if my token expires during analysis?**  
A: The token is only used during the initial clone (first 10-30 seconds). Expiration afterward doesn't affect the analysis.

**Q: Can I analyze repositories from GitHub Enterprise?**  
A: Not currently supported. Contact support for enterprise deployment options.

**Q: Does pviz support other Git hosts?**  
A: Currently only GitHub.com is supported. GitLab and Bitbucket support is planned.

---

## Webhooks

**Status:** Not currently available

Webhooks for real-time job status updates are not currently under development.

### Current Workaround

Until webhooks are available, use polling to check job status:

```python
import time
import httpx

def wait_for_completion(job_id, api_url, token, timeout=600):
    """
    Poll job status until completion or timeout.
    
    Args:
        job_id: Job ID to monitor
        api_url: API base URL
        token: JWT authentication token
        timeout: Maximum wait time in seconds
    
    Returns:
        Final job status dict
    """
    start_time = time.time()
    poll_interval = 5
    
    while time.time() - start_time < timeout:
        response = httpx.get(
            f"{api_url}/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        job = response.json()
        
        # Terminal states
        if job["status"] in ["completed", "failed", "canceled"]:
            return job
        
        # Increase polling interval after 1 minute
        if time.time() - start_time > 60:
            poll_interval = 10
            
        time.sleep(poll_interval)
    
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
```

### Interested in Webhooks?

If webhooks are critical for your use case, contact support to:
- Provide feedback on webhook payload design
- Request specific webhook events

Email: mikemc@pvizgenerator.com with subject "Webhook Early Access"

## Documentation Status

Still under development as of this release. Feedback on issues is much appreciated.
