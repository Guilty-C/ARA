# Agentic SR Pipeline

A modular, tool-augmented agentic pipeline for scientific research (SR) workflows. This project implements a policy-driven orchestrator that executes research stages (topic selection, literature review, hypothesis generation, experimentation, critique, and paper drafting) by interacting with external tools via a unified HTTP gateway. It includes comprehensive logging, automated auditing, and a fail-fast mechanism for robust execution.

## Project Overview

This repository provides a skeleton for an autonomous research agent. Key components include:
- **Orchestrator**: Manages the research state and executes stages based on a decision policy.
- **Stages**: Modular units of work (e.g., `LiteratureStage`, `ExperimentStage`) that consume and update the shared `ResearchState`.
- **Tool Gateway & API Port**: A centralized HTTP gateway that routes tool calls and API requests to an external server (currently a dummy server), ensuring decoupling between agent logic and external services.
- **Agents Package**: A set of modular agent skeletons (e.g., `TopicScoutAgent`, `LiteratureReviewAgent`) designed to handle specific research phases.
- **Auditing**: A dedicated auditor script that validates execution invariants, ensuring reproducibility and correctness.

## Quickstart

### Prerequisites
- Python 3.10+
- Local shell (bash/powershell)

### Quickstart Commands

1. Install editable package:
```bash
pip install -e .
```

2. Start the local dummy tool/API server (new terminal):
```bash
python tools_server/dummy_tool_server.py
```

3. Point CLI at the local server and run doctor:
```bash
export TOOL_API_BASE=http://127.0.0.1:8088
export API_BASE_URL=http://127.0.0.1:8088/api
python -m ara doctor
```

4. Run the pipeline via CLI:
```bash
python -m ara run --output-dir outputs --initial-state-json '{"topic":"industrial anomaly detection","constraints":{"compute":"low"}}'
```

5. Print a concise report:
```bash
python -m ara report outputs
```

## Configuration

The pipeline is configured via environment variables.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `TOOL_API_BASE` | `http://127.0.0.1:8088` | Base URL for tool endpoints. |
| `API_BASE_URL` | `http://127.0.0.1:8088/api` | Base URL for generic API endpoints. |
| `OUTPUT_DIR` | `outputs` | Directory where artifacts and logs are written. |
| `FAIL_FAST_TOOL` | `0` | If `1`, raises exception on tool failure. |
| `FAIL_FAST_API` | `0` | If `1`, raises exception on API failure (otherwise logs warning). |

## Real API (OpenAlex)

Provider mode is controlled by `PROVIDER_MODE`:
- `REPLAY` (default): reads local fixtures and does not call real OpenAlex network APIs.
- `LIVE`: calls OpenAlex `/works` and includes `OPENALEX_API_KEY` as `api_key` request parameter.

Optional variables:
- `OPENALEX_API_KEY`: required in `LIVE` mode (highest priority).
- `OPENALEX_API_KEY_FILE`: optional key file path, default `data/secrets/openalex_api_key.txt`.
- `OPENALEX_MAILTO`: optional OpenAlex `mailto` parameter.

Acceptance commands:
```bash
python -m ara live-smoke --mode REPLAY --min-works 10
PROVIDER_MODE=LIVE OPENALEX_API_KEY=... python -m ara live-smoke --mode LIVE --min-works 10
python -m ara set-openalex-key
PROVIDER_MODE=LIVE python -m ara live-smoke --mode LIVE --min-works 10
```

Key file mode is recommended for local usage. Do not commit key files to git. The default path `data/secrets/` is ignored by `.gitignore`.

## Architecture & Endpoints

The system connects to external services via two main portholes. The included `dummy_tool_server.py` implements both for testing.

### 1. Tool Gateway (`POST /tool/<name>`)
Used for high-level agent tools.
- `/tool/search`: Semantic search.
- `/tool/summarize`: Text summarization.
- `/tool/draft`: Content generation.
- `/tool/experiment`: Running simulations.
- `/tool/critique`: Evaluating results.

