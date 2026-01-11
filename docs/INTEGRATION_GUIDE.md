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

1. **check_account_balance()** - Shows email, plan, token balance
2. **estimate_analysis_cost()** - Pre-flight cost check
3. **get_job_history()** - Recent jobs with status
4. **retrieve_past_result()** - Re-download completed analysis

**Where to add:** After existing `analyze_repository` tool, before `if __name__ == "__main__"`.

---

### Step 3: Test Locally (10 min)

```bash
# Test MCP server
python pviz_mcp_server.py

# In another terminal, configure Claude Desktop
# Edit: ~/.config/Claude/claude_desktop_config.json
```

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

**Restart Claude Desktop and test:**
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

Based on your TypeScript client, here's what the adapter calls:

| Feature | Endpoint | Request Format |
|---------|----------|----------------|
| Account info | `GET /auth/me` | - |
| Token balance | `GET /tokens/overview` | - |
| Cost estimate | `POST /estimate/github` | `{repo_spec: {...}}` |
| Submit job | `POST /jobs/github` | `{repo_spec, expected_tokens, pricing_choice}` |
| Job status | `GET /jobs/{id}` | - |
| Job history | `GET /jobs` | `?skip=0&limit=10` |
| Download link | `GET /jobs/{id}/download-link` | - |

### Key Data Structures:

**repo_spec format:**
```json
{
  "provider": "github",
  "repo": "owner/repo",
  "branch": "main",      // optional
  "subpath": "src/app"   // optional
}
```

**Status values:**
- `completed` → "completed"
- `running`, `queued_precheck` → "processing"
- `failed`, `canceled` → "failed"
- `awaiting_payment`, `insufficient_tokens` → "pending"

---

## 📊 Rate Limits (Your Actual Limits)

### Job Creation:
- **20 jobs/hour** per user (no tiers, same for everyone)
- **5 concurrent jobs** per user (recommended)
- Token balance is primary cost control

### Other Endpoints:
- `/auth/me`: 300/min
- `/estimate/github`: 10/5min (expensive - full clone)
- `/jobs/{id}/download-link`: 10/min
- Login: 10/5min
- Signup: 3/hour

---

## 🎯 Deployment Options

### Option 1: Docker Compose + Caddy (Recommended for Your Stack)

See `CADDY_DEPLOYMENT.md` for detailed steps.

**Quick version:**
```bash
# 1. Add to docker-compose.yml
pviz-mcp-server:
  build: ./mcp-server
  environment:
    PVIZ_API_URL: http://api:8000
  secrets:
    - mcp_jwt_token
  expose:
    - "8080"

# 2. Create Caddyfile.mcp
mcp.pvizgenerator.com {
    reverse_proxy pviz-mcp-server:8080
}

# 3. Deploy
docker-compose up -d pviz-mcp-server
```

---

### Option 2: Google Cloud Run (Easiest)

```bash
gcloud run deploy pviz-mcp-server \
  --source . \
  --region us-central1 \
  --set-env-vars PVIZ_API_URL=https://api.pvizgenerator.com \
  --set-secrets PVIZ_JWT_TOKEN=pviz-jwt:latest
```

**Cost:** ~$0-5/month (scales to zero)

---

### Option 3: AWS ECS/Fargate

```bash
# Build and push to ECR
docker build -t pviz-mcp-server .
docker tag pviz-mcp-server:latest $ECR_URL/pviz-mcp-server:latest
docker push $ECR_URL/pviz-mcp-server:latest

# Deploy via ECS console or CloudFormation
```

**Cost:** ~$15-30/month

---

## ✅ Testing Checklist

### Local Testing:
```bash
# 1. Test adapter
python api_adapter.py
# ✅ All 7 endpoints working

# 2. Test MCP server
python pviz_mcp_server.py
# ✅ Server starts without errors

# 3. Test in Claude Desktop
# ✅ Tools appear in Claude
# ✅ Can check balance
# ✅ Can estimate costs
# ✅ Can analyze repos
```

### Production Testing:
```bash
# 1. Health check
curl https://mcp.pvizgenerator.com/health
# ✅ Returns {"status": "healthy"}

# 2. MCP endpoint
curl -X POST https://mcp.pvizgenerator.com/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# ✅ Returns list of tools

# 3. End-to-end test in Claude Desktop
# ✅ Point to production URL
# ✅ Run full analysis workflow
```

---

## 🎨 Enhanced User Experience

### Before Integration:
```
User: Analyze repo X
→ API call
→ 402 Insufficient tokens
→ User confused 😕
```

