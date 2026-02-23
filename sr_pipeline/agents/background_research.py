from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class BackgroundResearchInput:
    topic: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class BackgroundResearchOutput:
    summary: str
    key_concepts: list[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class BackgroundResearchAgent:
    """
    Role: Background Knowledge Gathering
    Inputs: Selected topic
    Outputs: Summary and key concepts
    Tools: summarize
    Failure modes: Tool error -> Returns empty summary
    """
    name = "BackgroundResearch"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = BackgroundResearchInput(**inputs)
            
            # Use summarize tool
            summary_text = ctx.tools.summarize(f"Background for {inp.topic}")
            
            out = BackgroundResearchOutput(
                summary=summary_text,
                key_concepts=["Concept A", "Concept B"],
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return BackgroundResearchOutput(summary="Fallback summary due to error", key_concepts=[], status="error").to_dict()
