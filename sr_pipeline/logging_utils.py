from __future__ import annotations
import logging
import json
import time
import uuid
from pathlib import Path
from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple

# Context variable for current stage name
SR_STAGE: ContextVar[Optional[str]] = ContextVar("sr_stage", default=None)
# Global event writer (singleton-ish for the process)
_GLOBAL_EVENT_WRITER: Optional[EventWriter] = None

def set_stage(stage_name: str) -> None:
    SR_STAGE.set(stage_name)

def get_stage() -> Optional[str]:
    return SR_STAGE.get()

def set_global_event_writer(writer: EventWriter) -> None:
    global _GLOBAL_EVENT_WRITER
    _GLOBAL_EVENT_WRITER = writer

def get_event_writer() -> Optional[EventWriter]:
    return _GLOBAL_EVENT_WRITER

def init_run_id() -> str:
    """Returns a short run id (e.g., timestamp + random suffix)."""
    ts_str = time.strftime("%Y%m%d-%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    return f"{ts_str}-{short_uuid}"

class EventWriter:
    def __init__(self, file_path: Path, run_id: str):
        self.file_path = file_path
        self.run_id = run_id
        # Ensure directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        # Open in append mode, create if not exists
        self._f = open(self.file_path, "a", encoding="utf-8")

    def append(self, event: Dict[str, Any]) -> None:
        """Appends JSON line to outputs/logs/events.jsonl."""
        # Enforce minimum schema
        final_event = {
            "ts": time.time(),
            "run_id": self.run_id,
            **event
        }
        
        # Write to file
        self._f.write(json.dumps(final_event, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self):
        if self._f:
            self._f.close()

def setup_logging(output_dir: str, run_id: str) -> Tuple[logging.Logger, EventWriter]:
    """
    Sets up logging to file and console, and initializes EventWriter.
    Returns (logger, event_writer).
    """
    out_path = Path(output_dir)
    logs_dir = out_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Setup Python Logger
    logger = logging.getLogger("sr_pipeline")
    logger.setLevel(logging.DEBUG)
    logger.handlers = [] # Clear existing handlers

    # File handler
    fh = logging.FileHandler(logs_dir / "pipeline.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # 2. Setup EventWriter
    event_writer = EventWriter(logs_dir / "events.jsonl", run_id)
    set_global_event_writer(event_writer)

    return logger, event_writer
