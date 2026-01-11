# Troubleshooting Guide - pviz MCP Server

Common issues and how to fix them.

---

## 🔴 Installation Issues

### Error: "mcp module not found"

```bash
ModuleNotFoundError: No module named 'mcp'
```

**Fix:**
```bash
pip install mcp>=1.0.0
pip install -r requirements.txt
```

---

### Error: "Python version too old"

```bash
ERROR: Python 3.8 or higher required
```

**Fix:**
```bash
python3 --version
pyenv install 3.11
pyenv local 3.11
```

---

## 🔴 Configuration Issues

### Error: "PVIZ_JWT_TOKEN not set"

```bash
PvizAPIError: PVIZ_JWT_TOKEN environment variable not set
```

**Fix options:**

```bash
export PVIZ_JWT_TOKEN="your-token-here"
```

Or via Claude Desktop config:
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

```bash
401 Unauthorized
```

**Fix:**
- Generate a new token in the pviz dashboard
- Update environment variables
- Restart MCP server

---

### Error: "Insufficient credits"

```bash
402 Payment Required
```

**Fix:**
- Purchase credits in dashboard
- Retry analysis

---

## 🔴 Claude Desktop Issues

### pviz tools not visible

Checklist:
- Correct config path
- Valid JSON
- Absolute path to server
- Claude restarted

Logs:
```bash
tail -f ~/Library/Logs/Claude/mcp*.log
```

---

### Error: "Server process exited"

**Fix:**
```bash
which python
python --version
python pviz_mcp_server.py
```

---

## 🔴 API Issues

### Connection timeout

```bash
httpx.ConnectTimeout
```

**Fix:**
```bash
curl https://api.pvizgenerator.com/health
export PVIZ_MAX_POLL_ATTEMPTS=120
```

---

### SSL verification failed

```bash
ssl.SSLError
```

**Fix:**
```bash
pip install --upgrade certifi
```

---

## 🔴 Docker Issues

### Container exits immediately

```bash
docker logs <container>
docker run -it pviz-mcp /bin/bash
```

---

## 🔴 Cloud Issues

### Cloud Run 502

```bash
gcloud logging read "resource.type=cloud_run_revision" --limit 50
```

---

## 🔴 Data Issues

### S3 URL expired

- Presigned URLs expire after ~1 hour
- Re-query job status for a new link or download on the pviz platform

---

## 🛠 Debug Checklist

```bash
python3 --version
python3 -c "import mcp, httpx"
echo $PVIZ_JWT_TOKEN
curl https://api.pvizgenerator.com/health
python pviz_mcp_server.py
```

---

## 📞 Getting Help

- API Docs: https://api.pvizgenerator.com/docs
- MCP: https://modelcontextprotocol.io
- Support: mikemc@pvizgenerator.com

---

**Most issues are configuration-related. Check tokens, paths, and environment variables first.**
