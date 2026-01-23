# pviz MCP Server - Quick Reference

One-page reference for common operations and commands.

---

## 🚀 Quick Start

```bash
# Install
pip install -r requirements.txt

# Configure
export PVIZ_JWT_TOKEN="your-token"
export PVIZ_API_URL="https://api.pvizgenerator.com"

# Run (STDIO mode)
python pviz_mcp_server.py

# Run (HTTP mode)
python pviz_mcp_http.py
```

---

## 🔧 Common MCP Tool Calls

### Check Account Balance
```
Check my pviz account balance
```
Returns: email, plan, token balance

### Estimate Cost
```
Estimate the cost for analyzing django/django
```
Returns: tokens_needed, SLOC, file_count

### Analyze Repository
```
Analyze https://github.com/django/django
```
Returns: Full analysis with metrics and dependencies

### Get Job History
```
Show my recent pviz analyses
```
Returns: List of recent jobs with status

### Retrieve Past Result
```
Get results from job job_abc123
```
Returns: Complete artifact from completed analysis

---

## 📊 Understanding Metrics

### Modularity Score (0-1)
- **0.0-0.3:** Low (monolithic)
- **0.4-0.7:** Medium
- **0.8-1.0:** High (well-separated)

**Higher is better** - indicates good separation of concerns

### Coupling Score (0-1)
- **0.0-0.3:** Low coupling (good)
- **0.4-0.7:** Medium coupling
- **0.8-1.0:** High coupling (tightly coupled)

**Lower is better** - indicates more independent components

---

## 💰 Token Costs

| Repository Size | Typical Cost |
|----------------|-------------|
| Small (<10K SLOC) | 50-150 tokens |
| Medium (10-50K) | 150-300 tokens |
| Large (50-100K) | 300-600 tokens |
| Very Large (100K+) | 600-1200 tokens |

**Tip:** Always use `estimate_analysis_cost` first!

---

## ⏱️ Typical Analysis Times

- **Small repos:** 30-90 seconds
- **Medium repos:** 2-5 minutes
- **Large repos:** 5-15 minutes

If longer than 30 minutes, check job status or contact support.

---

## 🔐 Private Repositories

### Create GitHub Token
```
GitHub → Settings → Developer settings 
→ Personal access tokens → Tokens (classic)
→ Generate new token
→ Select scope: "repo"
```

### Use in Analysis
```
Analyze https://github.com/myorg/private-repo 
with github token ghp_xxxxx
```

**Security:** Token is never stored, only used to clone.

---

## ⚠️ Common Errors

### 401 Unauthorized
**Fix:** Generate new JWT token from dashboard

### 402 Payment Required
**Fix:** Purchase more tokens

### 429 Too Many Requests
**Fix:** Wait for `retry_after` period (1-5 minutes)

### Repository not found
**Fix:** Check URL or provide GitHub token if private

---

## 📦 Supported Languages

✅ Python (`.py`)  
✅ TypeScript (`.ts`, `.tsx`)  
✅ JavaScript (`.js`, `.jsx`)  
✅ Java (`.java`)  
✅ Go (`.go`)
✅ Rust (`.rs`)

---

## 🔄 Job Status Values

- `queued_precheck` - Queued, awaiting validation
- `running` - Analysis in progress
- `completed` - Successfully completed
- `failed` - Error occurred
- `canceled` - User canceled

---

## 🌐 API Endpoints

### Production
```
API:  https://api.pvizgenerator.com
MCP:  https://mcp.pvizgenerator.com
Web:  https://pvizgenerator.com
```

### Health Check
```bash
curl https://api.pvizgenerator.com/health
```

---

## 🐳 Docker Quick Deploy

```bash
# Build
docker build -t pviz-mcp .

# Run
docker run -e PVIZ_JWT_TOKEN=xxx -p 8080:8080 pviz-mcp

# Health check
curl http://localhost:8080/health
```

---

## 📝 Claude Desktop Config

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

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

---

## 🔍 Debugging

### Check Environment
```bash
echo $PVIZ_JWT_TOKEN
echo $PVIZ_API_URL
python --version  # Must be 3.8+
```

### Test API Connection
```bash
curl https://api.pvizgenerator.com/auth/me \
  -H "Authorization: Bearer $PVIZ_JWT_TOKEN"
```

### View Logs (STDIO)
```bash
# Claude Desktop logs (macOS)
tail -f ~/Library/Logs/Claude/mcp*.log
```

### View Logs (HTTP)
```bash
# Server logs
docker logs -f pviz-mcp-server
```

---

## 📚 Documentation Links

- [Complete API Reference](API_ENDPOINTS.md)
- [MCP Tools Documentation](TOOLS_REFERENCE.md)
- [Usage Examples](EXAMPLES.md)
- [System Architecture](ARCHITECTURE.md)
- [FAQ](FAQ.md)
- [Setup Guide](SETUP.md)
- [Troubleshooting](TROUBLESHOOTING.md)

---

## 💡 Pro Tips

1. **Always estimate first** - Avoid surprise token usage
2. **Cache results** - Use `retrieve_past_result` instead of re-analyzing
3. **Use fine-grained tokens** - More secure for private repos
4. **Check rate limits** - Don't exceed 20 jobs/hour
5. **Monitor balance** - Check before large analyses

---

## 🆘 Support

- **Email:** mikemc@pvizgenerator.com
- **Docs:** https://docs.pvizgenerator.com
- **API Docs:** https://api.pvizgenerator.com/docs

---

**Last Updated:** January 2026  
**Version:** 2.0
