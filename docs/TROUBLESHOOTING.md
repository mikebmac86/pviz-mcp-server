# Troubleshooting Guide - pviz MCP Server

Common issues and how to fix them.

---

## 🔴 Installation Issues

### Error: "mcp module not found"

**Problem:**
```bash
ModuleNotFoundError: No module named 'mcp'
```

**Solution:**
```bash
pip install mcp>=1.0.0
# Or install all requirements:
pip install -r requirements.txt
```

---

### Error: "Python version too old"

**Problem:**
```bash
ERROR: Python 3.8 or higher required
```

**Solution:**
```bash
# Check your Python version
python3 --version

# Install Python 3.8+ or use pyenv
pyenv install 3.11
pyenv local 3.11
```

---

## 🔴 Configuration Issues

### Error: "PVIZ_JWT_TOKEN not set"

**Problem:**
```bash
PvizAPIError: PVIZ_JWT_TOKEN environment variable not set
```

**Solution:**

**Option 1: Set environment variable**
```bash
export PVIZ_JWT_TOKEN="your-token-here"
```

**Option 2: Use .env file**
```bash
# Copy template
cp .env.example .env

# Edit .env and add your token
nano .env
```

**Option 3: Set in Claude Desktop config**
```json
{
  "mcpServers": {
    "pviz": {
      "env": {
        "PVIZ_JWT_TOKEN": "your-token-here"
      }
    }
  }
}
```

---

### Error: "Invalid or expired token"

**Problem:**
```bash
401 Unauthorized: Invalid token
```

**Solution:**
1. Go to https://pvizgenerator.com/dashboard/api-keys
2. Generate a new JWT token
3. Update your environment variable
4. Restart the MCP server

---

### Error: "Insufficient credits"

**Problem:**
```bash
402 Payment Required: Insufficient credits (0 remaining)
```

**Solution:**
1. Go to https://pvizgenerator.com/dashboard/credits
2. Purchase more credits
3. Try your analysis again

---

## 🔴 Claude Desktop Integration Issues

### Claude Desktop doesn't show pviz tools

**Problem:**
MCP server configured but tools don't appear in Claude.

**Checklist:**
1. ✅ Config file in correct location?
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

2. ✅ JSON syntax valid?
   ```bash
   # Test JSON validity
   python -m json.tool < claude_desktop_config.json
   ```

3. ✅ Path to server correct?
   ```json
   {
     "mcpServers": {
       "pviz": {
         "command": "python",
         "args": ["/absolute/path/to/pviz_mcp_server.py"]
       }
     }
   }
   ```

4. ✅ Claude Desktop restarted?
   - Completely quit Claude Desktop (not just close window)
   - Reopen and check again

**Debug steps:**
```bash
# Test the server directly
python pviz_mcp_server.py

# Check Claude Desktop logs
# macOS:
tail -f ~/Library/Logs/Claude/mcp*.log

# Windows:
type %LOCALAPPDATA%\Claude\logs\mcp*.log
```

---

### Error: "Server process exited"

**Problem:**
```
MCP server 'pviz' process exited with code 1
```

**Solution:**

**Check Python path:**
```bash
# Make sure 'python' is the right command
which python
python --version

# If not, use full path:
{
  "command": "/usr/local/bin/python3",
  "args": ["/path/to/pviz_mcp_server.py"]
}
```

**Check dependencies:**
```bash
# Activate same Python environment
python -c "import mcp; import httpx; print('OK')"
```

**Check for errors:**
```bash
# Run server manually to see errors
python pviz_mcp_server.py
```

---

## 🔴 API Connection Issues

### Error: "Connection timeout"

**Problem:**
```bash
httpx.ConnectTimeout: Connection timeout after 30s
```

**Solution:**

**Check API is reachable:**
```bash
curl https://api.pvizgenerator.com/health
```

**Check firewall/proxy:**
```bash
# Test with explicit proxy
export HTTPS_PROXY=http://your-proxy:8080
python pviz_mcp_server.py
```

**Increase timeout:**
```bash
export PVIZ_MAX_POLL_ATTEMPTS=120  # 10 minutes
```

