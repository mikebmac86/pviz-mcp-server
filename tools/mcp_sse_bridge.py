# tools/mcp_sse_bridge.py
"""
MCP stdio <-> MCP remote (HTTP/SSE) bridge for Claude Desktop.

Remote MCP (PViz):
  - SSE:      GET  {MCP_REMOTE_URL}/sse
  - Messages: POST {origin}{endpoint_from_sse}

Key behavior:
  - Claude talks stdio (Content-Length framed JSON-RPC).
  - Remote talks SSE + POST with a session-bound endpoint.
  - POST usually returns 202 Accepted; real JSON-RPC responses arrive over SSE.

Env vars:
  - MCP_REMOTE_URL: base remote MCP path (default: https://mcp.pvizgenerator.com/mcp)
  - PVIZ_JWT_TOKEN: bearer token for Authorization (optional)
  - MCP_HTTP_TIMEOUT_S: http timeout seconds (default: 60)
  - MCP_SSE_RECONNECT_S: reconnect delay seconds (default: 1.5)
  - MCP_HTTP2: "1" to enable http2 (default: 0)
  - MCP_DEBUG: "1" to enable verbose stderr logs (default: 0)

Dependencies:
  - pip install httpx
  - optional for http2: pip install "httpx[http2]"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

print("[pviz-bridge] BOOT (stderr visible)", file=sys.stderr, flush=True)
print("[pviz-bridge] ENV MCP_DEBUG=", os.getenv("MCP_DEBUG"), file=sys.stderr, flush=True)
print("[pviz-bridge] ENV MCP_REMOTE_URL=", os.getenv("MCP_REMOTE_URL"), file=sys.stderr, flush=True)


# ---------------------------
# Debug logging (stderr only)
# ---------------------------

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


DEBUG = _bool_env("MCP_DEBUG", False)


def _log(*parts: object) -> None:
    if not DEBUG:
        return
    try:
        print("[pviz-bridge]", *parts, file=sys.stderr, flush=True)
    except Exception:
        pass


# ---------------------------
# Stdio framing (MCP/LSP-style)
# ---------------------------

def _read_headers(stdin) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    while True:
        line = stdin.readline()
        if not line:
            return headers
        s = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if s == "":
            break
        if ":" in s:
            k, v = s.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def stdio_read_message(stdin_buf) -> Optional[Dict[str, Any]]:
    """
    Reads one MCP JSON-RPC message from stdin using Content-Length framing.
    Returns None on EOF.
    """
    headers = _read_headers(stdin_buf)
    if not headers:
        return None

    cl_raw = headers.get("content-length")
    if not cl_raw:
        _log("stdin: missing content-length header:", headers)
        return None

    try:
        content_length = int(cl_raw)
    except ValueError:
        _log("stdin: invalid content-length:", cl_raw)
        return None

    body = stdin_buf.read(content_length)
    if not body:
        return None

    try:
        msg = json.loads(body.decode("utf-8"))
        return msg
    except Exception as e:
        _log("stdin: json parse error:", repr(e), "raw=", body[:200])
        return {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"level": "error", "message": "bridge: failed to parse stdin JSON"},
        }


def stdio_write_message(stdout_buf, msg: Dict[str, Any]) -> None:
    """
    Writes one JSON-RPC object to stdout with MCP/LSP Content-Length framing.
    MUST NOT write anything else to stdout.
    """
    data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    stdout_buf.write(header)
    stdout_buf.write(data)
    stdout_buf.flush()


# ---------------------------
# SSE parsing (minimal)
# ---------------------------

@dataclass
class SSEEvent:
    event: str
    data: str


async def _aiter_sse_events(resp: httpx.Response):
    """
    Minimal SSE parser over an httpx streaming response.
    Yields SSEEvent(event, data).
    """
    event_type = ""
    data_lines: list[str] = []

    async for raw_line in resp.aiter_lines():
        if raw_line is None:
            continue

        if DEBUG:
            # Raw SSE line visibility is critical for diagnosing buffering/parsing issues.
            try:
                print("[pviz-bridge] SSE line:", repr(raw_line), file=sys.stderr, flush=True)
            except Exception:
                pass

        line = raw_line.rstrip("\r")

        # Comment/ping line starts with ":" (keepalives)
        if line.startswith(":"):
            continue

        if line == "":
            # dispatch
            if data_lines or event_type:
                yield SSEEvent(event=event_type or "message", data="\n".join(data_lines))
            event_type = ""
            data_lines = []
            continue

        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
            continue

        if line.startswith("data:"):
            # tolerate "data: <json>" and "data:<json>"
            data_lines.append(line[len("data:"):].lstrip())
            continue

        # ignore id:, retry:, etc.


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return float(v.strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip()


def _build_urls(remote_base: str) -> Tuple[str, str]:
    """
    remote_base expected like https://mcp.pvizgenerator.com/mcp
    Returns (sse_url, origin)
    """
    base = remote_base.rstrip("/")
    if not base.endswith("/mcp"):
        if base.endswith("/mcp/sse"):
            base = base[: -len("/sse")]
        elif base.endswith("/sse"):
            base = base[: -len("/sse")]
    sse_url = f"{base}/sse"

    u = httpx.URL(base)
    origin = f"{u.scheme}://{u.host}"
    if u.port:
        origin = f"{origin}:{u.port}"
    return sse_url, origin


def _join_messages_url(remote_base: str, endpoint_data: str) -> str:
    """
    Robustly convert the SSE 'endpoint' event data into an absolute URL.

    endpoint_data examples seen in the wild:
      - "/mcp/messages/?session_id=..."
      - "mcp/messages/?session_id=..."
      - "messages/?session_id=..."
      - "https://mcp.pvizgenerator.com/mcp/messages/?session_id=..."
    """
    raw = (endpoint_data or "").strip()

    # full URL already
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    # Build origin from the remote_base (scheme://host[:port])
    u = urlparse(remote_base.rstrip("/") + "/")
    origin = f"{u.scheme}://{u.netloc}"

    # Ensure leading slash so urljoin behaves predictably
    if raw and not raw.startswith("/"):
        raw = "/" + raw

    # Join against origin only (not remote_base path) since server typically returns absolute paths.
    return urljoin(origin + "/", raw)


class Bridge:
    def __init__(self) -> None:
        self.remote_base = _env_str("MCP_REMOTE_URL", "https://mcp.pvizgenerator.com/mcp")
        self.jwt = _env_str("PVIZ_JWT_TOKEN", "")
        self.timeout_s = _env_float("MCP_HTTP_TIMEOUT_S", 60.0)
        self.reconnect_s = _env_float("MCP_SSE_RECONNECT_S", 1.5)

        self.sse_url, self.origin = _build_urls(self.remote_base)

        # Important: do NOT clear messages_url on reconnect; keep last known until replaced.
        self.messages_url: Optional[str] = None

        self._stop = asyncio.Event()
        self._remote_out_q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        http2 = _bool_env("MCP_HTTP2", False)

        # SSE friendliness:
        # - Origin is often required by transport security logic
        # - identity prevents gzip/deflate buffering oddities for SSE
        # - no-cache encourages intermediaries to stream immediately
        headers: Dict[str, str] = {
            "accept": "text/event-stream",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "accept-encoding": "identity",
            "connection": "keep-alive",
            "origin": self.origin,
            "user-agent": "pviz-mcp-stdio-bridge/1.4",
        }
        if self.jwt:
            headers["authorization"] = f"Bearer {self.jwt}"

        # Prefer explicit timeout object. Keep a single timeout for simplicity.
        timeout = httpx.Timeout(self.timeout_s)

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            http2=http2,
            trust_env=False,   # IMPORTANT: ignore system proxy env vars unless you explicitly want them
        )

        _log(
            "BOOT",
            "remote_base=", self.remote_base,
            "sse_url=", self.sse_url,
            "origin=", self.origin,
            "http2=", http2,
            "JWT present=", bool(self.jwt),
            "timeout_s=", self.timeout_s,
        )

    async def close(self) -> None:
        self._stop.set()
        await self._client.aclose()

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._remote_sse_loop(), name="remote_sse_loop"),
            asyncio.create_task(self._stdio_in_loop(), name="stdio_in_loop"),
            asyncio.create_task(self._stdio_out_loop(), name="stdio_out_loop"),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc:
                raise exc

    async def _ensure_messages_url(self) -> str:
        # Wait until SSE provides an endpoint at least once.
        while not self.messages_url and not self._stop.is_set():
            await asyncio.sleep(0.01)
        if not self.messages_url:
            raise RuntimeError("Bridge stopped before receiving SSE endpoint.")
        return self.messages_url

    async def _remote_sse_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _log("SSE connect ->", self.sse_url)
                async with self._client.stream("GET", self.sse_url) as resp:
                    resp.raise_for_status()
                    _log(
                        "SSE status=",
                        resp.status_code,
                        "content-type=",
                        resp.headers.get("content-type"),
                    )

                    async for ev in _aiter_sse_events(resp):
                        if self._stop.is_set():
                            return

                        if ev.event == "endpoint":
                            raw = ev.data.strip()
                            try:
                                self.messages_url = _join_messages_url(self.remote_base, raw)
                                _log("SSE endpoint raw =", raw)
                                _log("SSE endpoint url =", self.messages_url)
                            except Exception as e:
                                _log("SSE endpoint parse failed:", repr(e), "raw=", raw)
                            continue

                        data = ev.data.strip()
                        if not data:
                            continue

                        try:
                            msg = json.loads(data)
                            _log("SSE <-", "id=", msg.get("id"), "method=", msg.get("method"))
                            await self._remote_out_q.put(msg)
                        except Exception as e:
                            _log("SSE json parse failed:", repr(e), "data=", data[:300])
                            await self._remote_out_q.put(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "notifications/message",
                                    "params": {
                                        "level": "error",
                                        "message": f"bridge: failed to parse SSE JSON: {e!r}",
                                    },
                                }
                            )

            except asyncio.CancelledError:
                return
            except httpx.HTTPError as e:
                _log("SSE HTTPError:", repr(e), "reconnect in", self.reconnect_s)
                await asyncio.sleep(self.reconnect_s)
            except Exception as e:
                _log("SSE unexpected:", repr(e), "reconnect in", max(self.reconnect_s, 2.0))
                await asyncio.sleep(max(self.reconnect_s, 2.0))

    async def _stdio_in_loop(self) -> None:
        stdin = sys.stdin.buffer
        while not self._stop.is_set():
            msg = stdio_read_message(stdin)
            if msg is None:
                _log("stdin EOF -> closing")
                await self.close()
                return

            _log("STDIN ->", "id=", msg.get("id"), "method=", msg.get("method"))

            url = await self._ensure_messages_url()

            try:
                r = await self._client.post(
                    url,
                    json=msg,
                    headers={
                        "content-type": "application/json",
                        "accept": "application/json",
                    },
                )

                # A common failure mode if the SSE session rotated.
                if r.status_code == 400 and "Invalid session ID" in (r.text or ""):
                    _log("POST got Invalid session ID; clearing messages_url and retrying once")
                    self.messages_url = None
                    url = await self._ensure_messages_url()
                    r = await self._client.post(
                        url,
                        json=msg,
                        headers={
                            "content-type": "application/json",
                            "accept": "application/json",
                        },
                    )

                _log("POST <-", r.status_code, "len=", len(r.text or ""))

            except httpx.HTTPError as e:
                _log("POST HTTPError:", repr(e))
                await self._remote_out_q.put(
                    {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {
                            "code": -32000,
                            "message": f"Bridge failed to POST to remote MCP: {type(e).__name__}: {e}",
                        },
                    }
                )
                await asyncio.sleep(0.25)

    async def _stdio_out_loop(self) -> None:
        stdout = sys.stdout.buffer
        while not self._stop.is_set():
            msg = await self._remote_out_q.get()
            try:
                _log("STDOUT <-", "id=", msg.get("id"), "method=", msg.get("method"))
                stdio_write_message(stdout, msg)
            except BrokenPipeError:
                _log("stdout BrokenPipe -> closing")
                await self.close()
                return
            except Exception as e:
                _log("stdout write failed:", repr(e))
                await self.close()
                return


async def main() -> None:
    bridge = Bridge()
    try:
        await bridge.run()
    finally:
        await bridge.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        try:
            print(f"[pviz-bridge] FATAL: {e!r}", file=sys.stderr, flush=True)
        except Exception:
            pass
        raise
