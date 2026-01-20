# tools/mcp_sse_bridge.py
"""
MCP stdio <-> MCP remote (HTTP/SSE) bridge for Claude Desktop.

Why this exists:
- Claude Desktop expects MCP servers over stdio (command/args).
- Your PViz MCP server is deployed remotely over HTTP/SSE at:
    https://mcp.pvizgenerator.com/mcp

This bridge:
- Speaks MCP stdio framing (Content-Length headers) to Claude.
- Establishes an SSE session to the remote MCP server (/mcp/sse).
- Receives the session-specific messages endpoint from the SSE "endpoint" event.
- Forwards every stdio JSON-RPC message to the remote via POST /mcp/messages/?session_id=...
- Forwards remote JSON-RPC responses/notifications back to stdio from:
    (a) SSE stream events (data: {json})
    (b) POST response body if it's JSON (IMPORTANT for initialize in some servers)

Env vars:
- MCP_REMOTE_URL: base remote MCP path (default: https://mcp.pvizgenerator.com/mcp)
- PVIZ_JWT_TOKEN: bearer token for Authorization (optional but typical)
- MCP_HTTP_TIMEOUT_S: http timeout seconds (default: 60)
- MCP_SSE_RECONNECT_S: reconnect delay seconds (default: 1.5)
- MCP_DEBUG: set to "1" for stderr debug logs

Dependencies:
- pip install httpx
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx


# ---------------------------
# Logging (stderr shows in Claude MCP logs)
# ---------------------------

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


DEBUG = _bool_env("MCP_DEBUG", False)


def _log(*parts: Any) -> None:
    if DEBUG:
        print("[pviz-bridge]", *parts, file=sys.stderr, flush=True)


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
        return None

    try:
        content_length = int(cl_raw)
    except ValueError:
        return None

    body = stdin_buf.read(content_length)
    if not body:
        return None

    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {
            "jsonrpc": "2.0",
            "method": "mcp.bridge.parse_error",
            "params": {"raw": body.decode("utf-8", "replace")},
        }


def stdio_write_message(stdout_buf, msg: Dict[str, Any]) -> None:
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
    data_lines = []

    async for raw_line in resp.aiter_lines():
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")

        # Comment/ping line starts with ":"
        if line.startswith(":"):
            continue

        if line == "":
            if data_lines or event_type:
                yield SSEEvent(event=event_type or "message", data="\n".join(data_lines))
            event_type = ""
            data_lines = []
            continue

        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
            continue

        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
            continue


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
    remote_base is expected like:
      https://mcp.pvizgenerator.com/mcp
    Returns:
      (sse_url, origin)
    """
    base = remote_base.rstrip("/")
    if base.endswith("/mcp/sse"):
        base = base[: -len("/sse")]
    elif base.endswith("/sse"):
        base = base[: -len("/sse")]

    if not base.endswith("/mcp"):
        # If user provided just https://host, append /mcp
        base = base + "/mcp"

    sse_url = f"{base}/sse"

    u = httpx.URL(base)
    origin = f"{u.scheme}://{u.host}"
    if u.port:
        origin = f"{origin}:{u.port}"
    return sse_url, origin


class Bridge:
    def __init__(self) -> None:
        self.remote_base = _env_str("MCP_REMOTE_URL", "https://mcp.pvizgenerator.com/mcp")
        self.jwt = _env_str("PVIZ_JWT_TOKEN", "")
        self.timeout_s = _env_float("MCP_HTTP_TIMEOUT_S", 60.0)
        self.reconnect_s = _env_float("MCP_SSE_RECONNECT_S", 1.5)

        self.sse_url, self.origin = _build_urls(self.remote_base)
        self.messages_url: Optional[str] = None

        self._stop = asyncio.Event()
        self._remote_out_q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        headers = {
            "accept": "text/event-stream",
            "user-agent": "pviz-mcp-stdio-bridge/1.1",
            # Some servers enforce Origin for host/dns-rebinding protections.
            "origin": self.origin,
        }
        if self.jwt:
            headers["authorization"] = f"Bearer {self.jwt}"

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        )

        _log("remote_base=", self.remote_base)
        _log("sse_url=", self.sse_url, "origin=", self.origin)

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
        while not self.messages_url and not self._stop.is_set():
            await asyncio.sleep(0.01)
        if not self.messages_url:
            raise RuntimeError("Bridge stopped before receiving SSE endpoint.")
        return self.messages_url

    async def _remote_sse_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _log("connecting SSE:", self.sse_url)
                async with self._client.stream("GET", self.sse_url) as resp:
                    resp.raise_for_status()

                    self.messages_url = None

                    async for ev in _aiter_sse_events(resp):
                        if self._stop.is_set():
                            return

                        if ev.event == "endpoint":
                            path = ev.data.strip()
                            if path.startswith("http://") or path.startswith("https://"):
                                self.messages_url = path
                            else:
                                self.messages_url = f"{self.origin}{path}"
                            _log("SSE endpoint:", self.messages_url)
                            continue

                        data = ev.data.strip()
                        if not data:
                            continue

                        try:
                            msg = json.loads(data)
                        except Exception:
                            continue

                        await self._remote_out_q.put(msg)

            except (httpx.HTTPError, asyncio.CancelledError) as e:
                if isinstance(e, asyncio.CancelledError):
                    return
                _log("SSE error:", repr(e))
                await asyncio.sleep(self.reconnect_s)
            except Exception as e:
                _log("SSE unexpected:", repr(e))
                await asyncio.sleep(max(self.reconnect_s, 2.0))

    async def _enqueue_jsonrpc_from_post_response(self, r: httpx.Response) -> None:
        """
        If remote returns JSON in the POST response body see if it looks like a JSON-RPC message
        and forward it to stdio. This is critical for initialize on many servers.
        """
        ctype = (r.headers.get("content-type") or "").lower()
        if "application/json" not in ctype:
            return

        try:
            payload = r.json()
        except Exception:
            return

        if isinstance(payload, dict):
            if payload.get("jsonrpc") == "2.0":
                await self._remote_out_q.put(payload)
            return

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("jsonrpc") == "2.0":
                    await self._remote_out_q.put(item)

    async def _stdio_in_loop(self) -> None:
        stdin = sys.stdin.buffer
        while not self._stop.is_set():
            msg = stdio_read_message(stdin)
            if msg is None:
                await self.close()
                return

            _log("-> remote", "method=", msg.get("method"), "id=", msg.get("id"))

            url = await self._ensure_messages_url()

            try:
                r = await self._client.post(
                    url,
                    json=msg,
                    headers={
                        "content-type": "application/json",
                        "accept": "application/json, text/plain, */*",
                    },
                )
                # Forward immediate JSON response if present.
                await self._enqueue_jsonrpc_from_post_response(r)

            except httpx.HTTPError as e:
                err = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {
                        "code": -32000,
                        "message": f"Bridge failed to POST to remote MCP: {type(e).__name__}: {e}",
                    },
                }
                await self._remote_out_q.put(err)
                await asyncio.sleep(0.25)

    async def _stdio_out_loop(self) -> None:
        stdout = sys.stdout.buffer
        while not self._stop.is_set():
            msg = await self._remote_out_q.get()
            try:
                _log("<- stdio", "method=", msg.get("method"), "id=", msg.get("id"))
                stdio_write_message(stdout, msg)
            except Exception:
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
        _log("fatal:", repr(e))
        raise
