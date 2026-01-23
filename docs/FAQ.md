# pviz MCP Server - Frequently Asked Questions

Common questions and answers about the pviz MCP server.

---

## Table of Contents

1. [General](#general)
2. [Pricing & Tokens](#pricing--tokens)
3. [Repository Support](#repository-support)
4. [Private Repositories](#private-repositories)
5. [Analysis Capabilities](#analysis-capabilities)
6. [Limitations](#limitations)
7. [Integration](#integration)
8. [Troubleshooting](#troubleshooting)
9. [Security & Privacy](#security--privacy)

---

## General

### What is the pviz MCP server?

The pviz MCP server allows LLMs (like Claude, GPT, etc.) to analyze GitHub repositories for dependency structures, architectural patterns, and code metrics using the Model Context Protocol (MCP).

### What can I do with it?

- Analyze repository architecture
- Detect circular dependencies
- Compare multiple codebases
- Extract metrics (SLOC, complexity, coupling)
- Answer questions about code structure
- Generate architectural summaries

### Do I need a pviz account?

Yes. You need to sign up at https://pvizgenerator.com and obtain a JWT token to use the MCP server.

### Is it free?

The MCP server code is free and open-source (MIT license). However, using the pviz backend API requires tokens, which are consumed per analysis.

---

## Pricing & Tokens

### How much does an analysis cost?

Cost depends on repository size:

| Repository Size | Typical Token Cost |
|----------------|-------------------|
| Small (<10K SLOC) | 50-150 tokens |
| Medium (10-50K SLOC) | 150-300 tokens |
| Large (50-100K SLOC) | 300-600 tokens |
| Very Large (100K+ SLOC) | 600-1200 tokens |

Use `estimate_analysis_cost` to get exact estimates before analyzing.

### How do I get tokens?

1. **Free trial** - New accounts may include trial credits
2. **Purchase** - Buy token packages from the dashboard
3. **Subscription plans** - Monthly plans include token allocations

### Can I estimate cost before analyzing?

Yes! Use the `estimate_analysis_cost` tool:

```
Estimate the cost for analyzing django/django
```

This returns the exact token cost without consuming any tokens.

### Do tokens expire?

No, purchased tokens never expire. They remain in your account until used.

### What happens if I don't have enough tokens?

The API will return a `402 Payment Required` error with details:

```json
{
  "error": "INSUFFICIENT_TOKENS",
  "required": 250,
  "available": 42,
  "shortfall": 208
}
```

### Can I get a refund for failed analyses?

Failed analyses before processing starts (e.g., invalid repository) do not consume tokens. Analyses that fail during processing may consume partial tokens (no refund for partially completed work).

---

## Repository Support

### What programming languages are supported?

Currently supported:
- **Python** (`.py`)
- **TypeScript** (`.ts`, `.tsx`)
- **JavaScript** (`.js`, `.jsx`)
- **Java** (`.java`)
- **Go** (`.go`)
- **Rust** (`.rs`)

### What about other languages?

Languages like C++, Rust, Ruby, PHP, and C# are not currently supported but may be added in future releases.

### Can I analyze repositories with multiple languages?

Yes, but the analysis focuses on the **primary language** (the one with the most SLOC). Mixed-language repositories may have incomplete dependency graphs.

### What Git hosting platforms are supported?

Currently **GitHub.com only**. GitLab, Bitbucket, and GitHub Enterprise support is planned.

### Can I analyze private repositories?

Yes! You need to provide a GitHub Personal Access Token (PAT) with `repo` scope. See [Private Repositories](#private-repositories) below.

### What's the maximum repository size?

**Hard limits:**
- Maximum SLOC: 500,000 lines
- Maximum files: 10,000 files
- Analysis timeout: 30 minutes

Repositories exceeding these limits will fail with an error.

### Can I analyze monorepos?

Partially. Monorepos with a single primary language work well. Multi-language monorepos are analyzed for the primary language only.

### What about archived or deleted repositories?

Archived repositories can be analyzed (read-only access is sufficient). Deleted repositories will return an error.

---

## Private Repositories

### How do I analyze a private repository?

Provide a GitHub Personal Access Token (PAT):

```
Analyze https://github.com/myorg/private-repo 
with github token ghp_xxxxx
```

### How do I create a GitHub token?

1. Go to GitHub → Settings → Developer settings → Personal access tokens
2. Generate new token (classic)
3. Select scope: **repo** (full control of private repositories)
4. Copy the token (starts with `ghp_`)

### Is my GitHub token stored?

**No!** The token is:
- Transmitted over HTTPS only
- Used **only** to clone the repository
- **Never logged or stored** by pviz
- Destroyed immediately after cloning

### Can I use fine-grained tokens?

Yes! Fine-grained tokens with `Contents: Read-only` permission work. This is more secure than classic tokens.

### What if my token expires during analysis?

The token is only used during the initial clone (first 10-30 seconds). Expiration after that doesn't affect the analysis.

### Can pviz staff see my code?

No. Analysis happens in isolated, ephemeral containers that are destroyed after completion. Code is never persisted or logged.

---

## Analysis Capabilities

### What metrics are included in the analysis?

Standard metrics:
- **Total files** - Number of source files
- **Total SLOC** - Source lines of code
- **Total dependencies** - Import relationships
- **Avg dependencies per file**
- **Modularity score** (0-1, higher is better)
- **Coupling score** (0-1, lower is better)

### How is modularity calculated?

Modularity measures how well code is separated into independent modules. Higher scores indicate better separation of concerns.

**Formula:** Based on graph clustering algorithms (Louvain method).

**Interpretation:**
- 0.0-0.3: Low modularity (monolithic)
- 0.4-0.7: Medium modularity
- 0.8-1.0: High modularity (well-separated)

### What are circular dependencies?

Circular dependencies occur when modules import each other in a cycle:

```
A imports B
B imports C
C imports A  ← circular dependency
```

These can make code harder to test, refactor, and understand.

### Can I ask custom questions about the code?

Yes! Use the `questions` parameter:

```json
{
  "repo_url": "https://github.com/django/django",
  "questions": [
    "What architectural patterns are used?",
    "What are the main components?",
    "How is the database layer organized?"
  ]
}
```

**Note:** This feature requires LLM worker processing and may increase analysis time.

### How accurate is the analysis?

Accuracy depends on:
- **Static analysis only** - Dynamic imports may be missed
- **Language support** - Better for Python/TypeScript than others
- **Code complexity** - Complex metaprogramming may be incomplete

Typical accuracy: **90-95%** for supported languages.

---

## Limitations

### What are the current limitations?

**Technical:**
- Static analysis only (no code execution)
- Primary language analysis in multi-language repos
- No support for dynamic imports (e.g., `importlib`, `require()` with variables)
- No analysis of compiled/minified code

**Platform:**
- GitHub.com only (no GitLab, Bitbucket, GitHub Enterprise)
- No webhook support (must poll for status)
- 1-hour presigned URL expiration

**Size:**
- Max 500K SLOC
- Max 10K files
- 30-minute timeout

### Can I analyze a local repository?

Not directly. The repository must be hosted on GitHub. You can:
1. Push to a private GitHub repository
2. Analyze with a GitHub token

### Does it work with Git submodules?

Submodules are **not** analyzed. Only the main repository is processed.

### What about vendored dependencies?

Vendored (committed) dependencies are analyzed as part of the codebase. This may inflate SLOC counts and metrics.

**Recommendation:** Use `.gitignore` or exclude patterns to ignore vendor directories.

---

## Integration

### How do I integrate with Claude Desktop?

Edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pviz": {
      "command": "python",
      "args": ["/path/to/pviz_mcp_server.py"],
      "env": {
        "PVIZ_JWT_TOKEN": "your-token",
        "PVIZ_API_URL": "https://api.pvizgenerator.com"
      }
    }
  }
}
```

Restart Claude Desktop.

### Can I use it with GPT or other LLMs?

Yes! Any MCP-compatible LLM client can use the pviz server. HTTP mode (`pviz_mcp_http.py`) works with most clients.

### Can I deploy it to the cloud?

Yes! Supported platforms:
- Google Cloud Run
- AWS ECS / Fargate
- Fly.io
- Any Docker-compatible host

See [SETUP.md](SETUP.md) for deployment guides.

### Can I run multiple instances?

Yes, the MCP server is stateless and can scale horizontally. Use a load balancer to distribute traffic.

---

## Troubleshooting

### "PVIZ_JWT_TOKEN not set" error

**Solution:**
```bash
export PVIZ_JWT_TOKEN="your-token-here"
```

Or set it in your MCP client configuration.

### "401 Unauthorized" error

**Causes:**
- Invalid token
- Token belongs to deleted account
- Expired session (rare)

**Solution:** Generate a new token from the dashboard.

### "402 Payment Required" error

**Cause:** Insufficient tokens in account.

**Solution:** Purchase more tokens or use trial credits.

### "429 Too Many Requests" error

**Cause:** Rate limit exceeded.

**Solution:** Wait for the `retry_after` period (usually 1-5 minutes).

### Analysis is taking too long

**Expected times:**
- Small repos: 30-90 seconds
- Medium repos: 2-5 minutes
- Large repos: 5-15 minutes

If longer than expected:
1. Check job status: `get_analysis_status(job_id)`
2. Verify repository is accessible
3. Contact support if stuck >30 minutes

### "Repository not found" error

**Causes:**
- Repository doesn't exist
- Repository is private (need GitHub token)
- Typo in repository URL

**Solution:** Double-check URL and provide GitHub token if private.

### MCP server not appearing in Claude

**Checklist:**
- ✅ Correct config file path
- ✅ Valid JSON syntax
- ✅ Absolute path to script
- ✅ Python is in PATH
- ✅ Restart Claude Desktop

**Debug:** Check Claude logs:
```bash
tail -f ~/Library/Logs/Claude/mcp*.log
```

---

## Security & Privacy

### Is my code private?

Yes. Your code is:
- ✅ Processed in isolated containers
- ✅ Never stored or persisted
- ✅ Destroyed after analysis completes
- ✅ Not accessible to pviz staff
- ✅ Not used for training or other purposes

### What data is stored?

**Stored:**
- Job metadata (repo URL, status, timestamps)
- Analysis artifacts (dependency graphs, metrics)
- Account information (email, plan, token balance)

**NOT stored:**
- Source code
- GitHub tokens
- Intermediate analysis data

### How long are artifacts retained?

Artifacts are retained for **90 days** by default. After that, they're automatically deleted. You can download artifacts anytime during the retention period.

### Can I delete my data?

Yes. Contact support to request account deletion. This will:
- Delete all analysis artifacts
- Remove account information
- Invalidate all tokens

### Is communication encrypted?

Yes. All communication uses HTTPS (TLS 1.2+):
- MCP server ↔ API: HTTPS
- API ↔ S3: HTTPS
- Client ↔ MCP server: HTTPS (in HTTP mode)

### What permissions does the GitHub token need?

**Minimum:** `repo` scope (full control of private repositories)

**Recommended:** Fine-grained token with `Contents: Read-only` on specific repositories.

The token is **only** used to clone the repository.

---

## Still Have Questions?

### Documentation

- [API Reference](API_ENDPOINTS.md)
- [Tools Reference](TOOLS_REFERENCE.md)
- [Examples](EXAMPLES.md)
- [Architecture](ARCHITECTURE.md)
- [Setup Guide](SETUP.md)
- [Troubleshooting](TROUBLESHOOTING.md)

### Support

- **Email:** mikemc@pvizgenerator.com
- **Documentation:** https://docs.pvizgenerator.com
- **API Docs:** https://api.pvizgenerator.com/docs
- **MCP Protocol:** https://modelcontextprotocol.io

### Community

- GitHub Issues: For MCP server bugs and feature requests
- Feature Requests: Via email or dashboard feedback

---

**Last Updated:** January 2026