---

### Error: "SSL certificate verify failed"

**Problem:**
```bash
ssl.SSLError: certificate verify failed
```

**Solution:**

**Option 1: Update CA certificates**
```bash
pip install --upgrade certifi
```

**Option 2: Corporate proxy with self-signed cert**
```bash
# NOT recommended for production
export REQUESTS_CA_BUNDLE=/path/to/your/cert.pem
```

---

### Error: "Analysis taking too long"

**Problem:**
Job stays in "processing" state for 10+ minutes.

**Possible causes:**
1. Large repository (>1000 files)
2. API queue is busy
3. Job actually failed but status not updated

**Solution:**
```bash
# Increase max polling time
export PVIZ_MAX_POLL_ATTEMPTS=240  # 20 minutes

# Or check status manually
python -c "
import asyncio
from api_adapter import PvizAPIAdapter
import httpx

async def check():
    adapter = PvizAPIAdapter('https://api.pvizgenerator.com', 'YOUR_TOKEN')
    async with httpx.AsyncClient() as client:
        status = await adapter.get_job_status(client, 'JOB_ID')
        print(status)

asyncio.run(check())
"
```

---

## 🔴 Docker Issues

### Error: "Docker build fails"

**Problem:**
```bash
ERROR: failed to solve: failed to compute cache key
```

**Solution:**

**Make sure all files are present:**
```bash
# Check required files exist
ls -la pviz_mcp_server.py api_adapter.py requirements.txt Dockerfile
```

**Build with no cache:**
```bash
docker build --no-cache -t pviz-mcp .
```

---

### Error: "Container exits immediately"

**Problem:**
```bash
docker run pviz-mcp
# Container starts and stops
```

**Solution:**

**Check logs:**
```bash
docker logs <container-id>
```

**Check environment variables:**
```bash
docker run -e PVIZ_JWT_TOKEN=your-token pviz-mcp
```

---

## 🔴 Cloud Deployment Issues

### AWS Lambda: "Function timeout"

**Problem:**
Lambda times out after 3 seconds.

**Solution:**
```bash
# Increase Lambda timeout
aws lambda update-function-configuration \
  --function-name pviz-mcp-server \
  --timeout 300  # 5 minutes

# Increase memory (gives more CPU)
aws lambda update-function-configuration \
  --function-name pviz-mcp-server \
  --memory-size 1024
```

---

### Cloud Run: "502 Bad Gateway"

**Problem:**
```bash
Error: Service Unavailable
```

**Solution:**

**Check service logs:**
```bash
gcloud logging read "resource.type=cloud_run_revision" --limit 50
```

**Increase timeout:**
```bash
gcloud run services update pviz-mcp-server \
  --timeout=300 \
  --region=us-central1
```

**Check health endpoint:**
```bash
curl https://your-service-url/health
```

---

## 🔴 Performance Issues

### Analysis is very slow

**Problem:**
Simple repositories take 5+ minutes to analyze.

**Possible causes:**
1. Large repository
2. API queue is backed up
3. Slow S3 download
4. Network latency

**Solutions:**

**Use metrics-only mode for quick checks:**
```python
# Don't download full graph
get_repository_metrics(repo_url, wait_for_completion=True)
```

**Check if it's the API or MCP:**
```bash
# Time the API directly
time curl -X POST https://api.pvizgenerator.com/v1/analyze \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"repo_url": "https://github.com/simple/repo"}'
```

**Use async mode:**
```python
# Don't wait for completion
analyze_repository(repo_url, wait_for_completion=False)
# Returns job_id immediately, check later
get_analysis_status(job_id)
```

---

## 🔴 Data Issues

### S3 URL returns 403 Forbidden

**Problem:**
```bash
403 Forbidden when downloading from S3 URL
```

**Solution:**

**URL might have expired:**
Presigned URLs typically expire after 1 hour. Re-check the status to get a fresh URL:

```python
status = await get_analysis_status(job_id)
s3_url = status['s3_url']  # Fresh URL
```

---

### Dependency graph looks incomplete