Example:
```bash
curl -X POST http://127.0.0.1:8088/tool/search -d '{"query": "ML optimization"}'
```

### 2. API Port (`POST /api/<endpoint>`)
Used for lower-level API interactions (e.g., LLM completion, health checks).
- `/api/ping`: Health check.
- `/api/llm_complete`: Raw LLM completion.

Example:
```bash
curl -X POST http://127.0.0.1:8088/api/ping -d '{}'
```

## Agents Package

The `sr_pipeline/agents/` package contains installed module skeletons for specialized research agents. These are designed to eventually replace the monolithic logic in `stages.py`.

**Available Agents:**
- **TopicScoutAgent**: Topic generation and feasibility check.
- **BackgroundResearchAgent**: Initial domain exploration.
- **LiteratureReviewAgent**: RAG-based literature search and synthesis.
- **HypothesisGeneratorAgent**: Formulating hypotheses.
- **ExperimentDesignerAgent**: Creating experiment plans.
- **ExperimentRunnerAgent**: Executing experiments.
- **CriticEvaluatorAgent**: Reviewing results against criteria.
- **IterationManagerAgent**: Deciding whether to refine or conclude.
- **ConclusionComposerAgent**: Synthesizing final findings.
- **PaperAndFiguresAgent**: Compiling the final paper.

**Smoke Test:**
Verify the agents are correctly installed:
```bash
python -c "from sr_pipeline.agents import TopicScoutAgent, LiteratureReviewAgent; print('import_ok')"
```

## Outputs and Logging

Artifacts are generated in the configured `OUTPUT_DIR` (default: `outputs/`).

- **`state.json`**: The final snapshot of the `ResearchState`.
- **`paper.md`**: The generated research paper draft.
- **`logs/pipeline.log`**: Human-readable logs containing info, warnings, and stack traces.
- **`logs/events.jsonl`**: Structured event stream used for auditing.

### Event Schema (`events.jsonl`)
Each line is a JSON object. Common event kinds:
- `stage_start`, `stage_end`, `exception`.
- `policy_decision`: Records the orchestrator's decision logic.
- `tool_call`: Details of tool execution.
- `api_call`: Details of API interaction (Endpoint, Latency, Payload/Response bytes).

### Audit Expectations
The `audit_logs.py` script checks for:
1. **Stage Integrity**: `stage_start` matches `stage_end`.
2. **Tool Coverage**: Executed stages must perform tool calls.
3. **Error Free**: No unhandled exceptions.
4. **Artifacts**: `paper.md` must be valid.

## Directory Structure

```text
.
├── run_pipeline.py           # Main entry point
├── audit_logs.py             # Log verification tool
├── test_pipeline.py          # Integration test suite
├── sr_pipeline/              # Core package
│   ├── __init__.py
│   ├── state.py              # Data classes for research state
│   ├── tools.py              # HTTP tool gateway & registry
│   ├── api_port.py           # Generic API client port
│   ├── stages.py             # Implementation of research stages
│   ├── policy.py             # Decision logic (PolicyV2)
│   ├── orchestrator.py       # Main loop & state management
│   ├── logging_utils.py      # Logging configuration
│   └── agents/               # Specialized agent modules
│       ├── __init__.py
│       ├── base.py
│       └── ... (agent modules)
└── tools_server/
    └── dummy_tool_server.py  # Mock external API/Tool server
```

## How It Works

1. **Initialization**: `run_pipeline.py` initializes the `Orchestrator`, `ToolRegistry`, `APIClient`, and `Policy`.
2. **Orchestration Loop**:
   - The **Policy** (`PolicyV2`) evaluates the state to select the next optimal stage based on progress, cost, and risk.
   - The **Orchestrator** executes the selected stage.
   - **Stages** utilize `tools` and `api` ports to perform tasks.
3. **Completion**: The loop runs until the policy determines the paper is complete or a stop condition (e.g., max iterations) is met.

## License

No license specified yet.
