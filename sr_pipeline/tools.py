from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import urllib.request
import urllib.error
import time
import logging

import socket
import hashlib
from pathlib import Path

from sr_pipeline.logging_utils import get_stage, EventWriter, get_event_writer
from typing import Tuple, Set
from sr_pipeline.reliability import ReliabilityLayer, ReliabilityConfig, CircuitBreakerError

class ToolPermissionError(Exception):
    """Raised when a tool is called from a disallowed stage."""
    pass

ALLOWLIST: Dict[str, Set[str]] = {
    "topic": {"search", "summarize"},
    "background": {"search", "summarize", "retrieve"},
    "literature": {"search", "summarize", "retrieve", "draft"},
    "hypothesis": {"draft", "search"},
    "experiment": {"experiment", "summarize"},
    "critic": {"critique", "summarize", "experiment"},
    "conclusion": {"draft", "summarize"},
    "paper": {"draft", "summarize", "search"},
    "unknown": set(), # Deny all by default
}

def sanitize_untrusted_text(text: str) -> Tuple[str, Dict[str, int]]:
    """
    Heuristic sanitization of untrusted text.
    Logs 'untrusted_content_ingested' event.
    """
    patterns = ["ignore previous", "system prompt", "you must", "delete all"]
    stats = {"redacted_count": 0}
    sanitized = text
    import re
    
    for p in patterns:
        if p.lower() in sanitized.lower():
             sanitized, count = re.subn(re.escape(p), "[REDACTED]", sanitized, flags=re.IGNORECASE)
             stats["redacted_count"] += count
             
    # Log event
    writer = get_event_writer()
    if writer:
        writer.append({
            "kind": "untrusted_content_ingested",
            "redaction_stats": stats,
            "text_len": len(text)
        })
        
    return sanitized, stats

@dataclass
class ToolResponse:
    ok: bool
    result: Any
    meta: Dict[str, Any]

