from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Protocol, TYPE_CHECKING
import logging
import time

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState
    from sr_pipeline.tools import ToolRegistry
    # Avoid hard import of APIClient to keep it optional/loose dependency
    # from sr_pipeline.api_port import APIClient

def safe_trunc(obj: Any, limit: int = 800) -> str:
    """Truncate huge strings/objects for safe logging."""
    s = str(obj)
    if len(s) > limit:
        return s[:limit] + f"...({len(s) - limit} chars truncated)"
    return s

@dataclass
class AgentContext:
    """
    Context passed to agents during execution.
    Contains tools, optional API, logging, and tracing handles.
    """
    tools: ToolRegistry
    api: Optional[Any] = None
    logger: Optional[logging.Logger] = None
    event_writer: Optional[Any] = None # Has append(dict) method
    run_id: Optional[str] = None
    stage: Optional[str] = None

class BaseAgent(Protocol):
    """Protocol for all agents."""
    name: str

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the agent's logic.
        
        Args:
            ctx: Execution context (tools, logs, etc.)
            state: Global research state (read-only encouraged, but mutable)
            inputs: Agent-specific input dictionary
            
        Returns:
            Dict[str, Any]: JSON-serializable output
        """
        ...

def emit_agent_event(ctx: AgentContext, kind: str, agent: str, payload: Any, 
                     ok: Optional[bool] = None, error_type: Optional[str] = None, 
                     error_msg: Optional[str] = None) -> None:
    """
    Writes an event dict to ctx.event_writer if present.
    """
    if ctx.event_writer:
        evt = {
            "ts": time.time(),
            "run_id": ctx.run_id,
            "kind": kind,
            "agent": agent,
            "stage": ctx.stage,
            "payload": payload,
        }
        if ok is not None:
            evt["ok"] = ok
        if error_type:
            evt["error_type"] = error_type
        if error_msg:
            evt["error_msg"] = error_msg
            
        ctx.event_writer.append(evt)
