#!/usr/bin/env python3
"""Mock OpenAI-compatible server: returns a fixed extraction JSON.

The payload comes from $MOCK_JSON (default: a valid 'record' intent).
Proves the full pipe: amele binary → agent YAML → schema validation →
stdout JSON, without a real LLM.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT = {
    "intent": "record",
    "title": "Su faturasi",
    "kind": "bill",
    "agent_slug": "fatura",
    "amount": 3000,
    "currency": "TRY",
    "due_date": "2026-08-19",
    "repeat_rule": "monthly",
    "repeat_detail": "20",
    "remind_before_days": 2,
    "note": "test",
    "ask_user": None,
}

FIXED = json.loads(os.environ.get("MOCK_JSON") or json.dumps(DEFAULT))


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        content = json.dumps(FIXED, ensure_ascii=False)
        res = {"choices": [{"message": {"role": "assistant", "content": content}}],
               "usage": {"prompt_tokens": 120, "completion_tokens": 60,
                         "total_tokens": 180}}
        payload = json.dumps(res).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "9431"))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
