from __future__ import annotations
import os
import time
import json
import hashlib
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass

from sr_pipeline.logging_utils import get_stage, EventWriter, get_event_writer

class CircuitBreakerError(Exception):
    """Raised when circuit breaker trips."""
    pass

@dataclass
class ReliabilityConfig:
    failure_threshold: int = 2
    cache_dir: str = "data/tool_cache"
    cache_mode: str = "READWRITE" # OFF, READONLY, READWRITE

class ReliabilityLayer:
    """
    Shared logic for reliability: Timeout, Circuit Breaker, Caching.
    Used by both ToolRegistry and ProviderReliabilityLayer.
    """
    def __init__(self, name: str, config: ReliabilityConfig, 
                 event_writer: Optional[EventWriter] = None,
                 logger: Optional[logging.Logger] = None,
                 stop_reason_callback = None):
        self.name = name
        self.config = config
        self.event_writer = event_writer
        self.logger = logger
        self.stop_reason_callback = stop_reason_callback
        
        self.consecutive_failures = 0
        
        self.cache_dir = Path(config.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Git ignore cache
        if not (self.cache_dir / ".gitignore").exists():
            (self.cache_dir / ".gitignore").write_text("*\n!.gitignore", encoding="utf-8")

    def _normalize_payload(self, payload: Dict[str, Any]) -> str:
        # Canonical JSON: sorted keys, no whitespace
        return json.dumps(payload, sort_keys=True, separators=(',', ':'))

    def _get_cache_key(self, method: str, payload: Dict[str, Any]) -> str:
        s = f"{self.name}:{method}:{self._normalize_payload(payload)}"
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def check_circuit_breaker(self, stage: str = "unknown"):
        if self.consecutive_failures >= self.config.failure_threshold:
            # Log event
            if self.event_writer:
                self.event_writer.append({
                    "kind": "tool_breaker", # Generic breaker event
                    "tool": self.name,
                    "stage": stage,
                    "breaker_tripped": True
                })
            
            # Set stop reason
            if self.stop_reason_callback:
                self.stop_reason_callback("tool_dead_end")
                
            raise CircuitBreakerError(f"Circuit breaker tripped for {self.name}")

    def check_cache(self, key: str) -> Optional[Dict[str, Any]]:
        if self.config.cache_mode == "OFF":
            return None
            
        cache_path = self.cache_dir / f"{key}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except:
                pass
        return None

    def write_cache(self, key: str, data: Dict[str, Any]):
        if self.config.cache_mode == "READWRITE":
            try:
                (self.cache_dir / f"{key}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            except: pass

    def log_event(self, kind: str, payload: Dict[str, Any]):
        if self.event_writer:
            self.event_writer.append({
                "kind": kind,
                **payload
            })

    def handle_success(self):
        self.consecutive_failures = 0

    def handle_failure(self, is_timeout: bool = False):
        self.consecutive_failures += 1
        if is_timeout and self.stop_reason_callback:
            self.stop_reason_callback("tool_timeout")
