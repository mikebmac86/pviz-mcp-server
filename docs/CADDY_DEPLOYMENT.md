# Docker Compose + Caddy Deployment

Quick guide for adding the MCP server to your existing Docker Compose stack with Caddy.

---

## 🎯 Your Current Stack

```
api (FastAPI)      → Port 8000
worker             → Background jobs
worker_llm         → LLM processing
worker_llm_export  → Export processing
caddy              → Reverse proxy (80/443)
```

---

## ➕ Adding MCP Server (15 Minutes)

### Step 1: Create MCP Directory (2 min)

```bash
cd /path/to/your/project

mkdir -p mcp-server
cp api_adapter.py mcp-server/
cp pviz_mcp_server.py mcp-server/
cp requirements.txt mcp-server/
cp Dockerfile mcp-server/
```

---

### Step 2: Add JWT Token Secret (2 min)

```bash
mkdir -p secrets
echo "your-jwt-token" > secrets/mcp_jwt_token.txt
chmod 600 secrets/mcp_jwt_token.txt

# Add to .gitignore
echo "secrets/" >> .gitignore
```

---

### Step 3: Update docker-compose.yml (3 min)

Add this service:

```yaml
services:
  # ... existing services ...

  pviz-mcp-server:
    build:
      context: ./mcp-server
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      PVIZ_API_URL: http://api:8000  # Internal Docker network
      PORT: 8080
      HOST: 0.0.0.0
    secrets:
      - mcp_jwt_token
    expose:
      - "8080"
    depends_on:
      - api
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  caddy:
    # ... existing config ...
    depends_on:
      - api
      - worker
      - worker_llm
      - worker_llm_export
      - pviz-mcp-server  # ADD THIS

secrets:
  mcp_jwt_token:
    file: ./secrets/mcp_jwt_token.txt
```

---

### Step 4: Configure Caddy (3 min)

**Option A: Separate Subdomain (Recommended)**

Create `Caddyfile.mcp`:
```caddyfile
mcp.pvizgenerator.com {
    reverse_proxy pviz-mcp-server:8080 {
        transport http {
            response_header_timeout 300s
            read_timeout 300s
        }
        health_uri /health
        health_interval 30s
    }
}
```

Update main `Caddyfile`:
```caddyfile
# At the top
import /etc/caddy/Caddyfile.mcp
```

**Option B: Add to Existing Domain**

Add to your `api.pvizgenerator.com` block:
```caddyfile
api.pvizgenerator.com {
    # ... existing handlers ...
    
    handle /mcp* {
        reverse_proxy pviz-mcp-server:8080
    }
}
```

---

### Step 5: Deploy (5 min)

```bash
# Build MCP server
docker-compose build pviz-mcp-server

# Start it
docker-compose up -d pviz-mcp-server

# Check logs
docker-compose logs -f pviz-mcp-server

# Restart Caddy
docker-compose restart caddy
```

---

## ✅ Verify Deployment

```bash
# 1. Health check (internal)
curl http://localhost:8080/health
# {"status": "healthy"}

# 2. Health check (via Caddy - subdomain)
curl https://mcp.pvizgenerator.com/health

# 3. Health check (via Caddy - path)
curl https://api.pvizgenerator.com/mcp/health

# 4. Check MCP endpoint
curl -X POST https://mcp.pvizgenerator.com/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 📁 Final File Structure

```
your-project/
├── docker-compose.yml          # Updated
├── Caddyfile                   # Updated (imports Caddyfile.mcp)
├── Caddyfile.mcp              # NEW
├── secrets/
│   └── mcp_jwt_token.txt      # NEW
├── mcp-server/                 # NEW
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── api_adapter.py
│   └── pviz_mcp_server.py
├── api/                        # Existing
├── workers/                    # Existing
└── ...
```

---

## 🔧 Configuration Options

### Internal Network (Recommended)
```yaml
environment:
  PVIZ_API_URL: http://api:8000  # Fast, no external network
```

### External URL
```yaml
environment:
  PVIZ_API_URL: https://api.pvizgenerator.com  # Works but slower
```

---

## 📊 Monitoring

### View Logs
```bash
# MCP server
docker-compose logs -f pviz-mcp-server

# Caddy access logs (if configured)
docker-compose exec caddy tail -f /var/log/caddy/mcp-access.log

# All services
docker-compose logs -f
```

### Check Status
```bash
# Service status
docker-compose ps pviz-mcp-server

# Health via HTTP
curl http://localhost:8080/health
```

---

## 🐛 Troubleshooting

### MCP server won't start
```bash
# Check logs
docker-compose logs pviz-mcp-server

# Common issues:
# - Missing JWT token
# - Can't connect to api:8000
# - Port 8080 already in use
```

### Can't connect to API
```bash
# Test from MCP container
docker-compose exec pviz-mcp-server curl http://api:8000/health

# Should return API health response
```

### 401 Unauthorized
```bash
# Check JWT token
docker-compose exec pviz-mcp-server cat /run/secrets/mcp_jwt_token

# Test token
curl https://api.pvizgenerator.com/auth/me \
  -H "Authorization: Bearer $(cat secrets/mcp_jwt_token.txt)"
```

### Caddy can't reach MCP
```bash
# Test from Caddy container
docker-compose exec caddy curl http://pviz-mcp-server:8080/health

# Check depends_on in docker-compose.yml
```

---

## 🔄 Updates & Maintenance

### Update MCP Server Code
```bash
# Stop service
docker-compose stop pviz-mcp-server

# Update code in mcp-server/ directory
# ...

# Rebuild and restart
docker-compose build pviz-mcp-server
docker-compose up -d pviz-mcp-server
```

### Rotate JWT Token
```bash
# Update token file
echo "new-token" > secrets/mcp_jwt_token.txt

# Restart MCP server
docker-compose restart pviz-mcp-server
```

---

## 🚀 Using with Claude Desktop

Once deployed, configure Claude Desktop:

```json
{
  "mcpServers": {
    "pviz": {
      "url": "https://mcp.pvizgenerator.com/mcp",
      "headers": {
        "Authorization": "Bearer your-user-jwt-token"
      }
    }
  }
}
```

**Note:** User token (for Claude) is different from service token (for MCP server).

---

## ✅ Production Checklist

- [ ] JWT token secured (`chmod 600`)
- [ ] `secrets/` in `.gitignore`
- [ ] Health checks passing
- [ ] Caddy HTTPS working
- [ ] MCP endpoint accessible
- [ ] Tested from Claude Desktop
- [ ] Logs accessible
- [ ] Monitoring configured

---

**Deployment time: ~15 minutes**

**Your MCP server is now integrated! 🎉**
