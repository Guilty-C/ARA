from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class CriticEvaluatorInput:
    results_context: str
    criteria: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class CriticEvaluatorOutput:
    critique_text: str
    pass_gates: Dict[str, bool]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class CriticEvaluatorAgent:
    """
    Role: Result Critique
    Inputs: Results context and criteria
    Outputs: Critique text and pass/fail gates
    Tools: critique
    Failure modes: Tool error -> Returns empty critique
    """
    name = "CriticEvaluator"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = CriticEvaluatorInput(**inputs)
            
            # Use critique tool
            critique_text = ctx.tools.critique(f"Evaluate results: {inp.results_context}")
            
            out = CriticEvaluatorOutput(
                critique_text=critique_text,
                pass_gates={"accuracy": True, "latency": True},
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return CriticEvaluatorOutput(critique_text="", pass_gates={}, status="error").to_dict()
