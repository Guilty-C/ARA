import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import request, error

from sr_pipeline.providers import OpenAlexProvider


def _resolve_run_id(output_dir: Path) -> str:
    state_path = output_dir / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            rid = state.get("run_id")
            if rid:
                return str(rid)
        except Exception:
            pass

    runs_dir = output_dir / "runs"
    if runs_dir.exists():
        run_dirs = sorted([p for p in runs_dir.iterdir() if p.is_dir()], key=lambda p: p.name)
        if run_dirs:
            return run_dirs[-1].name
    return "unknown"


def cmd_run(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    env["OUTPUT_DIR"] = args.output_dir

    if args.initial_state_json and args.config:
        print("error: use either --config or --initial-state-json", file=sys.stderr)
        return 2

    if args.initial_state_json:
        try:
            json.loads(args.initial_state_json)
        except json.JSONDecodeError:
            print("error: --initial-state-json must be valid JSON", file=sys.stderr)
            return 2
        env["INITIAL_STATE"] = args.initial_state_json
    elif args.config:
        cfg = Path(args.config)
        if not cfg.exists():
            print(f"error: config missing: {cfg}", file=sys.stderr)
            return 2
        try:
            cfg_text = cfg.read_text(encoding="utf-8")
            json.loads(cfg_text)
        except Exception:
            print("error: --config must contain valid JSON", file=sys.stderr)
            return 2
        env["INITIAL_STATE"] = cfg_text

    proc = subprocess.run(
        [sys.executable, "run_pipeline.py"],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        return proc.returncode

    run_id = _resolve_run_id(Path(args.output_dir))
    print(f"RUN_ID={run_id}")
    return 0


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cmd_report(args: argparse.Namespace) -> int:
    out_dir = Path(args.output_dir)
    state = _load_json(out_dir / "state.json")
    critic = _load_json(out_dir / "critic_report.json")
    evidence = _load_json(out_dir / "evidence_table.json")

    stages = []
    events_path = out_dir / "logs" / "events.jsonl"
    if events_path.exists():
        try:
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("kind") == "stage_end":
                    stage = ev.get("stage")
                    if stage and stage not in stages:
                        stages.append(stage)
        except Exception:
            pass

    stop_reason = state.get("stop_reason") if isinstance(state, dict) else None
    critic_pass = critic.get("critic_pass") if isinstance(critic, dict) else None
    issue_codes = []
    if isinstance(critic, dict):
        issue_codes = [i.get("code", "") for i in critic.get("issues", []) if isinstance(i, dict)]

    accuracy_mean = None
    if isinstance(state, dict):
        exp = state.get("experiment_results")
        if isinstance(exp, dict):
            agg = exp.get("aggregate")
            if isinstance(agg, dict):
                accuracy_mean = agg.get("accuracy_mean")

    print(f"stages_executed={','.join(stages) if stages else 'unknown'}")
    print(f"stop_reason={stop_reason if stop_reason else 'none'}")
    print(f"critic_pass={critic_pass if critic_pass is not None else 'unknown'}")
    print(f"critic_issue_codes={','.join(issue_codes) if issue_codes else 'none'}")
    if accuracy_mean is None:
        print("experiment_accuracy_mean=unknown")
    else:
        print(f"experiment_accuracy_mean={accuracy_mean}")

    for name in ["paper.md", "paper_manifest.json", "evidence_table.json", "critic_report.json"]:
        p = out_dir / name
        if p.exists():
            print(f"{name}={p}")
        else:
            print(f"missing: {p}")

    return 0


def _doctor_ping(base: str, timeout: float = 2.0) -> bool:
    url = base.rstrip("/") + "/api/ping"
    req = request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return False


def cmd_doctor(_: argparse.Namespace) -> int:
    tool_base = os.environ.get("TOOL_API_BASE")
    provider_mode = os.environ.get("PROVIDER_MODE", "LIVE")
    cache_mode = os.environ.get("TOOL_CACHE_MODE", "READWRITE")

    ok = True
    if tool_base:
        tool_ok = _doctor_ping(tool_base)
        ok = ok and tool_ok
        print(f"TOOL_API_BASE={tool_base} {'OK' if tool_ok else 'FAIL'}")
    else:
        print("TOOL_API_BASE=<unset> OK")

    print(f"PROVIDER_MODE={provider_mode}")
    print(f"CACHE_MODE={cache_mode}")
    env_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    key_file = OpenAlexProvider.get_key_file_path()
    file_key = ""
    if not env_key and key_file.exists():
        try:
            file_key = key_file.read_text(encoding="utf-8").strip()
        except Exception:
            file_key = ""
    if env_key:
        print("OPENALEX_API_KEY=SET (env)")
    elif file_key:
        print(f"OPENALEX_API_KEY=SET (file:{key_file})")
    else:
        print("OPENALEX_API_KEY=UNSET")
    return 0 if ok else 1

def cmd_live_smoke(args: argparse.Namespace) -> int:
    mode = (args.mode or os.environ.get("PROVIDER_MODE", "REPLAY")).upper()
    os.environ["PROVIDER_MODE"] = mode
    os.environ["OUTPUT_DIR"] = args.output_dir
    os.environ.setdefault("PROVIDER_ARTIFACT_DEBUG", "1")
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if mode == "LIVE" and not OpenAlexProvider._read_secret_key():
        result = {
            "final_verdict": "SKIP",
            "status": "skipped",
            "stop_reason": "missing_openalex_api_key",
            "mode": mode,
            "query": args.query,
            "n_works": 0,
            "min_works": args.min_works,
            "score": 10,
            "total": 10,
        }
        print(f"LIVE_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 0

    try:
        provider = OpenAlexProvider()
        per_page = max(args.min_works * 2, 1)
        artifact = provider.fetch_metadata(args.query, per_page=per_page)
        response = artifact.get("response", {}) if isinstance(artifact, dict) else {}
        works = response.get("results", []) if isinstance(response, dict) else []
        n_works = len(works) if isinstance(works, list) else 0

        if n_works >= args.min_works:
            result = {
                "final_verdict": "PASS",
                "status": "ok",
                "stop_reason": "none",
                "mode": mode,
                "query": args.query,
                "n_works": n_works,
                "min_works": args.min_works,
                "provider": artifact.get("provider"),
                "method": artifact.get("method"),
                "sha256": artifact.get("sha256"),
            }
            print(f"LIVE_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
            return 0

        result = {
            "final_verdict": "FAIL",
            "status": "insufficient_works",
            "stop_reason": "insufficient_works",
            "mode": mode,
            "query": args.query,
            "n_works": n_works,
            "min_works": args.min_works,
            "provider": artifact.get("provider"),
            "method": artifact.get("method"),
            "sha256": artifact.get("sha256"),
        }
        print(f"LIVE_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 1
    except Exception as exc:
        status = "error"
        stop_reason = str(exc)
        if hasattr(exc, "meta") and isinstance(getattr(exc, "meta"), dict):
            meta = getattr(exc, "meta")
            status = str(meta.get("status", status))
            stop_reason = str(meta.get("stop_reason", stop_reason))
        result = {
            "final_verdict": "FAIL",
            "status": status,
            "stop_reason": stop_reason,
            "mode": mode,
            "query": args.query,
            "n_works": 0,
            "min_works": args.min_works,
        }
        print(f"LIVE_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 1

def cmd_set_openalex_key(args: argparse.Namespace) -> int:
    key_file = Path(args.key_file)
    key_value = args.key
    if key_value is None:
        try:
            key_value = getpass.getpass("OpenAlex API key: ")
        except Exception as exc:
            print(f"error: failed to read key interactively: {exc}", file=sys.stderr)
            return 1
    key_value = (key_value or "").strip()
    if not key_value:
        print("error: openalex key must be non-empty", file=sys.stderr)
        return 1

    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key_value + "\n", encoding="utf-8")
        verify = key_file.read_text(encoding="utf-8").strip()
    except Exception as exc:
        print(f"error: failed to write key file: {exc}", file=sys.stderr)
        return 1

    if not verify:
        print("error: key file verification failed (empty content)", file=sys.stderr)
        return 1

    print(f"OPENALEX_KEY_FILE_WRITTEN={key_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ara")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--output-dir", required=True)
    p_run.add_argument("--config")
    p_run.add_argument("--initial-state-json")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report")
    p_report.add_argument("output_dir")
    p_report.set_defaults(func=cmd_report)

    p_doctor = sub.add_parser("doctor")
    p_doctor.set_defaults(func=cmd_doctor)

    p_live_smoke = sub.add_parser("live-smoke")
    p_live_smoke.add_argument("--query", default="industrial anomaly detection")
    p_live_smoke.add_argument("--min-works", type=int, default=10)
    p_live_smoke.add_argument("--mode", choices=["REPLAY", "LIVE"])
    p_live_smoke.add_argument("--output-dir", default="outputs_live_smoke")
    p_live_smoke.set_defaults(func=cmd_live_smoke)

    p_set_key = sub.add_parser("set-openalex-key")
    p_set_key.add_argument("--key-file", default="data/secrets/openalex_api_key.txt")
    p_set_key.add_argument("--key")
    p_set_key.set_defaults(func=cmd_set_openalex_key)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
