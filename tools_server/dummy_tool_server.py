from __future__ import annotations
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", 8088))

def _send_json(h: BaseHTTPRequestHandler, code: int, obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(raw)))
    h.end_headers()
    h.wfile.write(raw)

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        
        # Parse payload
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}

        with open("server_debug.log", "a") as f:
             f.write(f"Received: {payload}\n")

        # Debug: Delay injection
        delay = float(payload.get("_debug_delay_s", 0))
        if delay > 0:
            import time
            time.sleep(delay)

        # Debug: Failure injection
        if payload.get("_debug_fail"):
             return _send_json(self, 500, {"ok": False, "result": None, "meta": {"error": "injected_failure"}})

        # Handle API calls
        if len(parts) >= 2 and parts[0] == "api":
            endpoint = parts[1]
            if endpoint == "ping":
                return _send_json(self, 200, {"ok": True, "result": {"pong": True}, "meta": {"endpoint": "ping"}})
            
            if endpoint == "llm_complete":
                prompt = payload.get("prompt", "")
                text = f"# Dummy Paper Draft\n\nPrompt length: {len(prompt)}\n\n(Replace with real API later.)"
                return _send_json(self, 200, {"ok": True, "result": text, "meta": {"endpoint": "llm_complete"}})
            
            return _send_json(self, 404, {"ok": False, "result": None, "meta": {"error": "api_endpoint_not_found"}})

        # Handle Tool calls
        if len(parts) != 2 or parts[0] != "tool":
            return _send_json(self, 404, {"ok": False, "result": None, "meta": {"error": "not_found"}})

        tool = parts[1]

        # Canned behaviors (replace later with real APIs)
        if tool == "search":
            q = payload.get("query", "")
            k = int(payload.get("k", 5))
            hits = [{"title": f"Candidate topic {i+1}: {q[:48]}", "id": i+1} for i in range(k)]
            return _send_json(self, 200, {"ok": True, "result": hits, "meta": {"tool": "search"}})

        if tool == "summarize":
            text = payload.get("text", "")
            return _send_json(self, 200, {"ok": True, "result": f"- Summary stub\n- len={len(text)}", "meta": {"tool": "summarize"}})

        if tool == "draft":
            instr = payload.get("instruction", "")
            if "hypotheses" in instr.lower():
                return _send_json(self, 200, {"ok": True, "result": [
                    "H1: Policy routing reduces overall SR cycle latency.",
                    "H2: Tool-gated stages reduce hallucinated citations vs LLM-only.",
                    "H3: Critic stage reduces overclaiming under noisy results."
                ], "meta": {"tool": "draft"}})
            return _send_json(self, 200, {"ok": True, "result": f"{instr}\n\n(Dummy draft output)", "meta": {"tool": "draft"}})

        if tool == "experiment":
            return _send_json(self, 200, {"ok": True, "result": {
                "toy_metric_latency_ms": [120, 115, 130, 118, 122],
                "trace_ok": True,
                "controlled_fail_caught": True
            }, "meta": {"tool": "experiment"}})

        if tool == "critique":
            return _send_json(self, 200, {"ok": True, "result": "Critique: add more runs; validate determinism; inject controlled failures.", "meta": {"tool": "critique"}})

        return _send_json(self, 200, {"ok": True, "result": None, "meta": {"tool": tool, "note": "no-op"}})

    def log_message(self, format, *args):
        # keep server quiet
        return

def main():
    httpd = HTTPServer((HOST, PORT), Handler)
    print(f"Dummy tool server listening on http://{HOST}:{PORT}")
    httpd.serve_forever()

if __name__ == "__main__":
    main()
