# pviz MCP Server

**Model Context Protocol server for pviz dependency analysis**

Expose your pviz dependency analysis capabilities to LLMs (Claude, GPT, etc.) via the standardized MCP protocol.

## What This Does

This MCP server allows LLMs to:
- Analyze GitHub repositories for dependency structures
- Detect circular dependencies
- Compare architectures of different projects
- Extract metrics (LOC, complexity, coupling)
- Download full dependency graphs from your S3 storage

**Architecture:**
```
LLM (Claude) → MCP Server → api.pvizgenerator.com → S3 → Dependency Graph
```

## Files Included

| File | Purpose |
|------|---------|
| `pviz_mcp_server.py` | Main MCP server (STDIO mode for local use) |
| `pviz_mcp_http.py` | HTTP wrapper for cloud deployment |
| `api_adapter.py` | Configure to match your actual API endpoints |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container image for deployment |
| `SETUP.md` | Complete deployment guide |
| `quick_start.py` | Environment checker script |

## Example Queries

Once configured, try these with Claude:

```
Check my account balance

Estimate the cost for analyzing facebook/react

Analyze https://github.com/django/django and summarize its architecture

Does https://github.com/facebook/react have any circular dependencies?

Show me my recent job history
```

For complete documentation, see the guides below.

## 📚 Documentation

- **[INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md)** - Production deployment (30 min)
- **[CADDY_DEPLOYMENT.md](CADDY_DEPLOYMENT.md)** - Docker Compose + Caddy (15 min)
- **[API_ENDPOINTS.md](API_ENDPOINTS.md)** - API reference & rate limits
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Common issues & solutions

## Quick Start

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
export PVIZ_JWT_TOKEN="your-jwt-token-here"
export PVIZ_API_URL="https://api.pvizgenerator.com"
```

### 3. Verify Setup

```bash
python quick_start.py
```

This will check:
- ✅ Python version (3.8+)
- ✅ Dependencies installed
- ✅ Environment variables set
- ✅ API connectivity

### 4. Test Locally

```bash
python pviz_mcp_server.py
```

### 5. Use with Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

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
"Analyze https://github.com/django/django using pviz"
```

## Cloud Deployment

See [`SETUP.md`](SETUP.md) for detailed guides on:
- AWS Lambda + API Gateway
- AWS ECS (Fargate)
- Google Cloud Run
- Fly.io

**Quick Deploy to Cloud Run:**

```bash
gcloud run deploy pviz-mcp-server \
  --source . \
  --platform managed \
  --region us-central1 \
  --set-env-vars PVIZ_API_URL=https://api.pvizgenerator.com \
  --set-secrets PVIZ_JWT_TOKEN=pviz-jwt-token:latest
```

## Available Tools

The MCP server exposes these tools to LLMs:

### 1. `analyze_repository`
Analyze any Git repository and get dependency graph

```python
analyze_repository(
    repo_url="https://github.com/django/django",
    languages=["python"],
    wait_for_completion=True,
    include_full_graph=False
)
```

### 2. `get_circular_dependencies`
Find circular dependency chains

```python
get_circular_dependencies(
    repo_url="https://github.com/facebook/react"
)
```

### 3. `get_repository_metrics`
Get high-level architecture metrics

```python
get_repository_metrics(
    repo_url="https://github.com/tensorflow/tensorflow"
)
```

### 4. `compare_repositories`
Compare two projects

```python
compare_repositories(
    repo_url_1="https://github.com/vuejs/vue",
    repo_url_2="https://github.com/facebook/react"
)
```

### 5. `get_analysis_status`
Check status of running job

```python
get_analysis_status(job_id="abc-123")
```

### 6. `download_dependency_graph`
Download full graph from S3

```python
download_dependency_graph(s3_url="https://...")
```

## Customizing for Your API

**IMPORTANT:** Your actual FastAPI might have different endpoint paths or response formats.

Edit `api_adapter.py` to match your API:

```python
# Update these methods:
- submit_analysis()      # POST /v1/analyze endpoint
- get_job_status()       # GET /v1/analysis/status/{id}
- extract_job_id()       # Parse job ID from response
- extract_s3_url()       # Parse S3 URL from response
- get_job_status_value() # Map status strings
```

Then run:
```bash
python api_adapter.py  # Test your configuration
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PVIZ_JWT_TOKEN` | ✅ | - | Your JWT auth token |
| `PVIZ_API_URL` | No | `https://api.pvizgenerator.com` | API base URL |
| `PVIZ_POLL_INTERVAL` | No | `5` | Polling interval (seconds) |
| `PVIZ_MAX_POLL_ATTEMPTS` | No | `60` | Max poll attempts |

## Architecture

```
┌─────────────┐
│   Claude    │  LLM requests dependency analysis
│     or      │
│   GPT-4     │
└──────┬──────┘
       │
       │ MCP Protocol (JSON-RPC)
       │
       ▼
┌─────────────────┐
│  MCP Server     │  Exposes tools like analyze_repository()
│  (This code)    │  Polls job status, downloads results
└────────┬────────┘
         │
         │ HTTP + JWT
         │
         ▼
┌────────────────────┐
│  api.pvizgenerator │  Your existing FastAPI backend
│      .com          │  - Clones repos
│                    │  - Analyzes dependencies
│                    │  - Uploads to S3
└─────────┬──────────┘
          │
          │ S3 Upload
          │
          ▼
┌──────────────────┐
│   Amazon S3      │  Stores dependency graph JSON
│                  │  Returns presigned URLs
└──────────────────┘
```

## Example Queries for LLMs

Once configured with Claude/GPT:

```
1. "Analyze https://github.com/django/django and tell me about its architecture"

2. "Does https://github.com/facebook/react have circular dependencies?"

3. "Compare Vue.js and React architectures"

4. "What are the metrics for https://github.com/tensorflow/tensorflow?"

5. "Analyze the Python code in https://github.com/pallets/flask"
```

## Next Steps

1. ✅ Get it working locally with Claude Desktop
2. ✅ Deploy to cloud (AWS/GCP recommended)
3. 📝 Submit to MCP Registry: https://github.com/modelcontextprotocol/servers
4. 🎯 Contact Anthropic about partnership opportunities
5. 📢 Share with developer community

## Security Notes

- **Never commit JWT tokens to git**
- Use environment variables or secrets managers
- Enable HTTPS for production deployments
- Consider adding rate limiting
- Monitor API usage

## Support & Documentation

- **Full Setup Guide:** See `SETUP.md`
- **MCP Documentation:** https://modelcontextprotocol.io
- **Anthropic Docs:** https://docs.anthropic.com
- **Your API Docs:** https://api.pvizgenerator.com/docs

## License

**MIT License** - MCP Server Code Only

This MCP server code is open source under the MIT License. See [LICENSE](LICENSE) for details.

**Important:** This license applies ONLY to the MCP server adapter code, not to:
- The pviz backend API (proprietary)
- The pviz analysis engine (proprietary)
- The pviz web application (proprietary)

**Using the MCP Server:**
- ✅ Free to use, modify, and distribute the MCP server code
- ✅ Can self-host the MCP server
- ⚠️ Requires a valid pviz account and API token
- ⚠️ Subject to pviz API Terms of Service
- ⚠️ API usage subject to your account's token balance/limits

**In other words:** The MCP server is free and open source, but it connects to the pviz API service which requires an account.

---

Built with [Model Context Protocol](https://modelcontextprotocol.io) by Anthropic