**Problem:**
Analysis returned but graph has fewer modules than expected.

**Possible causes:**
1. Language filtering excluded files
2. Parse errors on some files
3. Gitignore excluded files

**Solution:**

**Check the analysis logs:**
```json
{
  "summary": {
    "total_files": 100,
    "parsed_successfully": 95,
    "parse_errors": 5
  },
  "parse_errors": [
    {
      "file": "src/weird.js",
      "error": "Syntax error"
    }
  ]
}
```

**Try without language filter:**
```python
# Analyze all languages
analyze_repository(repo_url, languages=None)
```

---

## 🔴 Common Mistakes

### Mistake 1: Relative paths in Claude config

**Wrong:**
```json
{
  "args": ["./pviz_mcp_server.py"]  // ❌ Relative path
}
```

**Correct:**
```json
{
  "args": ["/Users/you/pviz-mcp/pviz_mcp_server.py"]  // ✅ Absolute path
}
```

---

### Mistake 2: Committing .env to git

**Wrong:**
```bash
git add .env  # ❌ Contains secrets!
git commit
```

**Correct:**
```bash
# .gitignore already excludes .env
# Use .env.example as template
cp .env.example .env
# Add your secrets to .env
# .env never gets committed
```

---

### Mistake 3: Using wrong Python

**Wrong:**
```bash
pip install mcp  # ❌ Installs to system Python
python3.11 pviz_mcp_server.py  # ❌ Runs with different Python
```

**Correct:**
```bash
# Use same Python throughout
python3.11 -m pip install mcp
python3.11 pviz_mcp_server.py
```

---

## 🛠️ Debug Checklist

When something isn't working, run through this checklist:

```bash
# 1. Check Python version
python3 --version  # Should be 3.8+

# 2. Check dependencies
python3 -c "import mcp; import httpx; print('✅ Dependencies OK')"

# 3. Check environment
echo $PVIZ_JWT_TOKEN  # Should print your token

# 4. Test API connection
curl https://api.pvizgenerator.com/health

# 5. Test API auth
curl -H "Authorization: Bearer $PVIZ_JWT_TOKEN" \
  https://api.pvizgenerator.com/v1/user/credits

# 6. Test MCP server
python3 pviz_mcp_server.py

# 7. Check logs
tail -f /var/log/pviz-mcp.log
```

---

## 📞 Getting Help

Still stuck? Here's where to get help:

1. **Check API documentation:**
   - https://api.pvizgenerator.com/docs

2. **Search existing issues:**
   - GitHub: https://github.com/your-username/pviz-mcp-server/issues

3. **Ask on Discord:**
   - MCP Community: https://discord.gg/modelcontextprotocol
   - Anthropic: https://discord.gg/anthropic

4. **Open a GitHub issue:**
   - Include: Error message, logs, environment details

5. **Email support:**
   - support@pvizgenerator.com

---

## 🔍 Useful Debug Commands

```bash
# View all environment variables
printenv | grep PVIZ

# Test API adapter
python api_adapter.py

# Run quick start checks
python quick_start.py

# Test with verbose logging
LOG_LEVEL=debug python pviz_mcp_server.py

# Check disk space (S3 downloads can be large)
df -h

# Check network connectivity
ping api.pvizgenerator.com
traceroute api.pvizgenerator.com

# Verify JSON syntax
python -m json.tool < mcp.json

# Check file permissions
ls -la pviz_mcp_server.py
```

---

## ✅ Preventive Measures

Avoid issues before they happen:

1. **Always use virtual environments:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Keep dependencies updated:**
   ```bash
   pip install --upgrade -r requirements.txt
   ```

3. **Test before deploying:**
   ```bash
   python quick_start.py
   python api_adapter.py
   ```

4. **Monitor your credits:**
   - Set up low-balance alerts
   - Check dashboard weekly

5. **Set reasonable timeouts:**
   - Don't set MAX_POLL_ATTEMPTS too high
   - Balance between waiting and failing fast

---

**Remember:** Most issues are configuration problems. Double-check your environment variables, paths, and API tokens before diving deeper!