class HTTPToolPort:
    """
    Single porthole to the outside world.
    Contract:
      POST {base_url}/tool/<name>
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

    def call(self, tool_name: str, payload: Dict[str, Any], timeout_override: Optional[float] = None) -> ToolResponse:
        start_ts = time.time()
        url = f"{self.base_url}/tool/{tool_name}"
        data = json.dumps(payload).encode("utf-8")
        payload_bytes = len(data)
        timeout = timeout_override if timeout_override is not None else self.timeout_s

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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                response_bytes = len(raw)
                raw_str = raw.decode("utf-8")
                obj = json.loads(raw_str)
                
                tr = ToolResponse(
                    ok=bool(obj.get("ok", False)),
                    result=obj.get("result", None),
                    meta=dict(obj.get("meta", {})),
                )
                ok = tr.ok
                return tr
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, socket.timeout) as e:
            # Detect timeout
            is_timeout = False
            if isinstance(e, socket.timeout):
                is_timeout = True
            elif isinstance(e, urllib.error.URLError) and isinstance(e.reason, socket.timeout):
                is_timeout = True
            elif "timed out" in str(e).lower():
                is_timeout = True
                
            error_msg = "timeout" if is_timeout else str(e)
            return ToolResponse(ok=False, result=None, meta={"error": error_msg})
        finally:
            latency_ms = (time.time() - start_ts) * 1000
            
            # Log event if writer is present
            if self.event_writer:
                stage = get_stage()
                evt = {
                    "kind": "tool_call",
                    "stage": stage,
                    "tool": tool_name,
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "payload_bytes": payload_bytes,
                    "response_bytes": response_bytes
                }
                if error_msg:
                    evt["error_msg"] = error_msg
                    evt["error_type"] = "ToolError"
                self.event_writer.append(evt)
            
            # Log summary if logger is present
            if self.logger:
                stage = get_stage()
                msg = f"Tool[{tool_name}] stage={stage} ok={ok} ms={latency_ms:.1f}"
                if ok:
                    self.logger.info(msg)
                else:
                    self.logger.warning(f"{msg} error={error_msg}")
            
            # Fail fast if enabled and tool call failed
            if self.fail_fast and not ok:
                stage = get_stage()
                raise RuntimeError(f"tool_call_failed tool={tool_name} stage={stage} error={error_msg}")

import os

class ToolRegistry:
    """
    Thin convenience wrapper over the generic gateway.
    Keep it small now; expand later (search, pdf_parse, code_exec, etc.).
    """
    def __init__(self, port: HTTPToolPort, cache_dir: str = "data/tool_cache"):
        self.port = port
        self.stop_reason_callback = None
        
        # Reliability Config
        cache_mode = os.environ.get("TOOL_CACHE_MODE", "READWRITE")
        self.config = ReliabilityConfig(cache_dir=cache_dir, cache_mode=cache_mode)
        
        # We need a reliability layer per tool? Or one global?
        # ToolRegistry manages multiple tools. Circuit breaker is per tool.
        # So we map tool_name -> ReliabilityLayer
        self.reliability_layers: Dict[str, ReliabilityLayer] = {}

        # Timeout Config
        self.timeout_config = {
            "default": 20.0,
            "tools": {
                "search": 10.0,
                "summarize": 30.0,
                "draft": 60.0,
                "experiment": 120.0,
                "critique": 60.0
            },
            "stages": {
                "experiment": 180.0
            }
        }

    def set_stop_reason_callback(self, callback):
        self.stop_reason_callback = callback
        # Update existing layers
        for layer in self.reliability_layers.values():
            layer.stop_reason_callback = callback
        
    def set_cache_mode(self, mode: str):
        self.config.cache_mode = mode
        # Update layers
        for layer in self.reliability_layers.values():
            layer.config.cache_mode = mode

    def _get_reliability(self, tool_name: str) -> ReliabilityLayer:
        if tool_name not in self.reliability_layers:
            self.reliability_layers[tool_name] = ReliabilityLayer(
                tool_name, 
                self.config, 
                event_writer=self.port.event_writer, 
                logger=self.port.logger,
                stop_reason_callback=self.stop_reason_callback
            )
        return self.reliability_layers[tool_name]

    def _get_timeout(self, tool: str, stage: str) -> float:
        # Check env var override for tool
        env_key = f"TIMEOUT_{tool.upper()}"
        if env_key in os.environ:
            return float(os.environ[env_key])

        # Stage override > Tool config > Default
        if stage in self.timeout_config["stages"]:
            return self.timeout_config["stages"][stage]
        if tool in self.timeout_config["tools"]:
            return self.timeout_config["tools"][tool]
        return self.timeout_config["default"]

    def _normalize_payload(self, payload: Dict[str, Any]) -> str:
        # Canonical JSON: sorted keys, no whitespace
        return json.dumps(payload, sort_keys=True, separators=(',', ':'))

    def dispatch_tool(self, stage: str, tool_name: str, payload: Dict[str, Any]) -> ToolResponse:
        """
        Central dispatcher with permission checking.
        """
        # If stage is None (e.g. not set), default to unknown
        if not stage:
            stage = "unknown"
            
        allowed = ALLOWLIST.get(stage, set())
        if tool_name not in allowed:
            # Emit forbidden event
            error_msg = f"permission_denied: tool '{tool_name}' not allowed in stage '{stage}'"
            if self.port.event_writer:
                self.port.event_writer.append({
                    "kind": "tool_call",
                    "stage": stage,
                    "tool": tool_name,
                    "ok": False,
                    "error_msg": error_msg,
                    "error_type": "ToolPermissionError"
                })
            
            # Raise exception to be caught by Orchestrator
            raise ToolPermissionError(error_msg)
        
        # Debug: Injection
        inject_delay = os.environ.get("INJECT_DELAY_TOOL")
        if inject_delay == tool_name:
            payload["_debug_delay_s"] = float(os.environ.get("INJECT_DELAY_SEC", "0"))
        
        if os.environ.get("INJECT_FAIL_TOOL") == tool_name:
            payload["_debug_fail"] = True

        rel_layer = self._get_reliability(tool_name)

        # 1. Circuit Breaker Check
        rel_layer.check_circuit_breaker(stage)

        # 2. Cache Check
        normalized = self._normalize_payload(payload)
        key_str = f"{tool_name}:{normalized}"
        cache_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
        
        cached_data = rel_layer.check_cache(cache_key)
        if cached_data:
             # Log event for cache hit
            if self.port.event_writer:
                self.port.event_writer.append({
                    "kind": "tool_call",
                    "stage": stage,
                    "tool": tool_name,
                    "ok": bool(cached_data.get("ok", False)),
                    "cached": True,
                    "latency_ms": 0.0,
                    "payload_bytes": len(json.dumps(payload)),
                    "response_bytes": len(json.dumps(cached_data))
                })

            return ToolResponse(
                ok=bool(cached_data.get("ok", False)),
                result=cached_data.get("result"),
                meta=cached_data.get("meta", {})
            )

        # 3. Timeout Calculation
        timeout = self._get_timeout(tool_name, stage)
        
        # 4. Call (with timeout)
        res = self.port.call(tool_name, payload, timeout_override=timeout)
        
        # 5. Handle Result
        if res.ok:
            rel_layer.handle_success()
            # Write Cache
            cache_data = {
                "tool_name": tool_name,
                "payload_hash": cache_key,
                "result": res.result,
                "meta": res.meta,
                "ok": res.ok,
                "created_ts": time.time()
            }
            rel_layer.write_cache(cache_key, cache_data)
        else:
            # Check for timeout error in meta
            error = res.meta.get("error", "")
            is_timeout = (error == "timeout")
            rel_layer.handle_failure(is_timeout)
                
        return res

    def search(self, query: str, k: int = 5) -> ToolResponse:
        return self.dispatch_tool(get_stage(), "search", {"query": query, "k": k})

    def summarize(self, text: str, style: str = "bullet") -> ToolResponse:
        return self.dispatch_tool(get_stage(), "summarize", {"text": text, "style": style})

    def draft(self, instruction: str, inputs: Dict[str, Any]) -> ToolResponse:
        return self.dispatch_tool(get_stage(), "draft", {"instruction": instruction, "inputs": inputs})

    def experiment(self, spec: Dict[str, Any]) -> ToolResponse:
        return self.dispatch_tool(get_stage(), "experiment", {"spec": spec})

    def critique(self, paper_or_results: str) -> ToolResponse:
        return self.dispatch_tool(get_stage(), "critique", {"text": paper_or_results})
