import json
import os
from pathlib import Path
from typing import Dict, Any, List
from sr_pipeline.state import ResearchState

class CriticAgent:
    def _load_blocked_claims_count(self) -> int:
        out_dir = Path(os.environ.get("OUTPUT_DIR", "outputs"))
        claims_path = out_dir / "claims.json"
        if not claims_path.exists():
            return 0
        try:
            payload = json.loads(claims_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        if not isinstance(payload, list):
            return 0
        return sum(1 for row in payload if isinstance(row, dict) and bool(row.get("blocked")))

    def _build_structured_score(self, st: ResearchState, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        evidence_rows = st.evidence_table if isinstance(st.evidence_table, list) else []
        evidence_count = len(evidence_rows)
        missing_support = sum(1 for r in evidence_rows if isinstance(r, dict) and not r.get("support_snippets"))
        blocked_claims_count = self._load_blocked_claims_count()
        missing_support_total = missing_support + blocked_claims_count
        has_experiment = isinstance(st.experiment_results, dict)
        has_seed_sweep = bool(
            isinstance(st.experiment_results, dict)
            and isinstance(st.experiment_results.get("sanity"), dict)
            and isinstance(st.experiment_results.get("sanity", {}).get("seed_sweep"), dict)
            and st.experiment_results.get("sanity", {}).get("seed_sweep", {}).get("pass") is True
        )

        rubric = {
            "coverage": min(10, evidence_count),
            "correctness": 10 if not any(i.get("severity") == "HIGH" for i in issues) else 4,
            "evidence_quality": max(0, 10 - min(10, missing_support_total * 2)),
            "novelty": min(10, len(st.hypotheses) * 3) if isinstance(st.hypotheses, list) else 0,
            "reproducibility": 10 if has_experiment else 2,
            "ablation_quality": 10 if has_seed_sweep else 3,
        }

        penalties: List[Dict[str, Any]] = []
        if missing_support_total > 0:
            penalties.append(
                {
                    "code": "claim_without_evidence",
                    "points": min(10, missing_support_total * 2),
                    "count": missing_support_total,
                }
            )
        if has_experiment:
            ablations = st.experiment_results.get("ablations", [])
            if not isinstance(ablations, list) or len(ablations) == 0:
                penalties.append(
                    {
                        "code": "missing_ablation",
                        "points": 6,
                        "count": 1,
                    }
                )
        if evidence_count > 0 and (not isinstance(st.annotated_bib, list) or len(st.annotated_bib) == 0):
            penalties.append(
                {
                    "code": "inconsistent_citations",
                    "points": 4,
                    "count": 1,
                }
            )
        penalties = sorted(
            penalties,
            key=lambda p: (str(p.get("code", "")), int(p.get("points", 0)), int(p.get("count", 0))),
        )

        recommendations_set: set[str] = set()
        if missing_support_total > 0:
            recommendations_set.add("extract_evidence")
            recommendations_set.add("fetch_more_evidence")
        if any(p.get("code") == "missing_ablation" for p in penalties):
            recommendations_set.add("add_ablation")
        if not has_seed_sweep:
            recommendations_set.add("rerun_with_seed")
        if evidence_count < 3:
            recommendations_set.add("fetch_more_evidence")
        if not recommendations_set:
            recommendations_set.add("stop_evidence_gap")
        recommendations = sorted(recommendations_set)

        raw_total = sum(int(v) for v in rubric.values())
        raw_max = 60
        base = round(100 * raw_total / raw_max)
        penalty_points = sum(int(p.get("points", 0)) for p in penalties)
        penalty_points_scaled = round(penalty_points * 2.5)
        overall = max(0, min(100, base - penalty_points_scaled))
        return {
            "overall_score": overall,
            "rubric": rubric,
            "penalties": penalties,
            "recommended_actions": recommendations,
            "rationale": "Deterministic 0-100 score: normalized rubric total minus scaled structured penalties.",
        }

    def run(self, st: ResearchState) -> Dict[str, Any]:
        issues = []
        
        # 1. Level 2 Checks
        if not st.evidence_table:
            issues.append({
                "code": "C_L2_EMPTY",
                "severity": "HIGH",
                "message": "Evidence table is empty.",
                "evidence_refs": []
            })
        else:
            for i, row in enumerate(st.evidence_table):
                if not row.get("support_snippets"):
                    issues.append({
                        "code": "C_L2_NO_SUPPORT",
                        "severity": "HIGH",
                        "message": f"Claim {i} has no support snippets.",
                        "evidence_refs": []
                    })
                    
        # 2. Level 3 Checks
        if st.ranked_topics:
            for i, t in enumerate(st.ranked_topics):
                evidence = t.get("evidence", [])
                has_lit = False
                for ev in evidence:
                    if ev.get("source_type") == "literature" and ev.get("paper_id"):
                        has_lit = True
                        
                    # Check provenance
                    required = ["title", "section", "span_start", "span_end", "snippet_text", "score", "ref"]
                    if any(f not in ev for f in required):
                        issues.append({
                            "code": "C_L3_MISSING_PROV",
                            "severity": "HIGH",
                            "message": f"Topic {i} evidence missing provenance.",
                            "evidence_refs": []
                        })
                        
                if not has_lit:
                    issues.append({
                        "code": "C_L3_NO_LIT",
                        "severity": "HIGH",
                        "message": f"Topic {i} has no literature evidence.",
                        "evidence_refs": []
                    })
                    
        # 3. Level 4 Checks
        # Get latest run metrics
        latest_metrics = st.experiment_results
        if st.experiment_runs:
            # Load from latest run dir if possible, or use what's in state if updated
            # State might hold old legacy metrics if we didn't update it fully.
            # But ExperimentStage updates st.experiment_results with metrics.json content.
            pass
            
        if latest_metrics:
            sanity = latest_metrics.get("sanity", {})
            
            # Label Shuffle
            ls = sanity.get("label_shuffle", {})
            if not ls.get("pass"):
                issues.append({
                    "code": "C_LABEL_SHUFFLE_TOO_HIGH",
                    "severity": "HIGH",
                    "message": "Label shuffle sanity check failed.",
                    "evidence_refs": []
                })
                
            # Leakage
            lc = sanity.get("leakage_check", {})
            if not lc.get("pass"):
                issues.append({
                    "code": "C_LEAKAGE",
                    "severity": "HIGH",
                    "message": "Data leakage detected.",
                    "evidence_refs": []
                })
                
            # Std dev check
            agg = latest_metrics.get("aggregate", {})
            std = agg.get("accuracy_std", 0.0)
            seeds = latest_metrics.get("seeds", {})
            if std == 0.0 and len(seeds) > 1:
                # Check if all accuracies are identical (could be 0.0 or 1.0 or whatever)
                # If they are not 0.0 or 1.0, it's suspicious?
                # Actually, 0.0 std is fine if deterministic, but might imply lack of variance in seed usage.
                # Prompt: "Level4 “std=0.0 across seeds” AND seeds are claimed meaningful -> severity MED (unless explicitly justified)"
                # We won't block on this for toy, but let's add it.
                issues.append({
                    "code": "C_ZERO_STD",
                    "severity": "MED",
                    "message": "Zero standard deviation across seeds.",
                    "evidence_refs": []
                })

        # Recommendations & Plan
        high_issues = [i for i in issues if i["severity"] == "HIGH"]
        critic_pass = len(high_issues) == 0
        
        recommendations = []
        next_steps = []
        
        if not critic_pass:
            # Check for auto-fixable issues
            fixable = False
            for issue in high_issues:
                if issue["code"] == "C_LABEL_SHUFFLE_TOO_HIGH":
                    recommendations.append({
                        "action": "tighten_threshold",
                        "details": "Reduce margin for label shuffle check and rerun."
                    })
                    next_steps.append("Reduce margin")
                    fixable = True
                else:
                    recommendations.append({
                        "action": "stop_fail",
                        "details": f"Unfixable issue: {issue['code']}"
                    })
                    
            if not fixable:
                next_steps.append("Stop pipeline")
        else:
            recommendations.append({"action": "pass", "details": "No critical issues."})
            next_steps.append("Proceed to completion")

        # Iteration Plan
        # Get current iteration from state or default
        current_iter = st.iteration # This is the "dummy" iteration from older stages.
        # We should use a structured iteration object in state if available, or just reuse st.iteration
        # The prompt says: "state.iteration = {attempt, max_iters, last_critic_pass, ...}"
        # But st.iteration is an int in definition.
        # We can store the dict in a new field or overload it?
        # Let's add a new field 'iteration_state' to ResearchState?
        # Or just return the plan here and let the Stage handle state update.
        
        # We need to know current attempt to plan next.
        # Assuming the caller (CriticStage) manages the attempt counter.
        
        return {
            "critic_pass": critic_pass,
            "issues": issues,
            "recommendations": recommendations,
            "score": self._build_structured_score(st, issues),
            "iteration_plan": {
                "max_iters": 2,
                "attempt": 0, # Placeholder, will be updated by Stage
                "next_steps": next_steps
            }
        }
