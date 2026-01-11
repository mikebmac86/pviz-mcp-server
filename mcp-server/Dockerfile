FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY pviz_mcp_server.py .
COPY pviz_mcp_http.py .
COPY api_adapter.py .

# Environment variables (override these at runtime)
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PVIZ_API_URL=https://api.pvizgenerator.com
ENV LOG_LEVEL=info

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the HTTP server
CMD ["python", "pviz_mcp_http.py"]
