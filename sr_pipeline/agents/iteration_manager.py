from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class IterationManagerInput:
    critique_notes: str
    current_iteration: int
    max_iterations: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class IterationManagerOutput:
    decision: str  # "iterate" or "conclude"
    reasoning: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class IterationManagerAgent:
    """
    Role: Iteration Management
    Inputs: Critique notes and iteration counters
    Outputs: Decision to iterate or conclude
    Tools: None (heuristic logic)
    Failure modes: Logic error -> Returns conclude fallback
    """
    name = "IterationManager"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = IterationManagerInput(**inputs)
            
            # Simple heuristic
            decision = "conclude"
            reason = "Max iterations reached"
            
            if inp.current_iteration < inp.max_iterations:
                decision = "iterate"
                reason = "Critique suggests improvements and budget remains"
                
            out = IterationManagerOutput(
                decision=decision,
                reasoning=reason,
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return IterationManagerOutput(decision="conclude", reasoning=f"Error: {str(e)}", status="error").to_dict()
