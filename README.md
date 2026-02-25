# Agentic SR Pipeline

A modular, tool-augmented agentic pipeline for scientific research (SR) workflows. The CLI supports deterministic `REPLAY` mode by default and optional `LIVE` mode for real network calls.

## Quickstart (REPLAY)

### bash
```bash
pip install -e .
python -m ara doctor
cat > outputs_test/run_config.json << 'JSON'
{"topic":"industrial anomaly detection","constraints":{"compute":"low"}}
JSON
python -m ara run --output-dir outputs_test/run_replay --config outputs_test/run_config.json
python -m ara report outputs_test/run_replay
```

### PowerShell
```powershell
pip install -e .
python -m ara doctor
@"
{"topic":"industrial anomaly detection","constraints":{"compute":"low"}}
"@ | Set-Content -Encoding utf8 outputs_test/run_config.json
python -m ara run --output-dir outputs_test/run_replay --config outputs_test/run_config.json
python -m ara report outputs_test/run_replay
```

## Quickstart (LIVE)

`LIVE` mode needs outbound network access and credentials:
- `OPENALEX_API_KEY`
- `UNPAYWALL_EMAIL`
- Recommended: `PROVIDER_DISABLE_PROXY=1` (to bypass broken local proxy settings)

### bash
```bash
export OPENALEX_API_KEY='your_openalex_key'
export UNPAYWALL_EMAIL='you@example.com'
export PROVIDER_DISABLE_PROXY=1
python -m ara net-smoke --mode LIVE --output-dir outputs_e2e/net_smoke_live
cat > outputs_e2e/live_run_config.json << 'JSON'
{"topic":"industrial anomaly detection","constraints":{"compute":"low"}}
JSON
python -m ara run --mode LIVE --output-dir outputs_e2e/live_run --config outputs_e2e/live_run_config.json
python -m ara report outputs_e2e/live_run
```

### OpenAlex live notes

- Get your OpenAlex API key from `openalex.org/settings/api`.
- Set `OPENALEX_API_KEY` before any `LIVE` run.
- Default mode is `REPLAY` (offline replay fixtures, deterministic, CI-safe).
- Manual live smoke (not for CI):
  - `python -m ara live-smoke --mode LIVE --query "machine learning" --min-works 3 --output-dir outputs_e2e/openalex_live_smoke`
  - On success it prints top 3 `(id, title, year)` rows.

### PowerShell
```powershell
$env:OPENALEX_API_KEY='your_openalex_key'
$env:UNPAYWALL_EMAIL='you@example.com'
$env:PROVIDER_DISABLE_PROXY='1'
python -m ara net-smoke --mode LIVE --output-dir outputs_e2e/net_smoke_live
@"
{"topic":"industrial anomaly detection","constraints":{"compute":"low"}}
"@ | Set-Content -Encoding utf8 outputs_e2e/live_run_config.json
python -m ara run --mode LIVE --output-dir outputs_e2e/live_run --config outputs_e2e/live_run_config.json
python -m ara report outputs_e2e/live_run
```

## Troubleshooting (LIVE 网络 & 代理)

- Connection refused / proxy errors:
  - Set `PROVIDER_DISABLE_PROXY=1`
  - Check `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`
- `401`:
  - OpenAlex key missing/invalid (`OPENALEX_API_KEY`)
- `422`:
  - Unpaywall email missing/invalid (`UNPAYWALL_EMAIL`)
- `429`:
  - You hit rate limits; use retry/backoff and lower request rate

## Acceptance commands

### REPLAY (必跑)

### bash
```bash
python -m compileall -q .
python test_pipeline.py
python -m ara live-smoke --mode REPLAY --min-works 10 --output-dir outputs_test/live_smoke_replay
python -m ara unpaywall-smoke --mode REPLAY --doi 10.1038/s41586-020-2649-2 --output-dir outputs_test/unpaywall_smoke_replay
```

### PowerShell
```powershell
python -m compileall -q .
python test_pipeline.py
python -m ara live-smoke --mode REPLAY --min-works 10 --output-dir outputs_test/live_smoke_replay
python -m ara unpaywall-smoke --mode REPLAY --doi 10.1038/s41586-020-2649-2 --output-dir outputs_test/unpaywall_smoke_replay
```

### LIVE (可选)

### bash
```bash
PROVIDER_DISABLE_PROXY=1 PROVIDER_MODE=LIVE python -m ara net-smoke --mode LIVE --output-dir outputs_e2e/net_smoke_live
PROVIDER_DISABLE_PROXY=1 PROVIDER_MODE=LIVE python -m ara run --output-dir outputs_e2e/live_run --config outputs_e2e/live_run_config.json
```

### PowerShell
```powershell
$env:PROVIDER_DISABLE_PROXY='1'; $env:PROVIDER_MODE='LIVE'; python -m ara net-smoke --mode LIVE --output-dir outputs_e2e/net_smoke_live
$env:PROVIDER_DISABLE_PROXY='1'; $env:PROVIDER_MODE='LIVE'; python -m ara run --output-dir outputs_e2e/live_run --config outputs_e2e/live_run_config.json
```

## PowerShell parameter pitfall (`--config` first)

For complex input, prefer `--config` with a temp file. Avoid passing complex JSON directly to `--initial-state-json` in PowerShell because quoting/escaping is error-prone.

```powershell
@"
{"topic":"industrial anomaly detection","constraints":{"compute":"low"}}
"@ | Set-Content -Encoding utf8 outputs_test/run_config.json
python -m ara run --output-dir outputs_test/run_replay --config outputs_test/run_config.json
```

## Minimum support matrix

| Item | Supported |
| :--- | :--- |
| OS | Windows 10/11 (PowerShell), Linux (bash) |
| Python | >=3.10 |
| Network in REPLAY | Not required |
| Network in LIVE | Required (outbound internet) |
| Optional env vars | `PROVIDER_DISABLE_PROXY`, `PROVIDER_MODE`, `OPENALEX_API_KEY`, `UNPAYWALL_EMAIL` |

## Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PROVIDER_MODE` | `REPLAY` | Provider mode: `REPLAY` or `LIVE`. |
| `PROVIDER_DISABLE_PROXY` | unset | If `1`, bypasses proxy env vars for provider requests. |
| `OPENALEX_API_KEY` | unset | Required for OpenAlex in `LIVE` mode. |
| `OPENALEX_API_KEY_FILE` | `data/secrets/openalex_api_key.txt` | Optional local key file path. |
| `OPENALEX_MAILTO` | unset | Optional OpenAlex mailto parameter. |
| `UNPAYWALL_EMAIL` | unset | Required for Unpaywall in `LIVE` mode. |
| `OUTPUT_DIR` | `outputs` | Where artifacts and logs are written. |
| `FAIL_FAST_TOOL` | `0` | If `1`, raises exception on tool failure. |
| `FAIL_FAST_API` | `0` | If `1`, raises exception on API failure. |

## Outputs

Artifacts are generated in `OUTPUT_DIR`:
- `state.json`
- `paper.md`
- `logs/pipeline.log`
- `logs/events.jsonl`

## Release notes

### v0.1.0

- Default provider mode is `REPLAY`; CI runs in `REPLAY` for deterministic checks.
- Provider support: OpenAlex / Crossref / Unpaywall.
- Added `net-smoke` and proxy diagnostics workflow for `LIVE` checks.
- Artifacts support redaction and `sha256` consistency checks.
- Known limitations:
  - `LIVE` runs depend on network stability, credentials, and provider quotas/rate limits.
  - In PowerShell, prefer `--config` over complex inline `--initial-state-json`.
