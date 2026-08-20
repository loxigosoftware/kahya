#!/usr/bin/env python3
"""Mock OpenAI-compatible LLM — tool call akışı (MCP testi için).

İstek 1: tool_calls (deneme__not_ekle) → amele MCP tool'unu çağırır.
İstek 2+: final mesaj.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL_NAME = os.environ.get("MOCK_TOOL", "deneme__not_ekle")
COUNTER = {"n": 0}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        COUNTER["n"] += 1
        if COUNTER["n"] == 1:
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "call_1", "type": "function",
                                   "function": {"name": TOOL_NAME,
                                                "arguments": "{\"not\": \"test notu\"}"}}]}
            finish = "tool_calls"
        else:
            msg = {"role": "assistant", "content": "Not kaydedildi: test notu."}
            finish = "stop"
        payload = json.dumps({
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"total_tokens": 10}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "9471"))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
