"""Loopback-only counting proxy for model API calls (M6-Lite, docs/evaluation/protocol.md,
docs/decisions/ADR-004-m6-launch-decisions.md).

ADR-004: "at most 20 minutes and 15 model calls per run. Every request to the model counts,
including retries, automatic summaries and relaunches. If the runner cannot measure this
number, it refuses the official launch."

This proxy is that measurement. It binds 127.0.0.1 ONLY (refuses any other host), counts
every POST to /v1/messages (retries and summaries are such POSTs, so they count), answers
429 once the budget is spent (the extra attempts are counted as refused), and forwards
allowed requests to the upstream model API. The count is written to a JSON counter file
after every call, so it survives the agent being killed.

  model_proxy.py --max-calls 15 --counter-file counter.json [--upstream URL] [--port 0]
      prints "PROXY_PORT=<n>" on stdout, then serves until SIGTERM/SIGINT.

Used by tools/agent_runner.py (in-process, via serve()) and by the official launch
(standalone, with the agent pointed at it through its base-URL environment variable).
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_UPSTREAM = "https://api.anthropic.com"
MESSAGES_PATH = "/v1/messages"
LOOPBACK = "127.0.0.1"


class ProxyError(Exception):
    pass


class Counter:
    """JSON counter file, rewritten after every call (temp file + rename)."""

    def __init__(self, path, budget):
        self.path = Path(path)
        self.budget = budget
        self.lock = threading.Lock()
        self.calls = 0
        self.refused = 0
        self._write()

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".counter-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"calls": self.calls, "refused": self.refused, "budget": self.budget}, fh)
        os.replace(tmp, self.path)

    def attempt(self):
        """One model-call attempt: True if within budget (counted as a call), False if the
        budget is spent (counted as refused, answered 429 by the caller)."""
        with self.lock:
            if self.calls >= self.budget:
                self.refused += 1
                self._write()
                return False
            self.calls += 1
            self._write()
            return True

    def snapshot(self):
        with self.lock:
            return {"calls": self.calls, "refused": self.refused, "budget": self.budget}


def read_counter(path):
    """The counter as a dict, or None if it is missing or unreadable (=> not measured)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data.get("calls"), int) or not isinstance(data.get("refused"), int):
            return None
        return data
    except (OSError, ValueError):
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "selyth-model-proxy/1.0"

    def log_message(self, *args):                      # quiet: the runner logs, not the proxy
        pass

    def _reply(self, status, body=b"{}", content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path.startswith(MESSAGES_PATH):
            if not self.server.counter.attempt():
                self._reply(429, json.dumps({"error": {"type": "over_budget",
                                                        "message": "model call budget exhausted"}}).encode())
                return
            try:
                req = urllib.request.Request(self.server.upstream + self.path, data=body,
                                             method="POST")
                for header in ("Content-Type", "x-api-key", "anthropic-version"):
                    if self.headers.get(header):
                        req.add_header(header, self.headers[header])
                with urllib.request.urlopen(req, timeout=120) as resp:
                    self._reply(resp.status, resp.read())
            except urllib.error.HTTPError as exc:
                self._reply(exc.code, exc.read())
            except Exception as exc:                   # upstream unreachable: the call still counts
                self._reply(502, json.dumps({"error": {"type": "bad_gateway", "message": str(exc)}}).encode())
            return
        self._reply(404, json.dumps({"error": {"type": "not_found", "message": self.path}}).encode())

    def do_GET(self):                                   # health check for the runner
        if self.path == "/health":
            self._reply(200, json.dumps(self.server.counter.snapshot()).encode())
        else:
            self._reply(404)


class Proxy:
    """A counting proxy bound to 127.0.0.1. `serve()` runs it in a daemon thread."""

    def __init__(self, max_calls, counter_file, upstream=DEFAULT_UPSTREAM, host=LOOPBACK, port=0):
        if host != LOOPBACK:
            raise ProxyError(f"the counting proxy binds {LOOPBACK} only, not {host!r}")
        self.counter = Counter(counter_file, max_calls)
        self.upstream = upstream.rstrip("/")
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.counter = self.counter
        self.server.upstream = self.upstream
        self.port = self.server.server_address[1]
        self.thread = None

    def serve(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self.thread:
            self.thread.join(timeout=5)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-calls", type=int, required=True)
    ap.add_argument("--counter-file", required=True)
    ap.add_argument("--upstream", default=os.environ.get("MODEL_UPSTREAM", DEFAULT_UPSTREAM))
    ap.add_argument("--host", default=LOOPBACK)
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    try:
        proxy = Proxy(args.max_calls, args.counter_file, upstream=args.upstream,
                      host=args.host, port=args.port)
    except ProxyError as exc:
        print(f"model_proxy: {exc}", file=sys.stderr)
        return 2
    print(f"PROXY_PORT={proxy.port}", flush=True)
    proxy.serve()
    try:
        threading.Event().wait()                        # until SIGTERM/SIGINT
    except KeyboardInterrupt:
        pass
    finally:
        proxy.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
