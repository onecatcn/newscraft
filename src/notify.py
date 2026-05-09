#!/usr/bin/env python3
"""notify.py -- Generic webhook notification module.

Sends pipeline status updates via a configurable webhook URL.
Supports any webhook endpoint that accepts JSON POST requests
(e.g., Slack incoming webhooks, custom bots, etc.).

Configure via environment variable:
    NOTIFY_WEBHOOK_URL=https://your-webhook-endpoint/...

If NOTIFY_WEBHOOK_URL is not set, notifications are silently skipped.
"""

import json
import sys
import urllib.error
import urllib.request


class NotifyClient:
    """Sends markdown messages to a webhook endpoint."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_md(self, content: str) -> bool:
        """Send a markdown message. Returns True on success."""
        if not self.webhook_url:
            return False
        return _post_json(self.webhook_url, {"text": content, "markdown": content})

    def send_text(self, content: str) -> bool:
        """Send a plain text message. Returns True on success."""
        if not self.webhook_url:
            return False
        return _post_json(self.webhook_url, {"text": content})

    def send_error(self, stage: str, error: str) -> bool:
        """Send an error notification."""
        content = (
            f"## newscraft pipeline error\n"
            f"**Stage**: {stage}\n"
            f"**Error**: {error}\n"
        )
        return self.send_md(content)


def _post_json(url: str, payload: dict) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"[notify] webhook failed: {e}", file=sys.stderr)
        return False
