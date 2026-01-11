# pviz MCP Server - Usage Examples

Real-world conversation examples showing how LLMs interact with the pviz MCP server.

---

## Table of Contents

1. [Basic Analysis](#basic-analysis)
2. [Private Repository Analysis](#private-repository-analysis)
3. [Architectural Comparison](#architectural-comparison)
4. [Circular Dependency Detection](#circular-dependency-detection)
5. [Historical Analysis Review](#historical-analysis-review)
6. [Cost Estimation](#cost-estimation)
7. [Error Recovery](#error-recovery)
8. [Advanced Queries](#advanced-queries)

---

## Basic Analysis

### Example 1: Simple Repository Analysis

**User:**
```
Analyze the Django repository and tell me about its architecture
```

**LLM Tool Calls:**

1. `estimate_analysis_cost`
```json
{
  "repo_url": "https://github.com/django/django"
}
```

**Response:**
```json
{
  "tokens_needed": 250,
  "sloc": 125000,
  "file_count": 1543,
  "can_afford": true,
  "estimated_duration_seconds": 180
}
```

2. `analyze_repository`
```json
{
  "repo_url": "https://github.com/django/django",
  "wait_for_completion": true,
  "include_full_graph": false
}
```

**Response:**
```json
{
  "job_id": "job_abc123",
  "status": "completed",
  "artifact": {
    "metadata": {
      "total_files": 1543,
      "total_sloc": 125000,
      "languages": ["Python"]
    },
    "metrics": {
      "modularity_score": 0.78,
      "coupling_score": 0.32,
      "total_dependencies": 8234
    }
  }
}
```

**LLM Response:**

> I've analyzed the Django repository. Here's what I found:
> 
> **Architecture Overview:**
> - **Size:** 125,000 lines of Python code across 1,543 files
> - **Modularity:** 0.78/1.0 (high modularity - well-organized into separate concerns)
> - **Coupling:** 0.32/1.0 (low coupling - components are loosely connected)
> - **Dependencies:** 8,234 import relationships
> 
> Django demonstrates a highly modular architecture with well-separated concerns. The main packages (`django.db`, `django.core`, `django.contrib`) handle distinct responsibilities, making the codebase maintainable despite its large size. The low coupling score indicates that changes to one module are less likely to require changes elsewhere.
>
> This analysis used 250 tokens from your account.

---

## Private Repository Analysis

### Example 2: Analyzing Private Code

**User:**
```
Can you analyze my company's private repository at github.com/mycompany/internal-api?
```

**LLM Tool Calls:**

1. `estimate_analysis_cost`
```json
{
  "repo_url": "https://github.com/mycompany/internal-api"
}
```

**Response:**
```json
{
  "success": false,
  "error": "private_repository",
  "message": "This repository is private. Please provide a GitHub Personal Access Token.",
  "requires_github_token": true
}
```

**LLM Response:**

> This is a private repository, so I'll need a GitHub Personal Access Token to analyze it.
> 
> To create one:
> 1. Go to GitHub Settings → Developer settings → Personal access tokens
> 2. Generate a new token (classic)
> 3. Select the `repo` scope (full control of private repositories)
> 4. Copy the token (it starts with `ghp_`)
> 
> Once you have the token, please provide it and I'll proceed with the analysis. Note that the token is only used to clone the repository and is never stored by pviz.

**User:**
```
Here's the token: ghp_abc123xyz789def456ghi012jkl345mno678
```

**LLM Tool Calls:**

2. `analyze_repository`
```json
{
  "repo_url": "https://github.com/mycompany/internal-api",
  "github_token": "ghp_abc123xyz789def456ghi012jkl345mno678",
  "wait_for_completion": true
}
```

**Response:**
```json
{
  "job_id": "job_xyz789",
  "status": "completed",
  "artifact": { /* analysis results */ }
}
```

**LLM Response:**

> ✅ Successfully analyzed your private repository.
> 
> The analysis shows your internal API has 42,000 lines of TypeScript across 324 files...

---

## Architectural Comparison

### Example 3: Comparing Frameworks

**User:**
```
Compare the architectural complexity of Flask vs Django. Which one is more modular?
```

**LLM Tool Calls:**

1. `analyze_repository` (Flask)
```json
{
  "repo_url": "https://github.com/pallets/flask",
  "wait_for_completion": true
}
```

2. `analyze_repository` (Django)
```json
{
  "repo_url": "https://github.com/django/django",
  "wait_for_completion": true
}
```

**Responses:**

Flask artifact:
```json
{
  "metadata": {
    "total_sloc": 15000,
    "total_files": 234
  },
  "metrics": {
    "modularity_score": 0.65,
    "coupling_score": 0.28
  }
}
```

Django artifact:
```json
{
  "metadata": {
    "total_sloc": 125000,
    "total_files": 1543
  },
  "metrics": {
    "modularity_score": 0.78,
    "coupling_score": 0.32
  }
}
```

**LLM Response:**

> I've analyzed both Flask and Django. Here's the comparison:
> 
> | Metric | Flask | Django | Winner |
> |--------|-------|--------|--------|
> | Size (SLOC) | 15,000 | 125,000 | Flask (smaller) |
> | Files | 234 | 1,543 | Flask (fewer) |
> | Modularity | 0.65 | 0.78 | **Django** |
> | Coupling | 0.28 | 0.32 | Flask (lower) |
> 
> **Key Findings:**
> 
> 1. **Django is more modular** (0.78 vs 0.65) - better separation of concerns
> 2. **Flask is smaller** - 8.3x fewer lines of code
> 3. **Flask has slightly lower coupling** (0.28 vs 0.32)
> 
> **Interpretation:** Django's higher modularity reflects its "batteries included" philosophy - many separate, well-defined components (ORM, admin, auth). Flask's lower coupling reflects its microframework design - fewer interdependencies between components.
> 
> For a large application, Django's modularity might be beneficial. For a small service, Flask's simplicity could be preferable.
>
> This comparison used 400 tokens (250 for Django, 150 for Flask).

---

## Circular Dependency Detection

### Example 4: Finding Dependency Cycles

**User:**
```
Does the FastAPI repository have any circular dependencies?
```

**LLM Tool Calls:**

1. `analyze_repository`
```json
{
  "repo_url": "https://github.com/tiangolo/fastapi",
  "wait_for_completion": true
}
```

**Response:**
```json
{
  "artifact": {
    "circular_dependencies": [
      {
        "chain": [
          "fastapi.routing",
          "fastapi.dependencies.utils",
          "fastapi.dependencies.models",
          "fastapi.routing"
        ],
        "length": 4,
        "severity": "medium"
      }
    ]
  }
}
```

**LLM Response:**

> Yes, FastAPI has 1 circular dependency:
> 
> **Cycle detected:**
> ```
> fastapi.routing
>   → fastapi.dependencies.utils
>     → fastapi.dependencies.models
>       → fastapi.routing (cycles back)
> ```
> 
> **Details:**
> - **Length:** 4 modules
> - **Severity:** Medium
> 
> This circular dependency involves the routing and dependency injection systems. While this doesn't necessarily indicate a problem (it's common in frameworks with tightly integrated features), it does mean these modules cannot be used independently.
> 
> **Recommendation:** If you're using FastAPI, this shouldn't affect your application. If you're studying the codebase, you might want to understand why these modules are interdependent before modifying them.

---

## Historical Analysis Review

### Example 5: Reviewing Past Analyses

**User:**
```
What repositories have I analyzed in the past week?
```

**LLM Tool Calls:**

1. `get_job_history`
```json
{
  "limit": 20,
  "skip": 0
}
```

**Response:**
```json
{
  "total": 42,
  "jobs": [
    {
      "job_id": "job_001",
      "repo_url": "https://github.com/django/django",
      "status": "completed",
      "created_at": "2026-01-10T14:30:00Z",
      "tokens_used": 250
    },
    {
      "job_id": "job_002",
      "repo_url": "https://github.com/pallets/flask",
      "status": "completed",
      "created_at": "2026-01-09T10:15:00Z",
      "tokens_used": 150
    },
    {
      "job_id": "job_003",
      "repo_url": "https://github.com/fastapi/fastapi",
      "status": "failed",
      "created_at": "2026-01-08T16:45:00Z",
      "error": "Rate limit exceeded"
    }
  ]
}
```

**LLM Response:**

> You've analyzed 42 repositories total. Here are your most recent analyses:
> 
> **Last 7 days:**
> 
> 1. **Django** - Jan 10, 2:30 PM
>    - Status: ✅ Completed
>    - Tokens: 250
> 
> 2. **Flask** - Jan 9, 10:15 AM
>    - Status: ✅ Completed
>    - Tokens: 150
> 
> 3. **FastAPI** - Jan 8, 4:45 PM
>    - Status: ❌ Failed (rate limit exceeded)
>    - Tokens: 0
> 
> Would you like me to retrieve the results from any of these analyses?

**User:**
```
Yes, show me the Django analysis
```

**LLM Tool Calls:**

2. `retrieve_past_result`
```json
{
  "job_id": "job_001",
  "include_full_graph": false
}
```

**Response:** *(Returns full Django artifact)*

**LLM Response:**

> Here's your Django analysis from January 10:
> 
> - **Size:** 125,000 SLOC, 1,543 files
> - **Modularity:** 0.78 (high)
> - **Coupling:** 0.32 (low)
> - **Circular Dependencies:** None
> 
> The analysis is still available and didn't consume any additional tokens to retrieve.

---

## Cost Estimation

### Example 6: Budget-Conscious Analysis

**User:**
```
I want to analyze tensorflow/tensorflow but I'm not sure I have enough tokens. Can you check first?
```

**LLM Tool Calls:**

1. `check_account_balance`
```json
{}
```

**Response:**
```json
{
  "email": "user@example.com",
  "plan": "free",
  "balance": 500,
  "trial_active": false
}
```

2. `estimate_analysis_cost`
```json
{
  "repo_url": "https://github.com/tensorflow/tensorflow"
}
```

**Response:**
```json
{
  "tokens_needed": 1200,
  "sloc": 600000,
  "file_count": 8934,
  "can_afford": false,
  "supported_languages": ["Python", "C++"],
  "estimated_duration_seconds": 900
}
```

**LLM Response:**

> I've checked your account and the cost for TensorFlow:
> 
> **Your Balance:** 500 tokens
> **Required:** 1,200 tokens
> **Shortfall:** 700 tokens
> 
> You don't currently have enough tokens to analyze TensorFlow. The repository is quite large (600K lines across ~9,000 files), which is why it requires more tokens.
> 
> **Options:**
> 
> 1. **Purchase more tokens** - You'd need at least 700 more tokens
> 2. **Analyze a smaller repository** - Consider analyzing a TensorFlow sub-project or a different repository
> 3. **Wait for token refresh** - If you're on a plan with monthly credits
> 
> Would you like me to suggest some smaller, related repositories to analyze instead?

---

## Error Recovery

### Example 7: Handling Analysis Failures

**User:**
```
Analyze https://github.com/invalid/does-not-exist
```

**LLM Tool Calls:**

1. `analyze_repository`
```json
{
  "repo_url": "https://github.com/invalid/does-not-exist",
  "wait_for_completion": true
}
```

**Response:**
```json
{
  "job_id": "job_failed_001",
  "status": "failed",
  "error": "Repository not found or not accessible"
}
```

**LLM Response:**

> ❌ The analysis failed because this repository doesn't exist or isn't accessible.
> 
> **Possible reasons:**
> 1. The repository name is misspelled
> 2. The repository has been deleted or made private
> 3. You don't have access permissions (if private)
> 
> Please double-check the repository URL. If it's a private repository, you'll need to provide a GitHub Personal Access Token.

---

## Advanced Queries

### Example 8: Multi-Step Analysis

**User:**
```
I'm deciding between using Express.js or Fastify for my new Node.js API. Can you analyze both and tell me which has better architectural quality?
```

**LLM Tool Calls:**

1-2. *(Analyzes both repositories)*

**LLM Response:**

> I've analyzed both Express.js and Fastify. Here's a comprehensive comparison:
> 
> **Express.js:**
> - Size: 6,800 SLOC
> - Modularity: 0.52
> - Coupling: 0.45
> - Circular Dependencies: 2 cycles
> - Age: Mature (10+ years)
> 
> **Fastify:**
> - Size: 8,200 SLOC
> - Modularity: 0.71
> - Coupling: 0.28
> - Circular Dependencies: 0 cycles
> - Age: Newer (5+ years)
> 
> **Architectural Quality Winner: Fastify**
> 
> Fastify demonstrates superior architectural quality:
> - Higher modularity (0.71 vs 0.52) means better separation of concerns
> - Lower coupling (0.28 vs 0.45) means more independent components
> - No circular dependencies vs 2 in Express
> 
> However, consider these factors beyond architecture:
> - **Express** has a much larger ecosystem and community
> - **Fastify** is designed for higher performance
> - **Express** has more third-party middleware available
> 
> For architectural quality alone, Fastify is the clear winner. But your decision should also weigh ecosystem maturity and your team's familiarity.

---

### Example 9: Deep Dive Analysis

**User:**
```
Analyze Next.js and give me a detailed breakdown of its main architectural components and how they interact
```

**LLM Tool Calls:**

1. `analyze_repository`
```json
{
  "repo_url": "https://github.com/vercel/next.js",
  "wait_for_completion": true,
  "include_full_graph": true
}
```

**Response:** *(Returns full artifact with dependency graph)*

**LLM Response:**

> I've completed a detailed analysis of Next.js. Here's the architectural breakdown:
> 
> **Core Components:**
> 
> 1. **Server Runtime** (`packages/next/server`)
>    - Handles SSR and API routes
>    - 15,234 SLOC
>    - Dependencies: 234 internal modules
> 
> 2. **Client Runtime** (`packages/next/client`)
>    - Browser-side functionality
>    - 8,456 SLOC
>    - Dependencies: 156 internal modules
> 
> 3. **Build System** (`packages/next/build`)
>    - Webpack integration and optimization
>    - 12,890 SLOC
>    - Dependencies: 189 internal modules
> 
> 4. **Routing** (`packages/next/shared/lib/router`)
>    - File-based routing logic
>    - 4,567 SLOC
>    - Central hub: 45 modules depend on it
> 
> **Component Interactions:**
> 
> ```
> Build System
>     ↓
> Server Runtime ←→ Client Runtime
>     ↓                 ↓
>   Routing  ←---------
> ```
> 
> **Key Findings:**
> - The routing module is the most central component (highest in-degree)
> - Server and client runtimes have bidirectional dependencies (for SSR)
> - Build system is upstream from both runtimes
> 
> **Modularity Score:** 0.73 - Good separation between build-time and runtime concerns
> 
> This architecture reflects Next.js's hybrid rendering model - the tight integration between server and client enables SSR while maintaining clear build-time boundaries.

---

## Workflow Patterns

### Pattern 1: Estimate → Confirm → Analyze

```
User: "Analyze X"
LLM:  estimate_analysis_cost(X)
LLM:  "This will cost N tokens. Proceed? (You have M tokens)"
User: "Yes"
LLM:  analyze_repository(X)
```

### Pattern 2: Analyze → Deep Dive

```
User: "Analyze X"
LLM:  analyze_repository(X)
LLM:  *Provides summary*
User: "Tell me more about circular dependencies"
LLM:  get_circular_dependencies(artifact)
LLM:  *Detailed circular dependency report*
```

### Pattern 3: History → Retrieve

```
User: "What did I analyze last week?"
LLM:  get_job_history(limit=20)
LLM:  *Shows list*
User: "Show me the Django one"
LLM:  retrieve_past_result(job_id)
LLM:  *Full analysis results*
```

---

For complete tool documentation, see [TOOLS_REFERENCE.md](TOOLS_REFERENCE.md).
