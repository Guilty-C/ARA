from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
import json

@dataclass
class ResearchState:
    # Core SR artifacts
    topic: Optional[str] = None
    background_notes: Optional[str] = None
    literature_notes: Optional[str] = None
    hypotheses: List[str] = field(default_factory=list)
    experiment_plan: Optional[str] = None
    experiment_results: Optional[Dict[str, Any]] = None
    critique_notes: Optional[str] = None
    conclusion: Optional[str] = None
    paper_md: Optional[str] = None

    # Level-2 Literature Artifacts
    annotated_bib: Optional[List[Dict[str, Any]]] = None
    evidence_table: Optional[List[Dict[str, Any]]] = None
    missing_matrix: Optional[List[Dict[str, Any]]] = None
    work_records: Optional[List[Dict[str, Any]]] = None
    literature_stats: Optional[Dict[str, Any]] = None
    clusters_summary: Optional[Dict[str, Any]] = None

    # Level-3 Topic & Background Artifacts
    ranked_topics: Optional[List[Dict[str, Any]]] = None
    concept_map: Optional[Dict[str, Any]] = None
    canonical_baselines: Optional[List[Dict[str, Any]]] = None
    metrics_taxonomy: Optional[List[Dict[str, Any]]] = None

    # Level-4 Experiment Artifacts
    experiment_runs: List[Dict[str, Any]] = field(default_factory=list)
    
    # Level-5 Critic & Iteration
    critic_report: Optional[Dict[str, Any]] = None
    iteration_state: Dict[str, Any] = field(default_factory=lambda: {"attempt": 0, "max_iters": 2})
    iter_state: Dict[str, Any] = field(
        default_factory=lambda: {"iter_id": None, "history": [], "best_score": 0.0, "best_iter_dir": None}
    )

    # Control knobs for dummy iteration
    iteration: int = 0
    max_iterations: int = 3
    last_stage: Optional[str] = None
    failures: int = 0
    
    # Determinism & Hygiene
    run_id: Optional[str] = None
    config_hash: Optional[str] = None
    env_snapshot: Optional[Dict[str, str]] = None
    stop_reason: Optional[str] = None # For explicit dead ends
    budgets: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ResearchState":
        return ResearchState(**d)
