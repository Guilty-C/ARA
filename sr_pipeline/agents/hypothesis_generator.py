from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class HypothesisGeneratorInput:
    literature_context: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class HypothesisGeneratorOutput:
    hypotheses: List[str]
    discriminating_tests: List[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class HypothesisGeneratorAgent:
    """
    Role: Hypothesis Generation
    Inputs: Literature context
    Outputs: List of hypotheses and discriminating tests
    Tools: draft
    Failure modes: Tool error -> Returns fallback hypotheses
    """
    name = "HypothesisGenerator"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = HypothesisGeneratorInput(**inputs)
            
            # Use draft tool
            draft = ctx.tools.draft("Propose 3 hypotheses")
            
            out = HypothesisGeneratorOutput(
                hypotheses=[f"H1 based on {safe_trunc(draft, 20)}", "H2: Alternative explanation"],
                discriminating_tests=["Test A: A/B comparison", "Test B: Stress test"],
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return HypothesisGeneratorOutput(hypotheses=[], discriminating_tests=[], status="error").to_dict()
