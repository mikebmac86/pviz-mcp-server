# MCP Tools Reference

Complete documentation for all pviz MCP server tools.

---

## Table of Contents

1. [Account Management](#account-management)
2. [Analysis Submission](#analysis-submission)
3. [Job Management](#job-management)
4. [Results Retrieval](#results-retrieval)
5. [Analysis Queries](#analysis-queries)
6. [Tool Usage Examples](#tool-usage-examples)

---

## Account Management

### check_account_balance

Returns account information including email, plan type, and current token balance.

**Parameters:** None

**Returns:**
```json
{
  "email": "user@example.com",
  "plan": "pro",
  "balance": 1500,
  "trial_active": false,
  "trial_credits": 0
}
```

**Example usage:**
```
Check my pviz account balance
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "check_account_balance",
    "arguments": {}
  }
}
```

---

## Analysis Submission

### estimate_analysis_cost

Estimates the token cost and analysis coverage for a repository before submitting.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo_url` | string | Yes | GitHub repository URL or "owner/repo" |
| `github_token` | string | No | GitHub PAT for private repos |

**Returns:**
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

**Error responses:**

Private repository without token:
```json
{
  "success": false,
  "error": "private_repository",
  "message": "This repository is private. Please provide a GitHub Personal Access Token.",
  "requires_github_token": true
}
```

**Example usage:**
```
Estimate the cost for analyzing django/django
How much would it cost to analyze https://github.com/facebook/react?
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "estimate_analysis_cost",
    "arguments": {
      "repo_url": "https://github.com/django/django"
    }
  }
}
```

---

### analyze_repository

Submits a repository for dependency analysis. Can optionally wait for completion.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `repo_url` | string | Yes | - | GitHub repository URL or "owner/repo" |
| `wait_for_completion` | boolean | No | true | Wait for analysis to complete |
| `include_full_graph` | boolean | No | false | Include complete dependency graph |
| `pricing_choice` | string | No | "tokens" | Payment method: "tokens" or "trial_credit" |
| `questions` | array | No | null | Optional analysis questions |
| `github_token` | string | No | null | GitHub PAT for private repos |

**Returns (when wait_for_completion=true):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "completed",
  "repo_url": "https://github.com/django/django",
  "artifact": {
    "metadata": { ... },
    "dependencies": [ ... ],
    "metrics": { ... },
    "circular_dependencies": [ ... ]
  }
}
```

**Returns (when wait_for_completion=false):**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "queued_precheck",
  "message": "Analysis job submitted. Use get_analysis_status to check progress."
}
```

**Example usage:**
```
Analyze https://github.com/django/django
Analyze facebook/react and tell me about its architecture
Analyze myorg/private-repo with github token ghp_xxxxx
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "analyze_repository",
    "arguments": {
      "repo_url": "https://github.com/django/django",
      "wait_for_completion": true,
      "include_full_graph": false
    }
  }
}
```

---

## Job Management

### get_job_history

Returns recent analysis jobs with pagination support.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | integer | No | 10 | Number of jobs to return (1-50) |
| `skip` | integer | No | 0 | Number of jobs to skip (pagination) |

**Returns:**
```json
{
  "total": 42,
  "jobs": [
    {
      "job_id": "job_a1b2c3d4e5f6",
      "status": "completed",
      "repo_url": "https://github.com/django/django",
      "created_at": "2026-01-11T12:00:00Z",
      "completed_at": "2026-01-11T12:03:45Z",
      "tokens_used": 250
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

**Example usage:**
```
Show my recent pviz analyses
Show my last 20 analysis jobs
What repositories have I analyzed?
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "get_job_history",
    "arguments": {
      "limit": 10,
      "skip": 0
    }
  }
}
```

---

### get_analysis_status

Checks the current status of a running or completed analysis job.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string | Yes | Job ID to check |

**Returns:**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "running",
  "repo_url": "https://github.com/django/django",
  "progress": 45,
  "created_at": "2026-01-11T12:00:00Z",
  "started_at": "2026-01-11T12:00:15Z",
  "estimated_completion": "2026-01-11T12:03:00Z"
}
```

**Possible statuses:**
- `queued_precheck` - Job queued, awaiting validation
- `running` - Analysis in progress
- `completed` - Analysis finished successfully
- `failed` - Analysis encountered an error
- `canceled` - Job was canceled by user

**Example usage:**
```
Check status of job job_a1b2c3d4e5f6
Is my analysis done yet?
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "get_analysis_status",
    "arguments": {
      "job_id": "job_a1b2c3d4e5f6"
    }
  }
}
```

---

## Results Retrieval

### retrieve_past_result

Downloads and returns the complete analysis artifact from a completed job.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `job_id` | string | Yes | - | Job ID to retrieve |
| `include_full_graph` | boolean | No | true | Include complete dependency graph |

**Returns:**
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "status": "completed",
  "repo_url": "https://github.com/django/django",
  "artifact": {
    "metadata": {
      "repo_url": "https://github.com/django/django",
      "commit_sha": "abc123...",
      "analyzed_at": "2026-01-11T12:03:45Z",
      "languages": ["Python"],
      "total_files": 1543,
      "total_sloc": 125000
    },
    "dependencies": [
      {
        "source": "django.core.handlers",
        "target": "django.http",
        "import_type": "module"
      }
    ],
    "metrics": {
      "total_files": 1543,
      "total_dependencies": 8234,
      "modularity_score": 0.78,
      "coupling_score": 0.32
    },
    "circular_dependencies": []
  }
}
```

**Errors:**

Job not found:
```json
{
  "error": "Job not found: job_invalid123"
}
```

Job not completed:
```json
{
  "error": "Job not completed yet. Current status: running"
}
```

**Example usage:**
```
Get the results from job job_a1b2c3d4e5f6
Show me the analysis from my django job
Retrieve the full dependency graph for job_xyz789
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "retrieve_past_result",
    "arguments": {
      "job_id": "job_a1b2c3d4e5f6",
      "include_full_graph": true
    }
  }
}
```

---

## Analysis Queries

### get_circular_dependencies

Detects and returns circular dependency chains from an analysis artifact.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `artifact` | object | Yes | Analysis artifact from analyze_repository or retrieve_past_result |

**Returns:**
```json
{
  "has_circular_dependencies": true,
  "total_cycles": 3,
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
  ]
}
```

**Example usage:**
```
Does django have any circular dependencies?
Check for circular dependencies in this analysis
Show me dependency cycles
```

**Note:** This tool processes an artifact, not a repository. Use `analyze_repository` first.

---

### get_repository_metrics

Extracts high-level architectural metrics from an analysis artifact.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `artifact` | object | Yes | Analysis artifact |

**Returns:**
```json
{
  "total_files": 1543,
  "total_sloc": 125000,
  "total_dependencies": 8234,
  "avg_dependencies_per_file": 5.3,
  "max_dependencies_in_file": 45,
  "modularity_score": 0.78,
  "coupling_score": 0.32,
  "languages": ["Python"],
  "main_packages": ["django.db", "django.core"]
}
```

**Metrics explanation:**

- **modularity_score** (0-1): Higher = more modular architecture
  - 0.0-0.3: Low modularity (monolithic)
  - 0.4-0.7: Medium modularity
  - 0.8-1.0: High modularity (well-separated concerns)

- **coupling_score** (0-1): Lower = less tightly coupled
  - 0.0-0.3: Low coupling (good)
  - 0.4-0.7: Medium coupling
  - 0.8-1.0: High coupling (tightly coupled)

**Example usage:**
```
What are the metrics for this codebase?
How modular is django?
Show me architectural statistics
```

---

### compare_repositories

Compares two repository analyses structurally.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `repo_url_1` | string | Yes | First repository URL |
| `repo_url_2` | string | Yes | Second repository URL |

**Returns:**
```json
{
  "comparison": {
    "repo_1": {
      "url": "https://github.com/django/django",
      "sloc": 125000,
      "files": 1543,
      "modularity": 0.78
    },
    "repo_2": {
      "url": "https://github.com/flask/flask",
      "sloc": 15000,
      "files": 234,
      "modularity": 0.65
    },
    "differences": {
      "size_ratio": 8.3,
      "modularity_difference": 0.13,
      "complexity_comparison": "Django is significantly larger and more modular"
    }
  }
}
```

**Example usage:**
```
Compare django and flask architectures
How does react compare to vue?
```

**Note:** This tool will analyze both repositories if not already analyzed, consuming tokens for each.

---

### download_dependency_graph

Downloads the complete dependency graph JSON from a completed analysis.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string | Yes | Job ID with completed analysis |

**Returns:**
```json
{
  "download_url": "https://pviz-artifacts.s3.amazonaws.com/...",
  "expires_at": "2026-01-11T13:00:00Z",
  "file_size_bytes": 4567890,
  "format": "application/json"
}
```

**Note:** Download URLs expire after 1 hour. Request a new URL if expired.

**Example usage:**
```
Download the full dependency graph for job_a1b2c3d4e5f6
Get the JSON artifact for my last analysis
```

**MCP call:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "download_dependency_graph",
    "arguments": {
      "job_id": "job_a1b2c3d4e5f6"
    }
  }
}
```

---

## Tool Usage Examples

### Complete Analysis Workflow

```
User: "Analyze django and tell me about its architecture"

