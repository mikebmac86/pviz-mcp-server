FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY pviz_mcp_server.py .
COPY pviz_mcp_http.py .
COPY api_adapter.py .

ENV PORT=8080
ENV HOST=0.0.0.0
ENV PVIZ_API_URL=https://api.pvizgenerator.com
ENV LOG_LEVEL=info

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "pviz_mcp_http:app", "--host", "0.0.0.0", "--port", "8080"]
