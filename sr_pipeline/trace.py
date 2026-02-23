from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
import json
import time

@dataclass
class TraceEvent:
    ts: float
    stage: str
    action: str
    ok: bool
    info: Dict[str, Any]
    error: Optional[str] = None

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

def now_ts() -> float:
    return time.time()
