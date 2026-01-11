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

### 3. Test the Server

```bash
# Run in STDIO mode (for Claude Desktop integration)
python pviz_mcp_server.py
```

---

## Integration with Claude Desktop (Local)

### 1. Configure Claude Desktop

Edit your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add this configuration:

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

After saving the config, restart Claude Desktop. You should see pviz tools available in the chat.

### 3. Test It

Try asking Claude:

```
"Analyze https://github.com/django/django using pviz and tell me about its architecture"
```

---

## Cloud Deployment Options

### Option 1: AWS (Recommended for your stack)

#### A. AWS Lambda + API Gateway

1. **Package the Lambda**

```bash
# Create deployment package
pip install -r requirements.txt -t package/
cp pviz_mcp_server.py package/

cd package
zip -r ../pviz-mcp-lambda.zip .
cd ..
zip -g pviz-mcp-lambda.zip pviz_mcp_server.py
```

2. **Deploy to Lambda**

```bash
aws lambda create-function \
  --function-name pviz-mcp-server \
  --runtime python3.11 \
  --handler pviz_mcp_http.app \
  --zip-file fileb://pviz-mcp-lambda.zip \
  --role arn:aws:iam::YOUR-ACCOUNT:role/lambda-execution-role \
  --timeout 300 \
  --memory-size 512 \
  --environment Variables="{PVIZ_API_URL=https://api.pvizgenerator.com}"
```

3. **Create API Gateway**

- Use HTTP API (cheaper, faster)
- Route: `POST /mcp` → Lambda function
- Enable CORS if needed

#### B. AWS ECS (Fargate) - Better for long-running tasks

1. **Build and Push Docker Image**

```bash
# Build
docker build -t pviz-mcp-server .

# Tag for ECR
docker tag pviz-mcp-server:latest YOUR-ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pviz-mcp-server:latest

# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin YOUR-ACCOUNT.dkr.ecr.us-east-1.amazonaws.com
docker push YOUR-ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pviz-mcp-server:latest
```

2. **Create ECS Task Definition**

```json
{
  "family": "pviz-mcp-server",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "containerDefinitions": [
    {
      "name": "pviz-mcp",
      "image": "YOUR-ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pviz-mcp-server:latest",
      "portMappings": [
        {
          "containerPort": 8080,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "PVIZ_API_URL",
          "value": "https://api.pvizgenerator.com"
        }
      ],
      "secrets": [
        {
          "name": "PVIZ_JWT_TOKEN",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:YOUR-ACCOUNT:secret:pviz/jwt-token"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pviz-mcp-server",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

3. **Deploy with Load Balancer**

- Application Load Balancer
- Target port: 8080
- Health check path: `/health`

---

### Option 2: Google Cloud Run (Easiest)

```bash
# Build and deploy in one command
gcloud run deploy pviz-mcp-server \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars PVIZ_API_URL=https://api.pvizgenerator.com \
  --set-secrets PVIZ_JWT_TOKEN=pviz-jwt-token:latest
```

Cloud Run will automatically:
- Build your Docker image
- Deploy it
- Give you an HTTPS endpoint
- Auto-scale to zero when not in use

---

### Option 3: Fly.io (Developer-Friendly)

1. **Install Fly CLI**

```bash
curl -L https://fly.io/install.sh | sh
```

2. **Deploy**

```bash
fly launch
fly secrets set PVIZ_JWT_TOKEN=your-token-here
fly deploy
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PVIZ_JWT_TOKEN` | ✅ Yes | - | JWT token for api.pvizgenerator.com authentication |
| `PVIZ_API_URL` | No | `https://api.pvizgenerator.com` | Your FastAPI backend URL |
| `PVIZ_POLL_INTERVAL` | No | `5` | Seconds between status polls |
| `PVIZ_MAX_POLL_ATTEMPTS` | No | `60` | Max polling attempts (5 min default) |
| `PORT` | No | `8080` | HTTP server port |
| `HOST` | No | `0.0.0.0` | HTTP server host |
| `LOG_LEVEL` | No | `info` | Logging level |
| `DEBUG` | No | `false` | Enable debug mode |
| `CORS_ORIGINS` | No | `*` | Comma-separated CORS origins |

---

## Testing Your Deployment

### 1. Test Health Check

```bash
curl https://your-mcp-server.com/health
```

Expected response:
```json
{"status": "healthy", "service": "pviz-mcp-server", "version": "1.0.0"}
```

### 2. Test MCP Protocol

```bash
curl -X POST https://your-mcp-server.com/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {}
  }'
```

### 3. Test with Claude

Configure your Claude Desktop to use the HTTP endpoint:

```json
{
  "mcpServers": {
    "pviz": {
      "transport": "http",
      "url": "https://your-mcp-server.com/mcp",
      "headers": {
        "X-API-Key": "optional-api-key-if-you-add-auth"
      }
    }
  }
}
```

---

## Monitoring & Logs

### CloudWatch Logs (AWS)

```bash
aws logs tail /ecs/pviz-mcp-server --follow
```

### View Container Logs (Cloud Run)

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=pviz-mcp-server" --limit 50 --format json
```

---

## Security Best Practices

### 1. Store JWT Tokens Securely

**AWS**: Use AWS Secrets Manager
```bash
aws secretsmanager create-secret \
  --name pviz/jwt-token \
  --secret-string "your-jwt-token"
```

**GCP**: Use Secret Manager
```bash
echo -n "your-jwt-token" | gcloud secrets create pviz-jwt-token --data-file=-
```

### 2. Enable HTTPS Only

All cloud platforms (AWS, GCP, Fly.io) provide HTTPS by default. Never expose HTTP in production.

---

## Troubleshooting

### "PVIZ_JWT_TOKEN not set"

Make sure you've set the environment variable:
```bash
export PVIZ_JWT_TOKEN="your-token"
```

### "Connection timeout" errors

Increase polling timeout:
```bash
export PVIZ_MAX_POLL_ATTEMPTS=120  # 10 minutes
```

### Claude Desktop can't find the server

1. Check config file path is correct
2. Verify Python path in command: `which python`
3. Check Claude Desktop logs (Help → Show Logs)

### HTTP server not responding

1. Check port isn't blocked: `netstat -an | grep 8080`
2. Verify firewall rules
3. Check container logs

---

## Next Steps

1. ✅ Get MCP server running locally
2. ✅ Test with Claude Desktop
3. ✅ Deploy to cloud
4. 📝 Submit to MCP Registry: https://github.com/modelcontextprotocol/servers
5. 🎯 Contact Anthropic about partnership

---

## Support

- MCP Documentation: https://modelcontextprotocol.io
- Anthropic Discord: Join for MCP discussions
- Your API Docs: https://api.pvizgenerator.com/docs
