# pviz MCP Server

**Model Context Protocol server for pviz dependency analysis**

Expose your pviz dependency analysis capabilities to LLMs (Claude, GPT, etc.) via the standardized MCP protocol.

---

## What This Does

This MCP server allows LLMs to:
- Analyze GitHub repositories for dependency structures
- Detect circular dependencies
- Compare architectures of different projects
- Extract metrics (LOC, complexity, coupling)
- Download full dependency graphs from S3 storage

### Architecture

```
LLM (Claude / GPT)
        │
        │ MCP (JSON-RPC)
        ▼
MCP Server
        │
        │ HTTP + JWT
        ▼
api.pvizgenerator.com
        │
        │ Presigned URLs
        ▼
Amazon S3 → Dependency Graph JSON
```

---

## Files Included

| File | Purpose |
|------|---------|
| pviz_mcp_server.py | Main MCP server (STDIO mode) |
| pviz_mcp_http.py | HTTP wrapper for cloud deployment |
| api_adapter.py | Backend API adapter |
| requirements.txt | Python dependencies |
| Dockerfile | Container image |
| SETUP.md | Deployment guide |
| quick_start.py | Environment checker |

---

## Example Queries

Try these with Claude or GPT:

```
Check my account balance
Estimate the cost for analyzing facebook/react
Analyze https://github.com/django/django and summarize its architecture
Does https://github.com/facebook/react have any circular dependencies?
Show me my recent job history
```

---

## Documentation

- INTEGRATION_GUIDE.md – MCP integration & deployment
- CADDY_DEPLOYMENT.md – Docker Compose + Caddy
- API_ENDPOINTS.md – API reference & limits
- TROUBLESHOOTING.md – Common issues

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
export PVIZ_JWT_TOKEN="your-jwt-token"
export PVIZ_API_URL="https://api.pvizgenerator.com"
```

### 3. Verify Setup

```bash
python quick_start.py
```

Checks:
- Python version (3.8+)
- Dependencies
- Environment variables
- API connectivity

### 4. Run Locally

```bash
python pviz_mcp_server.py
```

---

## Claude Desktop Configuration

Edit your Claude config:

```json
{
  "mcpServers": {
    "pviz": {
      "command": "python",
      "args": ["/absolute/path/to/pviz_mcp_server.py"],
      "env": {
        "PVIZ_JWT_TOKEN": "your-token",
        "PVIZ_API_URL": "https://api.pvizgenerator.com"
      }
    }
  }
}
```

Restart Claude Desktop and try:

```
Analyze https://github.com/django/django using pviz
```

---

## Cloud Deployment

Supported targets:
- Google Cloud Run
- AWS ECS / Fargate
- Fly.io

### Quick Cloud Run Deploy

```bash
gcloud run deploy pviz-mcp-server   --source .   --region us-central1   --set-env-vars PVIZ_API_URL=https://api.pvizgenerator.com   --set-secrets PVIZ_JWT_TOKEN=pviz-jwt-token:latest
```

---

## Available MCP Tools

### analyze_repository
Analyze a Git repository and optionally wait for completion.

### get_circular_dependencies
Detect circular dependency chains.

### get_repository_metrics
Return high-level architectural metrics.

### compare_repositories
Compare two repositories structurally.

### get_analysis_status
Check status of a running analysis job.

### download_dependency_graph
Download full dependency graph JSON.

---

## Customizing for Your API

Edit `api_adapter.py` to match your backend:

- submit_analysis()
- get_job_status()
- extract_job_id()
- extract_s3_url()
- map_status_values()

Then validate:

```bash
python api_adapter.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|--------|----------|---------|-------------|
| PVIZ_JWT_TOKEN | Yes | — | API JWT token |
| PVIZ_API_URL | No | https://api.pvizgenerator.com | API base |
| PVIZ_POLL_INTERVAL | No | 5 | Poll interval (seconds) |
| PVIZ_MAX_POLL_ATTEMPTS | No | 60 | Max polling attempts |

---

## Security Notes

- Never commit JWT tokens
- Use secrets managers in production
- Enable HTTPS
- Monitor API usage and rate limits

---

## License

**MIT License (MCP Server Code Only)**

This license applies only to the MCP server adapter code.
The pviz backend, analysis engine, and web application remain proprietary.

Using this MCP server:
- Free to use and self-host
- Requires a valid pviz account
- Subject to pviz API Terms of Service
- Token usage enforced by backend


