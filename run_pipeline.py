from __future__ import annotations
import os
import sys
import hashlib

from sr_pipeline.tools import HTTPToolPort, ToolRegistry
from sr_pipeline.state import ResearchState
from sr_pipeline.orchestrator import Orchestrator
from sr_pipeline.stages import (
    TopicStage, BackgroundStage, LiteratureStage, HypothesisStage,
    ExperimentStage, CriticStage, IterateStage, ConclusionStage, PaperStage
)
from sr_pipeline.logging_utils import init_run_id, setup_logging

from sr_pipeline.api_port import HTTPAPIPort, APIClient
from sr_pipeline.policy import PolicyV2

def main() -> int:
    tool_base = os.environ.get("TOOL_API_BASE", "http://127.0.0.1:8088")
    api_base = os.environ.get("API_BASE_URL", "http://127.0.0.1:8088/api")
    out_dir = os.environ.get("OUTPUT_DIR", "outputs")
    fail_fast_env = os.environ.get("FAIL_FAST_TOOL", "0")
    fail_fast = fail_fast_env == "1"

    # Setup Logging
    run_id = init_run_id()
    logger, event_writer = setup_logging(out_dir, run_id)
    logger.info(f"Pipeline run_id={run_id} started. Output dir={out_dir} fail_fast={fail_fast}")

    # Initialize Tools with logging
    port = HTTPToolPort(tool_base, logger=logger, event_writer=event_writer, fail_fast=fail_fast)
    tools = ToolRegistry(port)

    # Initialize API Port
    api_port = HTTPAPIPort(api_base, logger=logger, event_writer=event_writer, fail_fast=fail_fast)
    api_client = APIClient(api_port)

    stages = [
        TopicStage(),
        BackgroundStage(),
        LiteratureStage(),
        HypothesisStage(),
        ExperimentStage(),
        CriticStage(),
        IterateStage(),
        ConclusionStage(),
        PaperStage(),
    ]

    policy = PolicyV2(stages=stages, logger=logger)
    
    # Initialize Orchestrator with logging
    orch = Orchestrator(stages=stages, policy=policy, tools=tools, output_dir=out_dir,
                        logger=logger, event_writer=event_writer, api_client=api_client)

    st0 = ResearchState()
    
    # Determinism anchors
    st0.run_id = run_id
    config_str = f"{tool_base}|{api_base}|{fail_fast_env}|{sys.version}"
    st0.config_hash = hashlib.sha256(config_str.encode()).hexdigest()[:16]
    st0.env_snapshot = {
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "TOOL_API_BASE": tool_base,
        "API_BASE_URL": api_base,
        "FAIL_FAST_TOOL": fail_fast_env
    }

    try:
        st_final = orch.run(st0)
        
        # Check for failures in state (since orchestrator suppresses some exceptions)
        if st_final.failures > 0:
             logger.error(f"Pipeline finished with {st_final.failures} failures.")
             # Mark as failed if we have failures, even if we reached "paper" (technically a degraded run)
             # But let's respect the fact that it might have produced something.
             # However, for "FAIL test", we need exit code 1 if things broke.
             return 1

        print("final_topic=", st_final.topic)
        print("final_stage=", st_final.last_stage)
        print("final_outputs_dir=", out_dir)
        
        logger.info("Pipeline finished successfully.")
    except Exception as e:
        logger.error("Pipeline crashed.", exc_info=True)
        return 1
    finally:
        event_writer.close()
        
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