1. LLM calls: estimate_analysis_cost("django/django")
   → Returns: {tokens_needed: 250, can_afford: true}

2. LLM calls: analyze_repository("django/django", wait_for_completion=true)
   → Returns: {status: "completed", artifact: {...}}

3. LLM calls: get_repository_metrics(artifact)
   → Returns: {modularity_score: 0.78, ...}

4. LLM calls: get_circular_dependencies(artifact)
   → Returns: {has_circular_dependencies: false}

5. LLM responds: "Django has 125K SLOC across 1,543 files..."
```

### Checking Analysis History

```
User: "What repositories have I analyzed recently?"

1. LLM calls: get_job_history(limit=10)
   → Returns: {total: 42, jobs: [...]}

2. LLM responds: "You've analyzed 42 repositories total. 
   Your most recent analyses include: django, flask, fastapi..."
```

### Private Repository Analysis

```
User: "Analyze my private repo myorg/secret-project"

1. LLM calls: estimate_analysis_cost("myorg/secret-project")
   → Returns: {error: "private_repository", requires_github_token: true}

2. LLM asks: "This is a private repository. Please provide a 
   GitHub Personal Access Token with 'repo' scope."

3. User: "Use token ghp_xxxxx"

4. LLM calls: analyze_repository("myorg/secret-project", 
               github_token="ghp_xxxxx")
   → Analysis proceeds normally
