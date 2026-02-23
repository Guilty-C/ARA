from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class TopicScoutInput:
    requirements: str = "Find a feasible research topic"
    constraints: str = "No human subjects, ML systems focus"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class TopicScoutOutput:
    candidates: List[str]
    rationale: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class TopicScoutAgent:
    """
    Role: Topic Generation (Phase 1)
    Inputs: Constraints and broad domain requirements
    Outputs: List of feasible research topic candidates
    Tools: search
    Failure modes: Search returns empty -> Returns fallback topic
    """
    name = "TopicScout"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = TopicScoutInput(**inputs)
            
            # Use search tool
            query = f"Research topics in {inp.constraints}"
            search_results = ctx.tools.search(query)
            
            # Optional API usage if available
            if ctx.api:
                try:
                    ctx.api.ping()
                except Exception:
                    pass 

            candidates = [
                f"Topic 1 based on {safe_trunc(search_results, 50)}",
                "Topic 2: Auto-tuning LLM pipelines",
                "Topic 3: Efficient RAG systems"
            ]
            
            out = TopicScoutOutput(
                candidates=candidates,
                rationale="Selected based on feasibility and novelty.",
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            # Safe fallback
            return TopicScoutOutput(
                candidates=["Fallback Topic: Robustness Analysis"], 
                rationale=f"Error during generation: {str(e)}", 
                status="error"
            ).to_dict()
