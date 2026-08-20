#!/usr/bin/env python3
"""Mock MCP sunucusu (Streamable HTTP, JSON-RPC 2.0) — kahya MCP akış testleri için.

- POST /mcp: initialize → notifications/initialized → tools/list → tools/call
- Çağrılar CALLS listesine kaydedilir (tool adı + args)
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CALLS = []

TOOLS = [
    {"name": "not_ekle", "description": "Bir nota kaydeder",
     "inputSchema": {"type": "object",
                     "properties": {"not": {"type": "string"}},
                     "required": ["not"]}},
    {"name": "not_sil", "description": "Bir notu siler",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "integer"}}}},
]


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        method = body.get("method")
        req_id = body.get("id")

        if method == "initialize":
            result = {"protocolVersion": "2025-03-26",
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mock-mcp", "version": "1.0"}}
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = body.get("params") or {}
            CALLS.append({"name": params.get("name"),
                          "args": params.get("arguments") or {}})
            result = {"content": [{"type": "text",
                                   "text": f"mock: {params.get('name')} çalıştı"}],
                      "isError": False}
        else:
            result = {}

        payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("MCP-Protocol-Version", "2025-03-26")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", "9472"))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
