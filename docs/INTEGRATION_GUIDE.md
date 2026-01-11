# pviz MCP Server - Integration Guide

Complete guide for integrating the MCP server with your existing pviz stack.

---

## 🚀 Quick Integration (30 Minutes)

### Step 1: Replace API Adapter (5 min)

```bash
# Backup old adapter
mv api_adapter.py api_adapter_old.py

# Use production version
cp api_adapter_production.py api_adapter.py

# Test it works
export PVIZ_JWT_TOKEN="your-token"
export PVIZ_API_URL="https://api.pvizgenerator.com"
python api_adapter.py
```

**Expected output:**
```
✅ Account: user@example.com
✅ Balance: 42 tokens
✅ Estimate test passed
✅ Job history test passed
✅ All tests passed!
```

---

### Step 2: Add New MCP Tools (10 min)

Open `pviz_mcp_server.py` and add these 4 tools (from `mcp_tools_to_add.py`):

1. **check_account_balance()** – Shows email, plan, token balance  
2. **estimate_analysis_cost()** – Pre-flight cost check  
3. **get_job_history()** – Recent jobs with status  
4. **retrieve_past_result()** – Re-download completed analysis  

**Where to add:** After existing `analyze_repository` tool, before `if __name__ == "__main__"`.

---

### Step 3: Test Locally (10 min)

```bash
# Test MCP server
python pviz_mcp_server.py
```

Configure Claude Desktop (`~/.config/Claude/claude_desktop_config.json`):

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

Restart Claude Desktop and test:
```
Check my account balance
Estimate cost for facebook/react
Show my recent job history
```

---

### Step 4: Deploy (5 min)

See deployment options below based on your infrastructure.

---

## 🏗️ Your Actual API Endpoints

| Feature | Endpoint |
|-------|----------|
| Account info | GET /auth/me |
| Token balance | GET /tokens/overview |
| Cost estimate | POST /estimate/github |
| Submit job | POST /jobs/github |
| Job status | GET /jobs/{id} |
| Job history | GET /jobs |
| Download link | GET /jobs/{id}/download-link |

### Status Mapping

- completed → completed  
- running, queued_precheck → processing  
- failed, canceled → failed  
- awaiting_payment, insufficient_tokens → pending  

---

## 📊 Rate Limits

- Job creation: **20/hour**
- Cost estimation: **10 / 5 min**
- Job download links: **10 / min**
- `/auth/me`: **300 / min**

---

## 🎯 Deployment Options

### Docker Compose + Caddy (Recommended)

See **Docker Compose + Caddy Deployment** guide.

---

### Google Cloud Run

```bash
gcloud run deploy pviz-mcp-server   --source .   --region us-central1   --set-env-vars PVIZ_API_URL=https://api.pvizgenerator.com   --set-secrets PVIZ_JWT_TOKEN=pviz-jwt:latest
```

---

### AWS ECS / Fargate

```bash
docker build -t pviz-mcp-server .
docker push $ECR_URL/pviz-mcp-server:latest
```

---

## ✅ Testing Checklist

- Adapter tests pass
- MCP server starts locally
- Claude Desktop tools visible
- Health endpoint responds
- End-to-end analysis works

---

## 🔧 Environment Variables

| Variable | Required | Description |
|--------|----------|-------------|
| PVIZ_JWT_TOKEN | Yes | API authentication token |
| PVIZ_API_URL | No | Backend URL |
| PORT | No | Server port |
| HOST | No | Bind address |
| LOG_LEVEL | No | Logging level |

---

## 📋 Pre-Deployment Checklist

- JWT stored securely
- `.env` or secrets used
- API URL correct
- Logs accessible
- Monitoring enabled

---

## 📊 Monitoring

Track:
- Request rate
- Error rate
- Token usage
- Latency

---

## 🚀 Next Steps

1. Deploy to production
2. Test with users
3. Monitor usage
4. Iterate

---

**Total integration time: ~30 minutes**

🎉 **You're production-ready!**
