# pviz MCP Server - Architecture

System architecture, data flow, and component interaction documentation.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Component Architecture](#component-architecture)
3. [Data Flow](#data-flow)
4. [Authentication Flow](#authentication-flow)
5. [Analysis Workflow](#analysis-workflow)
6. [Storage Architecture](#storage-architecture)
7. [Network Architecture](#network-architecture)
8. [Deployment Modes](#deployment-modes)

---

## System Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         LLM Client                          │
│                   (Claude / GPT / etc.)                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ JSON-RPC 2.0 (MCP Protocol)
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    MCP Server Layer                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  pviz_mcp_server.py (STDIO mode)                     │  │
│  │  OR                                                   │  │
│  │  pviz_mcp_http.py (HTTP mode)                        │  │
│  └──────────────────────┬───────────────────────────────┘  │
└─────────────────────────┼──────────────────────────────────┘
                          │
                          │ Tool Execution
                          │
┌─────────────────────────▼──────────────────────────────────┐
│                   API Adapter Layer                         │
│                  (api_adapter.py)                          │
│  ┌────────────────────────────────────────────────────┐   │
│  │ • JWT Authentication                                │   │
│  │ • Request Building                                  │   │
│  │ • Response Parsing                                  │   │
│  │ • Error Handling                                    │   │
│  │ • Retry Logic                                       │   │
│  └────────────────────┬───────────────────────────────┘   │
└─────────────────────────┼──────────────────────────────────┘
                          │
                          │ HTTPS + JWT
                          │
┌─────────────────────────▼──────────────────────────────────┐
│              pviz Backend API (FastAPI)                     │
│                api.pvizgenerator.com                        │
│  ┌────────────────────────────────────────────────────┐   │
│  │ • Authentication                                    │   │
│  │ • Job Queue Management                              │   │
│  │ • Token Billing                                     │   │
│  │ • Repository Validation                             │   │
│  └────────────────────┬───────────────────────────────┘   │
└─────────────────────────┼──────────────────────────────────┘
                          │
           ┌──────────────┼──────────────┐
           │              │              │
           ▼              ▼              ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │  Worker  │   │ Worker   │   │ Worker   │
   │  (Core)  │   │  (LLM)   │   │ (Export) │
   └────┬─────┘   └────┬─────┘   └────┬─────┘
        │              │              │
        └──────────────┼──────────────┘
                       │
                       │ Presigned URLs
                       │
              ┌────────▼────────┐
              │   Amazon S3     │
              │   (Artifacts)   │
              └─────────────────┘
```

---

## Component Architecture

### MCP Server Components

#### 1. MCP Server (pviz_mcp_server.py)

**Purpose:** STDIO-based MCP protocol handler for local integration

**Responsibilities:**
- Listen on stdin/stdout for JSON-RPC messages
- Route tool calls to appropriate handlers
- Manage conversation state
- Format responses per MCP spec

**Technology:**
- Python 3.8+
- `mcp` SDK (Anthropic)
- `asyncio` for async operations

**Entry point:**
```python
if __name__ == "__main__":
    asyncio.run(mcp.server.stdio.stdio_server(app))
```

---

#### 2. HTTP MCP Server (pviz_mcp_http.py)

**Purpose:** HTTP-based MCP endpoint for cloud deployment

**Responsibilities:**
- Expose `/mcp` POST endpoint
- Accept JSON-RPC over HTTP
- Handle per-request authentication
- CORS configuration

**Technology:**
- FastAPI
- Uvicorn (ASGI server)
- Starlette middleware

**Endpoints:**
- `POST /mcp` - MCP JSON-RPC endpoint
- `GET /health` - Health check

---

#### 3. API Adapter (api_adapter.py)

**Purpose:** Abstraction layer for pviz backend API

**Responsibilities:**
- JWT token management
- HTTP request construction
- Response validation
- Error mapping
- Retry logic with exponential backoff
- Polling for job completion

**Key Functions:**
```python
submit_analysis(repo_url, github_token=None, questions=None)
get_job_status(job_id)
get_account_info()
get_token_balance()
estimate_cost(repo_url, github_token=None)
get_job_history(limit=10, skip=0)
retrieve_result(job_id, include_full_graph=True)
```

---

### Backend Components

#### 4. API Layer (FastAPI)

**Endpoints:**
- `GET /auth/me` - Account info
- `GET /tokens/overview` - Balance check
- `POST /estimate/github` - Cost estimation
- `POST /jobs/github` - Job submission
- `GET /jobs/{id}` - Status check
- `GET /jobs/{id}/artifact-link` - Artifact download
- `GET /jobs` - Job history

**Authentication:**
- JWT bearer tokens
- Per-request validation
- Token never expires (account-based)

---

#### 5. Worker Processes

**Worker Types:**

1. **Core Worker**
   - Repository cloning
   - Language detection
   - Dependency extraction
   - Graph construction

2. **LLM Worker**
   - Architecture summarization
   - Question answering
   - Pattern detection

3. **Export Worker**
   - Artifact generation
   - S3 upload
   - Metadata finalization

**Queue:** Redis-based job queue

---

#### 6. Storage Layer (S3)

**Bucket:** `pviz-artifacts`

**Structure:**
```
pviz-artifacts/
├── job_abc123/
│   ├── artifact.json         # Main analysis artifact
│   ├── metadata.json         # Job metadata
│   └── full_graph.json       # Complete dependency graph
└── job_xyz789/
    └── ...
```

**Access:**
- Presigned URLs (1-hour expiration)
- Generated on-demand via `/jobs/{id}/artifact-link`
- No direct public access

---

## Data Flow

### Analysis Submission Flow

```
┌──────┐                                    ┌──────┐
│ LLM  │                                    │ API  │
└───┬──┘                                    └───┬──┘
    │                                           │
    │ 1. analyze_repository(repo_url)           │
    ├──────────────────────────────────────────>│
    │                                           │
    │                                           │ 2. Validate JWT
    │                                           │
    │                                           │ 3. Check balance
    │                                           │
    │                                           │ 4. Clone repository
    │                                           │
    │                                           │ 5. Enqueue job
    │                                           │
    │ 6. {job_id, status: "queued"}             │
    │<──────────────────────────────────────────┤
    │                                           │
    │ 7. Poll: get_job_status(job_id)           │
    ├──────────────────────────────────────────>│
    │                                           │
    │ 8. {status: "running"}                    │
    │<──────────────────────────────────────────┤
    │                                           │
    │ ... (repeat polling) ...                  │
    │                                           │
    │ 9. Poll: get_job_status(job_id)           │
    ├──────────────────────────────────────────>│
    │                                           │
    │ 10. {status: "completed"}                 │
    │<──────────────────────────────────────────┤
    │                                           │
    │ 11. Get download link                     │
    ├──────────────────────────────────────────>│
    │                                           │
    │ 12. {download_url: "https://s3..."}       │
    │<──────────────────────────────────────────┤
    │                                           │
    │ 13. Download artifact                     │
    ├──────────────────────────────────────────>│
    │                                           │ S3
    │ 14. Artifact JSON                         │
    │<──────────────────────────────────────────┤
    │                                           │
```

---

### Cost Estimation Flow

```
┌──────┐                           ┌──────┐
│ LLM  │                           │ API  │
└───┬──┘                           └───┬──┘
    │                                  │
    │ 1. estimate_analysis_cost()      │
    ├─────────────────────────────────>│
    │                                  │
    │                                  │ 2. Shallow clone
    │                                  │
    │                                  │ 3. Count SLOC
    │                                  │
    │                                  │ 4. Detect languages
    │                                  │
    │                                  │ 5. Calculate tokens
    │                                  │
    │ 6. {tokens: 250, sloc: 125k}     │
    │<─────────────────────────────────┤
    │                                  │
```

**Estimation is fast:** ~10-30 seconds (shallow clone only)

---

## Authentication Flow

### JWT Token Lifecycle

```
┌─────────┐                    ┌─────────┐                    ┌─────────┐
│  User   │                    │   Web   │                    │   API   │
│ (Human) │                    │   App   │                    │         │
└────┬────┘                    └────┬────┘                    └────┬────┘
     │                              │                              │
     │ 1. Login (email/password)    │                              │
     ├─────────────────────────────>│                              │
     │                              │                              │
     │                              │ 2. Authenticate              │
     │                              ├─────────────────────────────>│
     │                              │                              │
     │                              │ 3. Generate JWT              │
     │                              │<─────────────────────────────┤
     │                              │                              │
     │ 4. Display JWT in settings   │                              │
     │<─────────────────────────────┤                              │
     │                              │                              │
     │ 5. Copy JWT                  │                              │
     │                              │                              │
     │ 6. Configure MCP server      │                              │
     │    with JWT                  │                              │
     │                              │                              │
```

### Per-Request Authentication

```
┌─────────┐                    ┌─────────┐
│   MCP   │                    │   API   │
│ Server  │                    │         │
└────┬────┘                    └────┬────┘
     │                              │
     │ Authorization: Bearer JWT    │
     ├─────────────────────────────>│
     │                              │
     │                              │ 1. Decode JWT
     │                              │
     │                              │ 2. Verify signature
     │                              │
     │                              │ 3. Check account status
     │                              │
     │                              │ 4. Load permissions
     │                              │
     │ 200 OK + response            │
     │<─────────────────────────────┤
     │                              │
```

**JWT Contents:**
```json
{
  "sub": "user_id_123",
  "email": "user@example.com",
  "plan": "pro",
  "iat": 1704067200,
  "exp": null  // Tokens don't expire
}
```

---

## Analysis Workflow

### Repository Analysis Pipeline

```
┌───────────────────────────────────────────────────────────┐
│                     Job Lifecycle                         │
└───────────────────────────────────────────────────────────┘

1. SUBMISSION
   ├─ Validate repository URL
   ├─ Check user token balance
   ├─ Deduct tokens
   └─ Create job record

2. QUEUED_PRECHECK
   ├─ Verify repository accessibility
   ├─ Detect languages
   └─ Queue for worker

3. RUNNING (Core Worker)
   ├─ Clone repository
   ├─ Build file tree
   ├─ Extract dependencies
   │  ├─ Python: import statements
   │  ├─ TypeScript/JS: import/require
   │  ├─ Java: import statements
   │  ├─ Rust: import statements
   │  └─ Go: import statements
   ├─ Build dependency graph
   ├─ Detect circular dependencies
   └─ Calculate metrics

4. RUNNING (LLM Worker - Optional)
   ├─ Generate architecture summary
   ├─ Answer user questions
   └─ Identify patterns

5. RUNNING (Export Worker)
   ├─ Generate artifact JSON
   ├─ Compress artifact
   ├─ Upload to S3
   └─ Generate presigned URL

6. COMPLETED
   ├─ Update job status
   ├─ Store metadata
   └─ Make artifact available
```

---

## Storage Architecture

### Artifact Storage (S3)

**Bucket Configuration:**
- Region: `us-east-1`
- Encryption: AES-256
- Versioning: Disabled
- Lifecycle: 90-day retention

**Access Pattern:**
```
API generates presigned URL
    ↓
Client downloads directly from S3
    ↓
URL expires after 1 hour
    ↓
Request new URL if needed
```

**Security:**
- No public access
- Bucket policy: API service role only
- Presigned URLs for temporary access
- HTTPS required

---

## Network Architecture

### Production Deployment

```
                    Internet
                       │
                       │ HTTPS (443)
                       ▼
              ┌────────────────┐
              │  Caddy Proxy   │
              │  (TLS + Proxy) │
              └────────┬───────┘
                       │
       ┌───────────────┼───────────────┐
       │               │               │
       ▼               ▼               ▼
┌──────────┐    ┌──────────┐    ┌──────────┐
│   API    │    │   MCP    │    │   Web    │
│ :8000    │    │  :8080   │    │  :3000   │
└──────────┘    └──────────┘    └──────────┘
       │               │
       └───────┬───────┘
               │
         Docker Network
               │
       ┌───────┼───────┐
       │       │       │
       ▼       ▼       ▼
  ┌────────┐ ┌────┐ ┌────┐
  │Workers │ │S3  │ │DB  │
  └────────┘ └────┘ └────┘
```

### Domain Configuration

**Production:**
- API: `api.pvizgenerator.com`
- MCP: `mcp.pvizgenerator.com`
- Web: `pvizgenerator.com`

**Internal (Docker):**
- API: `http://api:8000`
- MCP: `http://pviz-mcp-server:8080`
- Workers: `http://worker:8001`

---

## Deployment Modes

### Mode 1: STDIO (Claude Desktop)

```
┌────────────────────────────────────┐
│      Claude Desktop Process        │
│  ┌──────────────────────────────┐  │
│  │  MCP Server Subprocess       │  │
│  │  (pviz_mcp_server.py)        │  │
│  │                              │  │
│  │  stdin  ◄──── JSON-RPC       │  │
│  │  stdout ────► Responses      │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

**Configuration:**
```json
{
  "command": "python",
  "args": ["/path/to/pviz_mcp_server.py"],
  "env": {
    "PVIZ_JWT_TOKEN": "...",
    "PVIZ_API_URL": "https://api.pvizgenerator.com"
  }
}
```

---

### Mode 2: HTTP (Cloud Deployment)

```
┌────────────────────────────────────┐
│      Cloud Service (e.g., GCR)     │
│  ┌──────────────────────────────┐  │
│  │  HTTP MCP Server             │  │
│  │  (pviz_mcp_http.py)          │  │
│  │                              │  │
│  │  POST /mcp ◄──── JSON-RPC    │  │
│  │  200 OK   ────► Responses    │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

**Client Configuration:**
```json
{
  "url": "https://mcp.pvizgenerator.com/mcp",
  "headers": {
    "Authorization": "Bearer USER_JWT_TOKEN"
  }
}
```

---

### Mode 3: Docker Compose (Self-Hosted)

```
┌─────────────────────────────────────────┐
│         Docker Compose Stack            │
│  ┌───────────────────────────────────┐  │
│  │  Caddy (Reverse Proxy)            │  │
│  │  :80/:443 → Services              │  │
│  └─────────────┬─────────────────────┘  │
│                │                         │
│  ┌─────────────┼─────────────────────┐  │
│  │             │                     │  │
│  │  ┌──────────▼────┐  ┌───────────┐│  │
│  │  │ MCP Server    │  │ API       ││  │
│  │  │ :8080         │  │ :8000     ││  │
│  │  └───────────────┘  └───────────┘│  │
│  │                                   │  │
│  │  ┌───────────────────────────┐   │  │
│  │  │ Workers                   │   │  │
│  │  └───────────────────────────┘   │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

**Secrets Management:**
- JWT token in Docker secrets
- Environment variables for config
- Mounted volumes for logs

---

## Security Considerations

### 1. Token Security

**✅ DO:**
- Store JWT in environment variables
- Use Docker secrets in production
- Rotate tokens periodically
- Use HTTPS for all API calls

**❌ DON'T:**
- Commit tokens to version control
- Log tokens in application logs
- Share tokens between accounts
- Hardcode tokens in source code

---

### 2. GitHub Token Handling

**Security Model:**
- Tokens transmitted over HTTPS only
- Never persisted to disk or database
- Used only during repository clone
- Destroyed after analysis completes

**Flow:**
```
User provides token
    ↓
MCP server → API (HTTPS)
    ↓
Worker receives token
    ↓
Clone repository
    ↓
Token destroyed
```

---

### 3. Network Security

**Layers:**
1. **TLS/HTTPS:** All API communication encrypted
2. **JWT Authentication:** Token-based auth
3. **Presigned URLs:** Time-limited S3 access
4. **CORS:** Configured for web clients
5. **Rate Limiting:** Prevent abuse

---

## Performance Characteristics

### Latency Breakdown

**Cost Estimation:**
- Network: 50-100ms
- Shallow clone: 2-5 seconds
- SLOC counting: 1-3 seconds
- **Total: ~5-10 seconds**

**Analysis Submission:**
- Validation: 50ms
- Balance check: 50ms
- Job creation: 100ms
- **Total: ~200ms**

**Job Processing:**
- Small repo (<50K SLOC): 30-90 seconds
- Medium repo (50-250K SLOC): 2-5 minutes
- Large repo (250-2000K SLOC): 5-15 minutes

**Artifact Download:**
- URL generation: 50ms
- S3 download: depends on size
  - Small (500KB): <1 second
  - Large (50MB): 5-10 seconds

---

## Scalability

### Horizontal Scaling

**MCP Server:**
- Stateless (can scale infinitely)
- Load balancer distributes traffic (coming soon)
- Each instance independent

**Workers:**
- Auto-scaling based on queue depth (coming soon)
- Current maximum concurrent: 2 workers (scaling to come as demand dictates)
- Each worker handles 1 job at a time

**API:**
- Stateless
- Database connection pooling
- Redis for queue management

---

## Monitoring & Observability

### Key Metrics (to be implemented)

**MCP Server:**
- Request rate
- Error rate
- Response time (p50, p95, p99)
- Active connections

**API:**
- Job submission rate
- Job completion rate
- Job failure rate
- Average processing time
- Queue depth

**Workers:**
- Active workers
- Jobs per hour
- Average job duration
- Error rate

### Logging

**MCP Server:**
```json
{
  "timestamp": "2026-01-11T12:00:00Z",
  "level": "INFO",
  "message": "Tool call: analyze_repository",
  "repo_url": "django/django",
  "user_id": "user_123"
}
```

**API:**
```json
{
  "timestamp": "2026-01-11T12:00:00Z",
  "level": "INFO",
  "endpoint": "POST /jobs/github",
  "status_code": 201,
  "duration_ms": 150,
  "user_id": "user_123"
}
```

---

For deployment instructions, see [SETUP.md](SETUP.md) and [CADDY_DEPLOYMENT.md](CADDY_DEPLOYMENT.md).
