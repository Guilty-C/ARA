from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
import logging
from sr_pipeline.state import ResearchState
from sr_pipeline.stages import Stage

class PolicyV2:
    """
    Production-style controller with:
    - Explicit graph transitions
    - Deterministic utility scoring (Progress - Cost - Risk)
    - Detailed decision auditing
    """
    
    # Graph of allowed transitions: last_stage -> {allowed_next_stages}
    STAGE_GRAPH = {
        "topic": {"background"},
        "background": {"literature"},
        "literature": {"hypothesis"},
        "hypothesis": {"experiment"},
        "experiment": {"critic"},
        "critic": {"critic", "experiment", "conclusion"}, # Loop (via experiment re-run) or exit
        "iterate": {"experiment", "iterate", "conclusion"}, # Legacy
        "conclusion": {"paper"},
        "paper": set(),
    }

    # Estimated cost (arbitrary units, e.g., cents or tokens) per stage
    COST_ESTIMATE = {
        "topic": 1,
        "background": 5,
        "literature": 10,
        "hypothesis": 5,
        "experiment": 20,
        "critic": 5,
        "iterate": 2,
        "conclusion": 10,
        "paper": 15,
    }

    # Weight of filling a missing artifact (Progress Score)
    ARTIFACT_WEIGHTS = {
        "topic": 100,
        "background_notes": 100,
        "literature_notes": 100,
        "hypotheses": 100,
        "experiment_plan": 50,    # Plan is intermediate
        "experiment_results": 200, # Results are high value
        "critic_report": 50,
        "conclusion": 300,        # Near goal
        "paper_md": 500,          # Goal
    }

    # Map stage -> artifact it produces (simplified)
    STAGE_PRODUCES = {
        "topic": "topic",
        "background": "background_notes",
        "literature": "literature_notes",
        "hypothesis": "hypotheses",
        "experiment": "experiment_results",
        "critic": "critic_report",
        "iterate": "experiment_plan", # sort of updates plan
        "conclusion": "conclusion",
        "paper": "paper_md",
    }

    def __init__(self, stages: List[Stage], logger: Optional[logging.Logger] = None):
        self.stages = stages
        self.stage_map = {s.name: s for s in stages}
        self.logger = logger

    def _calculate_progress_score(self, stage_name: str, st: ResearchState) -> int:
        """Calculate potential progress score if this stage runs."""
        artifact = self.STAGE_PRODUCES.get(stage_name)
        if not artifact:
            return 0
        
        # Check if artifact is currently missing or empty
        val = getattr(st, artifact, None)
        is_missing = not val or (isinstance(val, list) and len(val) == 0)
        
        if is_missing:
            return self.ARTIFACT_WEIGHTS.get(artifact, 0)
        
        # If not missing, maybe we are refining it? (Iterate case)
        if stage_name == "iterate":
            return 50 # Small progress for iteration
        
        return 0 # Already done, so low value to run again unless forced

    def choose(self, st: ResearchState) -> Tuple[Optional[Stage], Dict[str, Any]]:
        candidates_info = []
        runnable_candidates = []

        # 1. Analyze all stages
        for s in self.stages:
            info = {
                "stage": s.name,
                "runnable": False,
                "legal": False,
                "reject_reason": None,
                "progress": 0,
                "cost": 0,
                "risk": 0,
                "total_score": 0
            }

            # Runnable Check
            try:
                if s.can_run(st):
                    info["runnable"] = True
                else:
                    info["reject_reason"] = "condition_met=False"
            except Exception as e:
                info["reject_reason"] = f"can_run_exception: {e}"

            # Legal Transition Check
            if st.last_stage:
                allowed = self.STAGE_GRAPH.get(st.last_stage, set())
                if s.name in allowed:
                    info["legal"] = True
                else:
                    if info["runnable"]: # Only set reject reason if it was runnable
                        info["reject_reason"] = f"graph_violation: {st.last_stage}->{s.name} forbidden"
            else:
                # First step: allow any runnable (usually topic)
                info["legal"] = True

            # Scoring (only if runnable and legal)
            if info["runnable"] and info["legal"]:
                # Progress
                info["progress"] = self._calculate_progress_score(s.name, st)
                
                # Cost (negative)
                info["cost"] = self.COST_ESTIMATE.get(s.name, 5)
                
                # Risk (negative) - penalize heavily if we have failures
                # Simple risk model: global failures * 10
                info["risk"] = st.failures * 10
                
                # Total Score
                info["total_score"] = info["progress"] - info["cost"] - info["risk"]
                
                runnable_candidates.append(info)
            
            candidates_info.append(info)

        # 2. Select Best Candidate
        chosen_stage = None
        stop_reason = None

        if not runnable_candidates:
            stop_reason = "dead_end: no legal runnable stages"
            # Try to find why
            if any(c["runnable"] for c in candidates_info):
                stop_reason = "dead_end: stages runnable but blocked by graph"
        else:
            # Sort by total_score descending
            # Tie-break: maintain definition order (stable sort)
            runnable_candidates.sort(key=lambda x: x["total_score"], reverse=True)
            best = runnable_candidates[0]
            chosen_stage = self.stage_map[best["stage"]]

        # 3. Construct Decision Payload
        missing_artifacts = [
            k for k in self.ARTIFACT_WEIGHTS.keys() 
            if not getattr(st, k, None) or (isinstance(getattr(st, k), list) and len(getattr(st, k)) == 0)
        ]

        decision_payload = {
            "chosen": chosen_stage.name if chosen_stage else None,
            "stop_reason": stop_reason,
            "candidates": candidates_info, # Full detail for audit
            "state_digest": {
                "missing": missing_artifacts,
                "iteration": st.iteration,
                "failures": st.failures,
                "last_stage": st.last_stage
            }
        }

        return chosen_stage, decision_payload
