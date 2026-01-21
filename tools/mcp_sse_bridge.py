# tools/mcp_sse_bridge.py
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Set, List

import httpx


# ---------------------------
# Logging (stderr)
# ---------------------------

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


DEBUG = _bool_env("MCP_DEBUG", False)


def _warn_line(s: str) -> None:
    try:
        sys.stderr.write(s + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _warn(*parts: object) -> None:
    _warn_line("[pviz-bridge] " + " ".join(str(p) for p in parts))


def _log(*parts: object) -> None:
    if DEBUG:
        _warn(*parts)


_warn("BOOT (stderr visible)")
_warn("ENV MCP_DEBUG=", os.getenv("MCP_DEBUG"))
_warn("ENV MCP_REMOTE_URL=", os.getenv("MCP_REMOTE_URL"))


# ---------------------------
# Stdio reader: supports JSONL (streaming) + Content-Length framing
# ---------------------------

class StdioReader:
    """
    Incremental stdio reader that can parse:
      - JSONL (newline-delimited OR not-yet-newline-terminated OR back-to-back JSON objects)
      - Content-Length framed messages

    CRITICAL: stdin reads happen in a thread (asyncio.to_thread) so the event loop
    doesn't freeze and starve SSE/POST tasks.
    """

    def __init__(self) -> None:
        self.buf = b""
        self.mode: Optional[str] = None  # "jsonl" or "framed"
        self._decoder = json.JSONDecoder()
        self._text_buf = ""  # decoded utf-8 text for JSONL-ish mode

    async def _read_chunk(self, n: int = 4096) -> bytes:
        stdin = sys.stdin.buffer

        def _blocking_read() -> bytes:
            if hasattr(stdin, "read1"):
                return stdin.read1(n)  # type: ignore[attr-defined]
            return stdin.read(n)

        return await asyncio.to_thread(_blocking_read)

    async def read_message(self) -> Optional[Dict[str, Any]]:
        while True:
            msg = self._try_parse_one()
            if msg is not None:
                return msg

            chunk = await self._read_chunk(4096)
            if not chunk:
                return None
            self.buf += chunk

    def _try_parse_one(self) -> Optional[Dict[str, Any]]:
        b = self.buf.lstrip()
        if not b:
            return None

        # Detect framed
        if b.startswith(b"Content-Length:") or b.startswith(b"content-length:"):
            self.mode = self.mode or "framed"
            return self._try_parse_framed()

        # Otherwise JSONL-ish
        self.mode = self.mode or "jsonl"
        return self._try_parse_json_streaming()

    def _try_parse_framed(self) -> Optional[Dict[str, Any]]:
        hdr_end = self.buf.find(b"\r\n\r\n")
        sep_len = 4
        if hdr_end < 0:
            hdr_end = self.buf.find(b"\n\n")
            sep_len = 2
            if hdr_end < 0:
                return None

        header_block = self.buf[:hdr_end].decode("utf-8", errors="replace")
        headers: Dict[str, str] = {}
        for line in header_block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        cl_raw = headers.get("content-length")
        if not cl_raw:
            _warn("framed: missing content-length; dropping header block")
            self.buf = self.buf[hdr_end + sep_len:]
            return None

        try:
            n = int(cl_raw)
        except ValueError:
            _warn("framed: invalid content-length:", cl_raw)
            self.buf = self.buf[hdr_end + sep_len:]
            return None

        body_start = hdr_end + sep_len
        if len(self.buf) < body_start + n:
            return None

        body = self.buf[body_start: body_start + n]
        self.buf = self.buf[body_start + n:]

        try:
            return json.loads(body.decode("utf-8", errors="replace"))
        except Exception as e:
            _warn("framed json parse error:", type(e).__name__, str(e))
            return None

    def _try_parse_json_streaming(self) -> Optional[Dict[str, Any]]:
        """
        Parse one JSON value from the front using JSONDecoder.raw_decode,
        which correctly handles:
          - {"a":1}\n
          - {"a":1}{"b":2}
          - {"a":1}   (no newline yet; returns only if complete)
        """
        if self.buf:
            # Keep text buffer in sync (append newly available bytes)
            # Decode everything; it's ok because buf is bounded by reads.
            self._text_buf = self.buf.decode("utf-8", errors="replace")

        s = self._text_buf.lstrip()
        if not s:
            return None

        # If we *do* have a newline, fast path: parse one line if valid JSON
        nl = s.find("\n")
        if nl >= 0:
            line = s[:nl].strip()
            rest = s[nl + 1:]
            if not line:
                self._consume_text_prefix(len(self._text_buf) - len(s) + nl + 1)
                return None
            try:
                obj = json.loads(line)
                self._consume_text_prefix(len(self._text_buf) - len(s) + nl + 1)
                return obj
            except Exception:
                # fall through to raw_decode
                pass

        # raw_decode from start of stripped string
        try:
            obj, end = self._decoder.raw_decode(s)
        except json.JSONDecodeError:
            return None

        # Consume prefix including leading whitespace we stripped
        leading_ws = len(self._text_buf) - len(s)
        self._consume_text_prefix(leading_ws + end)
        return obj  # type: ignore[return-value]

    def _consume_text_prefix(self, n_chars: int) -> None:
        # Consume from text buffer and update bytes buffer accordingly.
        # Re-encode the remainder to bytes for the framing detector.
        if n_chars <= 0:
            return
        remaining = self._text_buf[n_chars:]
        self._text_buf = remaining
        self.buf = remaining.encode("utf-8", errors="replace")


# ---------------------------
# Stdout writers (UTF-8 safe on Windows)
# ---------------------------

def _maybe_reconfigure_stdout_utf8() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="strict")  # type: ignore[attr-defined]
    except Exception:
        pass


