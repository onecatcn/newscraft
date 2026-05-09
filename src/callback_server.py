#!/usr/bin/env python3
"""callback_server.py -- HTTP server for pipeline event callbacks.

Dual-purpose:
1. Receives approval/reply POST callbacks at /callback
2. Serves /health endpoint for Docker health checks
3. (Optional) Serves a simple sidebar page at /sidebar

Messages are written to /data/state/reply_queue.json
for the orchestrator to consume.
"""

import json
import os
import sys
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Lock

# State file path
STATE_DIR = Path(os.environ.get("DATA_DIR", "/data")) / "state"
REPLY_QUEUE_PATH = STATE_DIR / "reply_queue.json"
ALLOWED_GROUP_ID = os.environ.get("CALLBACK_GROUP_ID", "")

# Thread-safe lock for file writes
_lock = Lock()


def _ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def append_reply(sender: str, content: str, msg_id: str = ""):
    """Append a reply message to the queue file."""
    _ensure_dirs()
    with _lock:
        queue = []
        if REPLY_QUEUE_PATH.exists():
            try:
                queue = json.loads(REPLY_QUEUE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                queue = []

        queue.append({
            "sender": sender,
            "content": content.strip(),
            "msg_id": msg_id,
            "received_at": datetime.now().isoformat(),
        })

        REPLY_QUEUE_PATH.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"[callback] queued reply from {sender}: {content[:50]}", file=sys.stderr)


def read_and_clear_replies() -> list:
    """Read all replies from queue and clear it. Used by orchestrator."""
    _ensure_dirs()
    with _lock:
        if not REPLY_QUEUE_PATH.exists():
            return []
        try:
            queue = json.loads(REPLY_QUEUE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        # Clear the queue
        REPLY_QUEUE_PATH.write_text("[]", encoding="utf-8")
        return queue


# ── Sidebar HTML ──

SIDEBAR_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 每日 10 分钟 - 操作面板</title>
<style>
body { font-family: -apple-system, sans-serif; padding: 16px; background: #f5f5f5; }
h2 { color: #1a1a2e; font-size: 18px; }
.btn { display: block; width: 100%; padding: 12px; margin: 8px 0;
       border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
.btn-primary { background: #1a1a2e; color: #fff; }
.btn-success { background: #10b981; color: #fff; }
.btn-warn { background: #f59e0b; color: #fff; }
#status { margin-top: 16px; padding: 12px; background: #fff;
          border-radius: 8px; font-size: 14px; }
</style>
</head>
<body>
<h2>AI 每日 10 分钟</h2>
<p>公众号半自动化面板</p>
<div id="status">Loading status...</div>
<script>
fetch('/api/status').then(r=>r.json()).then(d=>{
  document.getElementById('status').innerHTML =
    '<strong>State:</strong> ' + d.state + '<br>' +
    '<strong>Date:</strong> ' + d.date;
}).catch(()=>{
  document.getElementById('status').textContent = 'Service running';
});
</script>
</body>
</html>
"""


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for pipeline callbacks and health checks."""

    def log_message(self, format, *args):
        """Override to log to stderr with timestamp."""
        print(f"[callback] {args[0]}", file=sys.stderr)

    def _send_json(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        )

    def _send_html(self, code: int, html: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "service": "newscraft",
                "timestamp": datetime.now().isoformat(),
            })
        elif self.path == "/sidebar":
            self._send_html(200, SIDEBAR_HTML)
        elif self.path == "/api/status":
            # Return current pipeline state
            state = self._load_today_state()
            self._send_json(200, state)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/callback":
            self._handle_callback()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_callback(self):
        """Handle event subscription callback."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty body"})
            return

        raw = self.rfile.read(content_length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid json"})
            return

        print(f"[callback] received event: {json.dumps(data, ensure_ascii=False)[:200]}",
              file=sys.stderr)

        # Support verification challenge (common in webhook subscriptions)
        if "challenge" in data:
            self._send_json(200, {"challenge": data["challenge"]})
            return

        # Extract message from event payload
        event_type = data.get("type", data.get("event_type", ""))
        message = data.get("message", data.get("msg", {}))

        if isinstance(message, dict):
            sender = message.get("from", message.get("sender", {}))
            if isinstance(sender, dict):
                sender_name = sender.get("name", sender.get("id", "unknown"))
            else:
                sender_name = str(sender)

            body = message.get("body", [])
            text_parts = []
            for part in body if isinstance(body, list) else []:
                if isinstance(part, dict) and part.get("type") in ("TEXT", "MD"):
                    text_parts.append(part.get("content", ""))

            content = " ".join(text_parts).strip()
            msg_id = str(message.get("msgid", message.get("msg_id", "")))

            if content:
                group_id = str(message.get("groupid",
                    message.get("group_id",
                    message.get("header", {}).get("toid", ""))))
                if ALLOWED_GROUP_ID and group_id and group_id != ALLOWED_GROUP_ID:
                    print(f"[callback] ignoring msg from group {group_id}", file=sys.stderr)
                else:
                    append_reply(sender_name, content, msg_id)

        self._send_json(200, {"code": "ok"})

    def _load_today_state(self) -> dict:
        """Load today's pipeline state for status endpoint."""
        today = datetime.now().strftime("%Y-%m-%d")
        state_file = (
            Path(os.environ.get("DATA_DIR", "/data"))
            / "daily_pipeline" / today / "pipeline_state.json"
        )
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"state": "IDLE", "date": today}


def run_server(port: int = 8080):
    """Start the HTTP callback server."""
    _ensure_dirs()
    server = HTTPServer(("0.0.0.0", port), CallbackHandler)
    print(f"[callback] listening on 0.0.0.0:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[callback] shutting down", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("CALLBACK_PORT", "8080"))
    run_server(port)
