from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class LiteratureReviewInput:
    topic: str
    background_context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class LiteratureReviewOutput:
    bib_stub: str
    missing_knowledge: List[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class LiteratureReviewAgent:
    """
    Role: Literature Review (RAG-first)
    Inputs: Topic and background context
    Outputs: Annotated bibliography stub and missing knowledge gaps
    Tools: search, summarize
    Failure modes: Search failure -> Returns minimal stub
    """
    name = "LiteratureReview"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = LiteratureReviewInput(**inputs)
            
            # RAG-first approach: Search then summarize
            search_res = ctx.tools.search(f"Key papers for {inp.topic}")
            summary_res = ctx.tools.summarize(f"Summarize literature from: {safe_trunc(search_res, 200)}")
            
            out = LiteratureReviewOutput(
                bib_stub=f"Annotated bibliography based on: {safe_trunc(summary_res, 100)}",
                missing_knowledge=["Gap 1: Scalability", "Gap 2: Robustness"],
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return LiteratureReviewOutput(bib_stub="Error reading literature", missing_knowledge=[], status="error").to_dict()
