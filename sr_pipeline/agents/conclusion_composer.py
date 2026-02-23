from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any, TYPE_CHECKING
from sr_pipeline.agents.base import BaseAgent, AgentContext, emit_agent_event, safe_trunc

if TYPE_CHECKING:
    from sr_pipeline.state import ResearchState

@dataclass
class ConclusionComposerInput:
    all_findings_summary: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ConclusionComposerOutput:
    conclusion_text: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class ConclusionComposerAgent:
    """
    Role: Conclusion Drafting
    Inputs: Summary of findings
    Outputs: Draft conclusion text
    Tools: draft
    Failure modes: Tool error -> Returns fallback conclusion
    """
    name = "ConclusionComposer"

    def run(self, ctx: AgentContext, *, state: "ResearchState", inputs: Dict[str, Any]) -> Dict[str, Any]:
        emit_agent_event(ctx, "agent_run_start", self.name, payload={"inputs": safe_trunc(inputs)})
        
        try:
            inp = ConclusionComposerInput(**inputs)
            
            # Use draft tool
            draft = ctx.tools.draft(f"Write conclusion for: {inp.all_findings_summary}")
            
            out = ConclusionComposerOutput(
                conclusion_text=draft,
                status="success"
            )
            
            emit_agent_event(ctx, "agent_run_end", self.name, payload=out.to_dict(), ok=True)
            return out.to_dict()
            
        except Exception as e:
            emit_agent_event(ctx, "agent_error", self.name, payload={}, ok=False, error_type=type(e).__name__, error_msg=str(e))
            return ConclusionComposerOutput(conclusion_text="Conclusion unavailable due to error", status="error").to_dict()