```

### Retrieving Past Analysis

```
User: "Show me the circular dependencies from my django analysis"

1. LLM calls: get_job_history(limit=20)
   → Finds: {job_id: "job_abc123", repo_url: "django/django"}

2. LLM calls: retrieve_past_result("job_abc123")
   → Returns: {artifact: {...}}

3. LLM calls: get_circular_dependencies(artifact)
   → Returns: {has_circular_dependencies: false}

4. LLM responds: "Django has no circular dependencies."
```

---

## Error Handling

All tools may return errors in this format:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description",
  "details": { /* additional context */ }
}
```

### Common Errors

**Insufficient tokens:**
```json
{
  "error": "INSUFFICIENT_TOKENS",
  "message": "Need 250 tokens, you have 42",
  "required": 250,
  "available": 42
}
```

**Invalid repository:**
```json
{
  "error": "REPOSITORY_NOT_FOUND",
  "message": "Repository does not exist or is not accessible"
}
```

**Rate limited:**
```json
{
  "error": "RATE_LIMIT_EXCEEDED",
  "message": "Too many requests. Retry after 300 seconds",
  "retry_after": 300
}
```

---

## Tool Limitations

### Repository Size Limits

- Maximum SLOC: 500,000 lines
- Maximum files: 10,000 files
- Timeout: 30 minutes per analysis

### Supported Languages

- Python (`.py`)
- TypeScript (`.ts`, `.tsx`)
- JavaScript (`.js`, `.jsx`)
- Java (`.java`)
- Go (`.go`)

### Unsupported Features

- Monorepos with multiple languages (analyzes primary language only)
- Dynamic imports (analyzed statically)
- Non-GitHub repositories
- Archived/deleted repositories

---

## Best Practices

1. **Always estimate first:** Use `estimate_analysis_cost` before submitting large analyses
2. **Check balance:** Use `check_account_balance` periodically
3. **Handle private repos:** Prompt users for GitHub tokens when needed
4. **Cache results:** Use `retrieve_past_result` instead of re-analyzing
5. **Respect rate limits:** Implement exponential backoff
6. **Provide context:** Help users understand metrics and scores

---

For API-level details, see [API_ENDPOINTS.md](API_ENDPOINTS.md).