_maybe_reconfigure_stdout_utf8()


def stdio_write_jsonl(msg: Dict[str, Any]) -> None:
    data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
    sys.stdout.buffer.write((data + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def stdio_write_framed(msg: Dict[str, Any]) -> None:
    data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# ---------------------------
# SSE parsing
# ---------------------------

@dataclass
class SSEEvent:
    event: str
    data: str


def _parse_sse_block(block: str) -> Optional[SSEEvent]:
    event_type = ""
    data_lines: list[str] = []
    for raw in block.split("\n"):
        line = raw.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if not event_type and not data_lines:
        return None
    return SSEEvent(event=event_type or "message", data="\n".join(data_lines))


async def _aiter_sse_events(resp: httpx.Response):
    buf = b""
    async for chunk in resp.aiter_bytes():
        if not chunk:
            continue
        buf += chunk
        while True:
            idx = buf.find(b"\n\n")
            sep_len = 2
            if idx < 0:
                idx = buf.find(b"\r\n\r\n")
                sep_len = 4
                if idx < 0:
                    break
            frame = buf[:idx]
            buf = buf[idx + sep_len:]
            text = frame.decode("utf-8", errors="replace")
            ev = _parse_sse_block(text)
            if ev:
                yield ev


async def _anext(ait):
    return await ait.__anext__()


# ---------------------------
# Env helpers
# ---------------------------

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


# ---------------------------
# Bridge
# ---------------------------

class Bridge:
    def __init__(self) -> None:
        self.remote_base = _env_str("MCP_REMOTE_URL", "https://mcp.pvizgenerator.com/mcp")
        self.jwt = _env_str("PVIZ_JWT_TOKEN", "")
        self.http_timeout_s = _env_float("MCP_HTTP_TIMEOUT_S", 60.0)
        self.sse_open_timeout_s = _env_float("MCP_SSE_CONNECT_TIMEOUT_S", 10.0)
        self.sse_first_event_timeout_s = _env_float("MCP_SSE_FIRST_EVENT_TIMEOUT_S", 10.0)
        self.reconnect_s = _env_float("MCP_SSE_RECONNECT_S", 1.5)
        self.endpoint_grace_s = _env_float("MCP_ENDPOINT_GRACE_S", 0.35)

        self.sse_url, self.origin = _build_urls(self.remote_base)

        self._sse_messages_url: Optional[str] = None
        self._latched_messages_url: Optional[str] = None

        self._stop = asyncio.Event()
        self._remote_out_q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._pending_posts: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        self._drop_response_ids: Set[Any] = set()

        # Cache remote capabilities once we see remote init response (even if we drop it)
        self._remote_caps: Optional[Dict[str, Any]] = None

        default_http2 = False if os.name == "nt" else True
        self._prefer_http2 = _bool_env("MCP_HTTP2", default_http2)
        self._http2_try_order: List[bool] = [self._prefer_http2, not self._prefer_http2]

        self._headers: Dict[str, str] = {
            "accept": "text/event-stream",
            "cache-control": "no-cache",
            "accept-encoding": "identity",
            "connection": "keep-alive",
            "origin": self.origin,
            "user-agent": "pviz-mcp-stdio-bridge/2.6",
        }
        if self.jwt:
            self._headers["authorization"] = f"Bearer {self.jwt}"

        self._sse_client: Optional[httpx.AsyncClient] = None
        self._post_client: Optional[httpx.AsyncClient] = None
        self._post_client_lock = asyncio.Lock()

        self._stdio = StdioReader()
        self._out_mode: Optional[str] = None  # "jsonl" or "framed"

        _warn(
            "BOOT remote_base=", self.remote_base,
            "sse_url=", self.sse_url,
            "origin=", self.origin,
            "JWT present=", bool(self.jwt),
            "http_timeout_s=", self.http_timeout_s,
            "sse_open_timeout_s=", self.sse_open_timeout_s,
            "sse_first_event_timeout_s=", self.sse_first_event_timeout_s,
            "endpoint_grace_s=", self.endpoint_grace_s,
            "http2_order=", self._http2_try_order,
        )

    def _write(self, msg: Dict[str, Any]) -> None:
        mode = self._out_mode or self._stdio.mode or "jsonl"
        if mode == "framed":
            stdio_write_framed(msg)
        else:
            stdio_write_jsonl(msg)

    def _effective_messages_url(self) -> Optional[str]:
        return self._sse_messages_url or self._latched_messages_url

    async def _ensure_post_client(self) -> None:
        if self._post_client is not None:
            return
        async with self._post_client_lock:
            if self._post_client is not None:
                return
            self._post_client = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(self.http_timeout_s),
                follow_redirects=True,
                http2=self._prefer_http2,
                trust_env=False,
            )
            _warn("POST client created http2=", self._prefer_http2)

    async def _make_sse_client(self, http2: bool) -> None:
        try:
            if self._sse_client:
                await self._sse_client.aclose()
        except Exception:
            pass
        self._sse_client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(connect=None, read=None, write=10.0, pool=10.0),
            follow_redirects=True,
            http2=http2,
            trust_env=False,
        )

    async def _close_clients(self) -> None:
        try:
            if self._sse_client:
                await self._sse_client.aclose()
        except Exception:
            pass
        try:
            if self._post_client:
                await self._post_client.aclose()
        except Exception:
            pass
        self._sse_client = None
        self._post_client = None

    async def close(self) -> None:
        self._stop.set()
        await self._close_clients()

    def _local_initialize_response(self, req: Dict[str, Any]) -> Dict[str, Any]:
        req_id = req.get("id")
        pv = req.get("params", {}).get("protocolVersion", "2025-06-18")

        # If we learned remote caps, advertise them; otherwise keep minimal structure.
        caps = self._remote_caps if isinstance(self._remote_caps, dict) else {"tools": {}, "prompts": {}, "resources": {}}

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": pv,
                "capabilities": caps,
                "serverInfo": {"name": "pviz-mcp-remote-bridge", "version": "2.6"},
            },
        }

    def _candidate_message_urls(self) -> List[str]:
        base = self.remote_base.rstrip("/")
        return [base, f"{base}/message", f"{base}/messages"]

    @staticmethod
    def _is_success_latch_status(code: int) -> bool:
        return (200 <= code < 300) or code in (202, 204)

    async def _post_json_no_body_wait(self, url: str, msg: Dict[str, Any]) -> int:
        await self._ensure_post_client()
        assert self._post_client is not None
        req = self._post_client.build_request(
            "POST",
            url,
            json=msg,
            headers={"content-type": "application/json"},
        )
        resp = await self._post_client.send(req, stream=True)
        code = resp.status_code
        await resp.aclose()
        return code

    async def _post_with_probe(self, msg: Dict[str, Any]) -> int:
        if self._sse_messages_url:
            return await self._post_json_no_body_wait(self._sse_messages_url, msg)

        if self._latched_messages_url:
            return await self._post_json_no_body_wait(self._latched_messages_url, msg)

        if self.endpoint_grace_s > 0:
            end = asyncio.get_running_loop().time() + float(self.endpoint_grace_s)
            while asyncio.get_running_loop().time() < end:
                if self._sse_messages_url:
                    return await self._post_json_no_body_wait(self._sse_messages_url, msg)
                await asyncio.sleep(0.01)

        last_exc: Optional[Exception] = None
        for url in self._candidate_message_urls():
            if self._sse_messages_url:
                return await self._post_json_no_body_wait(self._sse_messages_url, msg)
            try:
                code = await self._post_json_no_body_wait(url, msg)
                if code in (404, 405):
                    _log("Probe POST", url, "->", code, "(continue)")
                    continue
                if not self._is_success_latch_status(code):
                    _log("Probe POST", url, "->", code, "(not latching)")
                    continue
                self._latched_messages_url = url
                _warn("Latched messages_url =", self._latched_messages_url, "(probe status=", code, ")")
                return code
            except Exception as e:
                last_exc = e
                continue

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unable to determine MCP messages endpoint (all probes failed)")

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._remote_sse_loop(), name="remote_sse_loop"),
            asyncio.create_task(self._pending_post_flusher_loop(), name="pending_post_flusher_loop"),
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

    async def _remote_sse_loop(self) -> None:
        attempt_idx = 0
        while not self._stop.is_set():
            http2 = self._http2_try_order[attempt_idx % len(self._http2_try_order)]
            attempt_idx += 1

            cm = None
            try:
                await self._make_sse_client(http2=http2)
                assert self._sse_client is not None

                _warn("SSE connect ->", self.sse_url, "http2=", http2)

                cm = self._sse_client.stream("GET", self.sse_url)
                resp = await asyncio.wait_for(cm.__aenter__(), timeout=self.sse_open_timeout_s)
                _warn("SSE status=", resp.status_code, "ct=", resp.headers.get("content-type"))
                resp.raise_for_status()

                events_iter = _aiter_sse_events(resp)

                try:
                    first_ev = await asyncio.wait_for(_anext(events_iter), timeout=self.sse_first_event_timeout_s)
                    await self._handle_sse_event(first_ev)
                except asyncio.TimeoutError:
                    _warn("SSE connected but no first event yet; continuing without endpoint event.")

                async for ev in events_iter:
                    if self._stop.is_set():
                        return
                    await self._handle_sse_event(ev)

                _warn("SSE stream ended. Reconnect in", self.reconnect_s)

            except asyncio.TimeoutError:
                _warn("SSE timeout (open). Reconnect in", self.reconnect_s, "http2=", http2)
                await asyncio.sleep(self.reconnect_s)
            except Exception as e:
                _warn("SSE error:", type(e).__name__, str(e), "Reconnect in", max(self.reconnect_s, 2.0))
                await asyncio.sleep(max(self.reconnect_s, 2.0))
            finally:
                try:
                    if cm is not None:
                        await cm.__aexit__(None, None, None)
                except Exception:
                    pass

    async def _handle_sse_event(self, ev: SSEEvent) -> None:
        if ev.event == "endpoint":
            path = ev.data.strip()
            if path.startswith("http://") or path.startswith("https://"):
                self._sse_messages_url = path
            else:
                self._sse_messages_url = f"{self.origin}{path}"
            _warn("SSE endpoint =", self._sse_messages_url)
            return

        data = ev.data.strip()
        if not data:
            return

        try:
            msg = json.loads(data)

            # Learn remote capabilities from initialize response, even if we drop it
            if msg.get("id") in self._drop_response_ids and isinstance(msg.get("result"), dict):
                res = msg.get("result") or {}
                if isinstance(res, dict) and isinstance(res.get("capabilities"), dict):
                    self._remote_caps = res["capabilities"]  # type: ignore[assignment]

            if "id" in msg and msg.get("id") in self._drop_response_ids and ("result" in msg or "error" in msg):
                _log("SSE <- dropping remote response for locally-handled id=", msg.get("id"))
                return

            await self._remote_out_q.put(msg)
        except Exception as e:
            _warn("SSE json parse failed:", type(e).__name__, str(e))

    async def _pending_post_flusher_loop(self) -> None:
        while not self._stop.is_set():
            msg = await self._pending_posts.get()
            if msg is None:
                continue
            try:
                code = await self._post_with_probe(msg)
                _log("PENDING POST ->", code, "(url=", self._effective_messages_url(), ")")
            except Exception as e:
                _warn("Pending POST failed:", type(e).__name__, str(e))
                await asyncio.sleep(0.25)

    async def _stdio_in_loop(self) -> None:
        while not self._stop.is_set():
            msg = await self._stdio.read_message()
            if msg is None:
                _warn("stdin EOF -> closing")
                await self.close()
                return

            if self._out_mode is None and self._stdio.mode:
                self._out_mode = self._stdio.mode
                _warn("STDIO mode detected:", self._out_mode)

            mid = msg.get("id")
            method = msg.get("method")
            _warn("STDIN recv id=", mid, "method=", method)

            if method == "initialize" and mid is not None:
                self._drop_response_ids.add(mid)
                self._write(self._local_initialize_response(msg))
                _warn("LOCAL initialize response sent")
                await self._pending_posts.put(msg)
                continue

            if method == "notifications/cancelled":
                _log("ignoring notifications/cancelled")
                continue

            try:
                code = await self._post_with_probe(msg)
                _warn("POST ->", code, "method=", method, "id=", mid, "url=", self._effective_messages_url())
            except Exception as e:
                _warn("POST failed:", type(e).__name__, str(e))
                await self._remote_out_q.put(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32000, "message": f"Bridge POST failed: {type(e).__name__}: {e}"},
                    }
                )
                await asyncio.sleep(0.25)

    async def _stdio_out_loop(self) -> None:
        while not self._stop.is_set():
            msg = await self._remote_out_q.get()
            try:
                self._write(msg)
            except BrokenPipeError:
                _warn("stdout BrokenPipe -> closing")
                await self.close()
                return
            except Exception as e:
                _warn("stdout write failed:", type(e).__name__, str(e))
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
        _warn("FATAL:", repr(e))
        raise
