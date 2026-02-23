from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class ExperimentDesignerInput:
    hypotheses: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ExperimentDesignerOutput:
    experiment_plan: str
    ablations: List[str]
    acceptance_criteria: List[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class ExperimentDesignerAgent:
    """
    Role: Experiment Design
    Inputs: Hypotheses
    Outputs: Experiment plan, ablations, acceptance criteria
    Tools: draft
    Failure modes: Tool error -> Returns fallback plan
    """
    name = "ExperimentDesigner"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = ExperimentDesignerInput(**inputs)
            
            # Use draft tool
            plan_draft = ctx.tools.draft("Design experiment for hypotheses")
            
            out = ExperimentDesignerOutput(
                experiment_plan=f"Plan: {safe_trunc(plan_draft, 50)}",
                ablations=["Remove module X", "Reduce dataset size"],
                acceptance_criteria=["Accuracy > 90%", "Latency < 100ms"],
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return ExperimentDesignerOutput(experiment_plan="", ablations=[], acceptance_criteria=[], status="error").to_dict()
