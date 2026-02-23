from __future__ import annotations
from typing import List, Optional
import os
import logging
from pathlib import Path

from sr_pipeline.state import ResearchState
from sr_pipeline.trace import TraceEvent, now_ts
from sr_pipeline.tools import ToolRegistry, ToolPermissionError, CircuitBreakerError
from sr_pipeline.policy import PolicyV2
from sr_pipeline.stages import Stage
from sr_pipeline.logging_utils import EventWriter, set_stage

class Orchestrator:
    def __init__(self, stages: List[Stage], policy: PolicyV2, tools: ToolRegistry, output_dir: str,
                 logger: Optional[logging.Logger] = None, event_writer: Optional[EventWriter] = None,
                 api_client: Optional[APIClient] = None):
        self.stages = stages
        self.policy = policy
        self.tools = tools
        self.out = Path(output_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.event_writer = event_writer
        self.api_client = api_client

        self.trace_path = self.out / "trace.jsonl"
        self.state_path = self.out / "state.json"
        self.paper_path = self.out / "paper.md"

    def append_trace(self, ev: TraceEvent) -> None:
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(ev.to_jsonl() + "\n")

    def save_state(self, st: ResearchState) -> None:
        self.state_path.write_text(st.to_json(), encoding="utf-8")
        if st.paper_md is not None:
            self.paper_path.write_text(str(st.paper_md), encoding="utf-8")

    def run(self, st: ResearchState, max_steps: int = 50) -> ResearchState:
        # Define callback for tools to set stop_reason
        def stop_reason_cb(reason):
            st.stop_reason = reason
            
        if hasattr(self.tools, "set_stop_reason_callback"):
            self.tools.set_stop_reason_callback(stop_reason_cb)

        steps = 0
        while steps < max_steps:
            stage, decision_info = self.policy.choose(st)
            
            # Log policy decision
            if self.event_writer:
                self.event_writer.append({
                    "kind": "policy_decision",
                    **decision_info
                })
            
            if stage is None:
                if self.logger:
                    self.logger.info(f"Policy returned None: {decision_info.get('reason')}")
                break

            steps += 1
            stage_name = getattr(stage, "name", "unknown")
            st.last_stage = stage_name

            # Logging: Start
            set_stage(stage_name)
            if self.event_writer:
                self.event_writer.append({"kind": "stage_start", "stage": stage_name})
            if self.logger:
                self.logger.info(f"Starting stage: {stage_name}")

            try:
                st2 = stage.run(st, self.tools, api=self.api_client)
                self.append_trace(TraceEvent(
                    ts=now_ts(),
                    stage=stage.name,
                    action="run",
                    ok=True,
                    info={"iteration": st2.iteration},
                ))
                st = st2
                
                # Logging: End Success
                if self.event_writer:
                    self.event_writer.append({"kind": "stage_end", "stage": stage_name, "ok": True})

            except ToolPermissionError as e:
                st.failures += 1
                err_msg = str(e)
                st.stop_reason = "tool_permission_denied"
                
                if self.event_writer:
                    self.event_writer.append({
                        "kind": "exception",
                        "stage": stage_name,
                        "error_type": "ToolPermissionError",
                        "error_msg": err_msg
                    })
                    self.event_writer.append({"kind": "stage_end", "stage": stage_name, "ok": False})
                
                if self.logger:
                    self.logger.error(f"Stopping due to tool permission denial: {err_msg}")
                    
                self.append_trace(TraceEvent(
                    ts=now_ts(),
                    stage=stage.name,
                    action="run",
                    ok=False,
                    info={"failures": st.failures, "stop_reason": "tool_permission_denied"},
                    error=err_msg,
                ))
                self.save_state(st)
                break

            except CircuitBreakerError as e:
                st.failures += 1
                err_msg = str(e)
                # Ensure stop_reason is set (callback should have set it, but enforce just in case)
                if not st.stop_reason:
                    st.stop_reason = "tool_dead_end"
                
                if self.event_writer:
                    self.event_writer.append({
                        "kind": "exception",
                        "stage": stage_name,
                        "error_type": "CircuitBreakerError",
                        "error_msg": err_msg
                    })
                    self.event_writer.append({"kind": "stage_end", "stage": stage_name, "ok": False})
                
                if self.logger:
                    self.logger.error(f"Stopping due to circuit breaker: {err_msg}")
                    
                self.append_trace(TraceEvent(
                    ts=now_ts(),
                    stage=stage.name,
                    action="run",
                    ok=False,
                    info={"failures": st.failures, "stop_reason": st.stop_reason},
                    error=err_msg,
                ))
                self.save_state(st)
                break

            except Exception as e:
                st.failures += 1
                err_msg = str(e)
                
                # Logging: Exception & End Failure
                if self.event_writer:
                    self.event_writer.append({
                        "kind": "exception", 
                        "stage": stage_name, 
                        "error_type": type(e).__name__, 
                        "error_msg": err_msg
                    })
                    self.event_writer.append({"kind": "stage_end", "stage": stage_name, "ok": False})
                
                if self.logger:
                    self.logger.exception(f"Stage {stage_name} failed: {err_msg}")

                self.append_trace(TraceEvent(
                    ts=now_ts(),
                    stage=stage.name,
                    action="run",
                    ok=False,
                    info={"failures": st.failures},
                    error=err_msg,
                ))

            self.save_state(st)

            # Check for explicit stop reason (e.g. timeout, breaker, permission)
            if st.stop_reason:
                if self.logger:
                     self.logger.info(f"Stopping due to stop_reason: {st.stop_reason}")
                break

            # stop condition: paper done
            if st.paper_md is not None:
                break

        return st
