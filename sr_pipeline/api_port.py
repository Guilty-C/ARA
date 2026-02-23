from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import urllib.request
import urllib.error
import time
import logging

from sr_pipeline.logging_utils import get_stage, EventWriter

@dataclass
class APIResponse:
    ok: bool
    result: Any
    meta: Dict[str, Any]

class HTTPAPIPort:
    """
    Gateway to external API endpoints.
    Contract:
      POST {base_url}/{endpoint}
      body: JSON payload
      response: {"ok": bool, "result": ..., "meta": {...}}
    """
    def __init__(self, base_url: str, timeout_s: float = 20.0, 
                 logger: Optional[logging.Logger] = None, 
                 event_writer: Optional[EventWriter] = None,
                 fail_fast: bool = False):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.logger = logger
        self.event_writer = event_writer
        self.fail_fast = fail_fast

    def call(self, endpoint: str, payload: Dict[str, Any]) -> APIResponse:
        start_ts = time.time()
        url = f"{self.base_url}/{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        payload_bytes = len(data)

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        ok = False
        response_bytes = 0
        error_msg = None
        
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                response_bytes = len(raw)
                raw_str = raw.decode("utf-8")
                obj = json.loads(raw_str)
                
                resp_obj = APIResponse(
                    ok=bool(obj.get("ok", False)),
                    result=obj.get("result", None),
                    meta=dict(obj.get("meta", {})),
                )
                ok = resp_obj.ok
                return resp_obj
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            error_msg = str(e)
            return APIResponse(ok=False, result=None, meta={"error": error_msg})
        finally:
            latency_ms = (time.time() - start_ts) * 1000
            
            # Log event if writer is present
            if self.event_writer:
                stage = get_stage()
                evt = {
                    "kind": "api_call",
                    "stage": stage,
                    "endpoint": endpoint,
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "payload_bytes": payload_bytes,
                    "response_bytes": response_bytes
                }
                if error_msg:
                    evt["error_msg"] = error_msg
                    evt["error_type"] = "APIError"
                self.event_writer.append(evt)
            
            # Log summary if logger is present
            if self.logger:
                stage = get_stage()
                msg = f"API[{endpoint}] stage={stage} ok={ok} ms={latency_ms:.1f}"
                if ok:
                    self.logger.info(msg)
                else:
                    self.logger.warning(f"{msg} error={error_msg}")
            
            # Fail fast if enabled and call failed
            if self.fail_fast and not ok:
                stage = get_stage()
                raise RuntimeError(f"api_call_failed endpoint={endpoint} stage={stage} error={error_msg}")

class APIClient:
    def __init__(self, port: HTTPAPIPort):
        self.port = port

    def ping(self) -> APIResponse:
        return self.port.call("ping", {})

    def llm_complete(self, prompt: str) -> APIResponse:
        return self.port.call("llm_complete", {"prompt": prompt})
