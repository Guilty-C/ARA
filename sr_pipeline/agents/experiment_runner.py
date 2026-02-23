from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class ExperimentRunnerInput:
    plan: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ExperimentRunnerOutput:
    results: Dict[str, Any]
    raw_logs: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class ExperimentRunnerAgent:
    """
    Role: Experiment Execution
    Inputs: Experiment plan
    Outputs: Results and logs
    Tools: experiment
    Failure modes: Tool error -> Returns error status and empty results
    """
    name = "ExperimentRunner"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = ExperimentRunnerInput(**inputs)
            
            # Use experiment tool
            result_dict = ctx.tools.experiment(inp.plan)
            
            out = ExperimentRunnerOutput(
                results=result_dict if isinstance(result_dict, dict) else {"raw": result_dict},
                raw_logs="Simulation logs...",
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return ExperimentRunnerOutput(results={}, raw_logs=str(e), status="error").to_dict()