### After Integration:
```
User: Analyze repo X

Claude: Let me check your account...
        Account: user@example.com (42 tokens)
        
        Estimating cost...
        Tokens needed: 3
        Your balance: 42 tokens
        After analysis: 39 tokens
        
        Shall I proceed?

User: Yes

Claude: ✅ Analysis started!
        Tokens charged: 3
        New balance: 39 tokens
        
        [Shows results...]
```

---

## 🔧 Configuration Reference

### Environment Variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PVIZ_JWT_TOKEN` | ✅ Yes | - | API authentication token |
| `PVIZ_API_URL` | No | `https://api.pvizgenerator.com` | Your backend URL |
| `PORT` | No | `8080` | HTTP server port |
| `HOST` | No | `0.0.0.0` | HTTP server host |
| `LOG_LEVEL` | No | `info` | Logging level |

### Security Best Practices:

**For Docker Compose:**
```yaml
secrets:
  mcp_jwt_token:
    file: ./secrets/mcp_jwt_token.txt

# Then in service:
secrets:
  - mcp_jwt_token
```

**For Cloud Run:**
```bash
# Store in Secret Manager
echo -n "your-token" | gcloud secrets create pviz-jwt-token --data-file=-

# Reference in deployment
--set-secrets PVIZ_JWT_TOKEN=pviz-jwt-token:latest
```

**For Local Development:**
```bash
# Use .env file (already in .gitignore)
cp .env.example .env
# Edit .env with your token
```

---

## 📋 Pre-Deployment Checklist

### Code:
- [ ] `api_adapter.py` replaced with production version
- [ ] 4 new tools added to `pviz_mcp_server.py`
- [ ] All tests pass: `python api_adapter.py`
- [ ] No syntax errors: `python -m py_compile *.py`

### Configuration:
- [ ] JWT token stored securely (not in code)
- [ ] `.env` in `.gitignore`
- [ ] Environment variables set correctly
- [ ] API URL points to production

### Testing:
- [ ] Local MCP server works
- [ ] Claude Desktop integration works
- [ ] Docker builds successfully
- [ ] Health endpoint responds

### Deployment:
- [ ] Deployed to staging first
- [ ] End-to-end test passed
- [ ] Monitoring/alerts configured
- [ ] Logs accessible

---

## 🐛 Common Issues

### "Invalid token"
```bash
# Test token manually
curl https://api.pvizgenerator.com/auth/me \
  -H "Authorization: Bearer $PVIZ_JWT_TOKEN"

# Should return your account info
# If 401: Get new token from dashboard
```

### "Connection refused"
```bash
# Check MCP server is running
curl http://localhost:8080/health

# Check can reach your API
curl https://api.pvizgenerator.com/health
```

### "Module not found"
```bash
# Install all dependencies
pip install -r requirements.txt
```

See `TROUBLESHOOTING.md` for more issues.

---

## 📊 Monitoring

### Logs:
```bash
# Docker Compose
docker-compose logs -f pviz-mcp-server

# Cloud Run
gcloud logging read "resource.type=cloud_run_revision" --limit 50

# Local
# Logs go to stderr by default
```

### Metrics to Track:
- Request rate (requests/minute)
- Error rate (4xx, 5xx errors)
- Response time (p50, p95, p99)
- Token consumption (tokens/day)
- Active users

---

## 🎯 What You've Built

### Three Protection Layers:
1. **Authentication** - Must be logged in
2. **Token Balance** - Primary cost control
3. **Rate Limiting** - Queue fairness (20/hour)

### Five Core Features:
1. ✅ Account verification (`check_account_balance`)
2. ✅ Cost estimation (`estimate_analysis_cost`)
3. ✅ Repository analysis (`analyze_repository`)
4. ✅ Job history (`get_job_history`)
5. ✅ Result retrieval (`retrieve_past_result`)

### Production-Ready:
- ✅ Proper error handling
- ✅ Rate limit compliance
- ✅ Status mapping
- ✅ Download link generation
- ✅ Comprehensive testing

---

## 🚀 Next Steps

1. **Deploy to production** using your chosen method
2. **Test end-to-end** with real users
3. **Monitor for issues** in first 24 hours
4. **Gather feedback** and iterate
5. **Consider MCP registry** submission

---

## 📞 Support

- API Documentation: `https://api.pvizgenerator.com/docs`
- MCP Protocol: `https://modelcontextprotocol.io`
- Issues: See `TROUBLESHOOTING.md`

---

**Total integration time: ~30 minutes**

**You're production-ready! 🎉**
