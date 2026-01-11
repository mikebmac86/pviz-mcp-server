# pviz MCP Server - Setup & Deployment Guide

## Quick Start (Local Development)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
export PVIZ_JWT_TOKEN="your-jwt-token-here"
export PVIZ_API_URL="https://api.pvizgenerator.com"
```

### 3. Choose Your Mode

#### STDIO Mode (for Claude Desktop)

```bash
python pviz_mcp_server.py
```

**When to use:**
- Local Claude Desktop integration
- Direct process spawning
- Single-user scenarios

#### HTTP Mode (for cloud deployment)

```bash
python pviz_mcp_http.py
```

**When to use:**
- Cloud deployments (GCP, AWS, Fly.io)
- Multi-user scenarios
- Web-based LLM clients
- Behind a reverse proxy

**HTTP Endpoints:**
- `POST /mcp` - MCP JSON-RPC endpoint
- `GET /health` - Health check

**Testing HTTP mode:**
```bash
# Health check
curl http://localhost:8080/health

# MCP tools list
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_USER_JWT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## MCP Server Modes Comparison

| Feature | STDIO Mode | HTTP Mode |
|---------|-----------|-----------|
| **Protocol** | stdin/stdout | HTTP POST |
| **Use Case** | Claude Desktop | Cloud deployment |
| **Authentication** | Environment variable | Per-request header |
| **Scaling** | 1 process per user | Horizontal scaling |
| **Deployment** | Local machine | Cloud services |
| **Configuration** | JSON file | Environment + secrets |

### STDIO Mode Details

**Communication:**
```
Claude Desktop Process
    ↓
  Spawns python pviz_mcp_server.py
    ↓
  JSON-RPC via stdin/stdout
    ↓
  MCP Server responds via stdout
```

**Authentication:**
- JWT token from environment variable
- Same token used for all API calls
- Configured once in claude_desktop_config.json

---

### HTTP Mode Details

**Communication:**
```
LLM Client (web/mobile)
    ↓
  HTTP POST to /mcp endpoint
    ↓
  Authorization: Bearer USER_JWT
    ↓
  MCP Server validates and responds
```

**Authentication:**
- Per-request JWT in Authorization header
- Different users can use same server
- Token validated on each request

**Endpoint structure:**
```
POST https://your-server.com/mcp
Content-Type: application/json
Authorization: Bearer USER_JWT_TOKEN

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "analyze_repository",
    "arguments": {
      "repo_url": "https://github.com/django/django"
    }
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "job_id": "job_abc123",
    "status": "completed",
    "artifact": { ... }
  }
}
```

---

## Integration with Claude Desktop (Local)

### 1. Configure Claude Desktop

Edit your Claude Desktop config file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`  
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add:

```json
{
  "mcpServers": {
    "pviz": {
      "command": "python",
      "args": ["/absolute/path/to/pviz_mcp_server.py"],
      "env": {
        "PVIZ_JWT_TOKEN": "your-jwt-token-here",
        "PVIZ_API_URL": "https://api.pvizgenerator.com"
      }
    }
  }
}
```

### 2. Restart Claude Desktop

Restart Claude Desktop to load the MCP server.

### 3. Test It

```
Analyze https://github.com/django/django using pviz and tell me about its architecture
```

---

## Cloud Deployment Options

### Option 1: AWS

#### AWS Lambda + API Gateway

```bash
pip install -r requirements.txt -t package/
cp pviz_mcp_server.py package/

cd package
zip -r ../pviz-mcp-lambda.zip .
cd ..
zip -g pviz-mcp-lambda.zip pviz_mcp_server.py
```

```bash
aws lambda create-function   --function-name pviz-mcp-server   --runtime python3.11   --handler pviz_mcp_http.app   --zip-file fileb://pviz-mcp-lambda.zip   --timeout 300   --memory-size 512   --environment Variables="{PVIZ_API_URL=https://api.pvizgenerator.com}"
```

---

#### AWS ECS (Fargate)

```bash
docker build -t pviz-mcp-server .
docker tag pviz-mcp-server:latest YOUR-ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pviz-mcp-server:latest
docker push YOUR-ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pviz-mcp-server:latest
```

---

### Option 2: Google Cloud Run

```bash
gcloud run deploy pviz-mcp-server   --source .   --region us-central1   --set-env-vars PVIZ_API_URL=https://api.pvizgenerator.com   --set-secrets PVIZ_JWT_TOKEN=pviz-jwt-token:latest
```

---

### Option 3: Fly.io

```bash
fly launch
fly secrets set PVIZ_JWT_TOKEN=your-token-here
fly deploy
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|--------|----------|---------|-------------|
| PVIZ_JWT_TOKEN | Yes | — | JWT token |
| PVIZ_API_URL | No | https://api.pvizgenerator.com | Backend URL |
| PVIZ_POLL_INTERVAL | No | 5 | Poll interval (sec) |
| PVIZ_MAX_POLL_ATTEMPTS | No | 60 | Max poll attempts |
| PORT | No | 8080 | HTTP port |
| HOST | No | 0.0.0.0 | Bind host |
| LOG_LEVEL | No | info | Logging level |

---

## Testing Your Deployment

```bash
curl https://your-mcp-server.com/health
```

```bash
curl -X POST https://your-mcp-server.com/mcp   -H "Content-Type: application/json"   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## Security Best Practices

- Never commit JWT tokens
- Use secrets managers
- Enforce HTTPS
- Monitor usage and rate limits

---

## Troubleshooting

### PVIZ_JWT_TOKEN not set

```bash
export PVIZ_JWT_TOKEN="your-token"
```

### Timeout issues

```bash
export PVIZ_MAX_POLL_ATTEMPTS=120
```

---

## Next Steps

1. Run locally
2. Test with Claude
3. Deploy to cloud
4. Submit to MCP registry

---

## Support

- MCP Docs: https://modelcontextprotocol.io
- API Docs: https://api.pvizgenerator.com/docs
