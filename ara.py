import argparse
import hashlib
import getpass
import json
import os
import secrets
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib import request, error

from sr_pipeline.providers import OpenAlexProvider, UnpaywallProvider


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


def _make_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def _resolve_run_output_dir(args: argparse.Namespace) -> tuple[Path, str | None]:
    if args.output_root:
        run_id = _make_run_id()
        run_dir = Path(args.output_root) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir, run_id
    return Path(args.output_dir), None


def cmd_run(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    run_dir, generated_run_id = _resolve_run_output_dir(args)
    env["OUTPUT_DIR"] = str(run_dir)

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

    if generated_run_id:
        print(f"RUN_ID={generated_run_id}")
        print(f"RUN_DIR={run_dir}")
        return 0

    run_id = _resolve_run_id(run_dir)
    print(f"RUN_ID={run_id}")
    return 0


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_report_dir(path_arg: str) -> tuple[Path, str | None]:
    base = Path(path_arg)
    runs_dir = base / "runs"
    if runs_dir.exists() and runs_dir.is_dir():
        run_dirs = sorted([p for p in runs_dir.iterdir() if p.is_dir()], key=lambda p: p.name)
        if run_dirs:
            latest = run_dirs[-1]
            return latest, latest.name
    return base, None


def _scan_provider_artifacts(run_dir: Path) -> dict:
    providers_dir = run_dir / "providers"
    if not providers_dir.exists():
        return {
            "calls_total": 0,
            "success_count": 0,
            "fail_count": 0,
            "pdf_count": 0,
            "pdf_bytes": 0,
            "retries_total": 0,
            "stop_reason_top": "none",
        }

    artifacts = [p for p in providers_dir.rglob("*.json") if p.is_file()]
    total = len(artifacts)
    fail = 0
    pdf_count = 0
    pdf_bytes = 0
    retries_total = 0
    stop_reasons: Counter[str] = Counter()
    for artifact in artifacts:
        is_error = artifact.name.endswith("_error.json")
        if is_error:
            fail += 1

        if ("fetch_pdf" in artifact.name) and not is_error:
            pdf_count += 1

        payload = _load_json(artifact)
        reason = "none"
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict):
                value = meta.get("stop_reason")
                if isinstance(value, str) and value.strip():
                    reason = value.strip()
                attempts = meta.get("attempt_count")
                if isinstance(attempts, int) and attempts > 1:
                    retries_total += attempts - 1

                attempt_rows = meta.get("attempts")
                if isinstance(attempt_rows, list) and attempts is None:
                    retries_total += max(len(attempt_rows) - 1, 0)

                bytes_candidates = [
                    meta.get("bytes_downloaded"),
                    meta.get("bytes"),
                ]
                response = payload.get("response")
                if isinstance(response, dict):
                    bytes_candidates.extend([response.get("size"), response.get("bytes")])
                for bv in bytes_candidates:
                    if isinstance(bv, int) and bv > 0:
                        pdf_bytes += bv
                        break
        stop_reasons[reason] += 1

    success = total - fail
    top = stop_reasons.most_common(5)
    top_text = ",".join([f"{k}:{v}" for k, v in top]) if top else "none"
    return {
        "calls_total": total,
        "success_count": success,
        "fail_count": fail,
        "pdf_count": pdf_count,
        "pdf_bytes": pdf_bytes,
        "retries_total": retries_total,
        "stop_reason_top": top_text,
    }


def cmd_report(args: argparse.Namespace) -> int:
    out_dir, run_id = _resolve_report_dir(args.output_dir)
    if run_id:
        print(f"run_dir={out_dir}")
        print(f"run_id={run_id}")

    state = _load_json(out_dir / "state.json")
    critic = _load_json(out_dir / "critic_report.json")
    evidence = _load_json(out_dir / "evidence_table.json")
    manifest = _load_json(out_dir / "paper_manifest.json")

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

    provider = _scan_provider_artifacts(out_dir)
    print(f"provider_artifacts_total={provider['calls_total']}")
    print(f"provider_success={provider['success_count']}")
    print(f"provider_fail={provider['fail_count']}")
    print(f"provider_stop_reason_top={provider['stop_reason_top']}")
    print(f"budget_calls_total={provider['calls_total']}")
    print(f"budget_pdf_count={provider['pdf_count']}")
    print(f"budget_pdf_bytes={provider['pdf_bytes']}")
    print(f"budget_fail_count={provider['fail_count']}")
    print(f"budget_retries_total={provider['retries_total']}")
    budget_enforced = False
    budget_stop_reason = "none"
    if isinstance(manifest, dict):
        budgets = manifest.get("budgets")
        if isinstance(budgets, dict):
            budget_enforced = bool(budgets.get("budgets_enforced", False))
            budget_stop_reason = str(budgets.get("budget_stop_reason", "none") or "none")
    if not budget_enforced and isinstance(state, dict):
        budget_enforced = bool(state.get("budget_enforced", False))
        budget_stop_reason = str(state.get("budget_stop_reason", budget_stop_reason) or budget_stop_reason)
    print(f"budget_enforced={'true' if budget_enforced else 'false'}")
    print(f"budget_stop_reason={budget_stop_reason}")

    lit_stats = None
    cluster_stats = None
    if isinstance(manifest, dict):
        meta = manifest.get("meta")
        if isinstance(meta, dict):
            lit_stats = meta.get("literature_stats")
            cluster_stats = meta.get("clusters_summary")

    if lit_stats is None and isinstance(evidence, list) and evidence:
        first_row = evidence[0] if isinstance(evidence[0], dict) else {}
        lit_stats = first_row.get("literature_stats")
        cluster_stats = first_row.get("clusters_summary")

    if isinstance(lit_stats, dict):
        print(f"works_raw_count={lit_stats.get('works_raw_count', 'unknown')}")
        print(f"works_dedup_count={lit_stats.get('works_dedup_count', 'unknown')}")
        print(f"dedup_removed={lit_stats.get('dedup_removed', 'unknown')}")
    if isinstance(cluster_stats, dict):
        print(f"cluster_count={cluster_stats.get('cluster_count', 'unknown')}")
        print(f"largest_cluster_size={cluster_stats.get('largest_cluster_size', 'unknown')}")

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
    provider_mode = os.environ.get("PROVIDER_MODE", "REPLAY")
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
    unpaywall_email = os.environ.get("UNPAYWALL_EMAIL", "").strip()
    if unpaywall_email:
        print("UNPAYWALL_EMAIL=SET (env)")
    else:
        print("UNPAYWALL_EMAIL=UNSET")
    print("TIP: set PROVIDER_MODE=LIVE to enable real API calls")
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

def _extract_unpaywall_urls(payload: dict) -> list[str]:
    urls: list[str] = []
    if not isinstance(payload, dict):
        return urls
    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        for key in ["url_for_pdf", "url"]:
            value = best.get(key)
            if isinstance(value, str) and value.strip():
                urls.append(value.strip())
    locs = payload.get("oa_locations", [])
    if isinstance(locs, list):
        for loc in locs:
            if not isinstance(loc, dict):
                continue
            for key in ["url_for_pdf", "url"]:
                value = loc.get(key)
                if isinstance(value, str) and value.strip():
                    urls.append(value.strip())
    out: list[str] = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

def cmd_unpaywall_smoke(args: argparse.Namespace) -> int:
    mode = (args.mode or os.environ.get("PROVIDER_MODE", "REPLAY")).upper()
    os.environ["PROVIDER_MODE"] = mode
    os.environ["OUTPUT_DIR"] = args.output_dir
    os.environ.setdefault("PROVIDER_ARTIFACT_DEBUG", "1")
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if mode == "LIVE" and not os.environ.get("UNPAYWALL_EMAIL", "").strip():
        result = {
            "final_verdict": "SKIP",
            "status": "skipped",
            "stop_reason": "missing_unpaywall_email",
            "mode": mode,
            "doi": args.doi,
            "oa_urls": [],
        }
        print(f"UNPAYWALL_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 0

    try:
        provider = UnpaywallProvider()
        artifact = provider.fetch_metadata(args.doi)
        response = artifact.get("response", {}) if isinstance(artifact, dict) else {}
        oa_urls = _extract_unpaywall_urls(response if isinstance(response, dict) else {})
        verdict = "PASS" if oa_urls else "FAIL"
        result = {
            "final_verdict": verdict,
            "status": "ok" if verdict == "PASS" else "no_oa_url",
            "stop_reason": "none" if verdict == "PASS" else "no_oa_url",
            "mode": mode,
            "doi": args.doi,
            "oa_urls": oa_urls,
            "provider": artifact.get("provider"),
            "method": artifact.get("method"),
            "sha256": artifact.get("sha256"),
        }
        print(f"UNPAYWALL_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 0 if verdict == "PASS" else 1
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
            "doi": args.doi,
            "oa_urls": [],
        }
        print(f"UNPAYWALL_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 1

def cmd_net_smoke(args: argparse.Namespace) -> int:
    mode = (args.mode or os.environ.get("PROVIDER_MODE", "REPLAY")).upper()
    os.environ["PROVIDER_MODE"] = mode
    os.environ["OUTPUT_DIR"] = args.output_dir
    os.environ.setdefault("PROVIDER_ARTIFACT_DEBUG", "1")
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    result = {
        "mode": mode,
        "openalex": {"verdict": "SKIP", "status": "skipped", "stop_reason": "not_run", "sha256": None},
        "unpaywall": {"verdict": "SKIP", "status": "skipped", "stop_reason": "not_run", "sha256": None},
    }

    openalex_key_present = bool(OpenAlexProvider._read_secret_key())
    if mode != "LIVE" or openalex_key_present:
        try:
            oa = OpenAlexProvider()
            artifact = oa.fetch_metadata(args.query, per_page=10)
            works = artifact.get("response", {}).get("results", []) if isinstance(artifact, dict) else []
            ok = isinstance(works, list) and len(works) > 0
            result["openalex"] = {
                "verdict": "PASS" if ok else "FAIL",
                "status": "ok" if ok else "empty_results",
                "stop_reason": "none" if ok else "empty_results",
                "sha256": artifact.get("sha256") if isinstance(artifact, dict) else None,
            }
        except Exception as exc:
            status = "error"
            stop_reason = str(exc)
            if hasattr(exc, "meta") and isinstance(getattr(exc, "meta"), dict):
                meta = getattr(exc, "meta")
                status = str(meta.get("status", status))
                stop_reason = str(meta.get("stop_reason", stop_reason))
            result["openalex"] = {
                "verdict": "FAIL",
                "status": status,
                "stop_reason": stop_reason,
                "sha256": None,
            }
    else:
        result["openalex"] = {
            "verdict": "SKIP",
            "status": "skipped",
            "stop_reason": "missing_openalex_api_key",
            "sha256": None,
        }

    unpaywall_email_present = bool(os.environ.get("UNPAYWALL_EMAIL", "").strip())
    if mode != "LIVE" or unpaywall_email_present:
        try:
            up = UnpaywallProvider()
            artifact = up.fetch_metadata(args.doi)
            oa_urls = _extract_unpaywall_urls(artifact.get("response", {}) if isinstance(artifact, dict) else {})
            ok = len(oa_urls) > 0
            result["unpaywall"] = {
                "verdict": "PASS" if ok else "FAIL",
                "status": "ok" if ok else "no_oa_url",
                "stop_reason": "none" if ok else "no_oa_url",
                "sha256": artifact.get("sha256") if isinstance(artifact, dict) else None,
            }
        except Exception as exc:
            status = "error"
            stop_reason = str(exc)
            if hasattr(exc, "meta") and isinstance(getattr(exc, "meta"), dict):
                meta = getattr(exc, "meta")
                status = str(meta.get("status", status))
                stop_reason = str(meta.get("stop_reason", stop_reason))
            result["unpaywall"] = {
                "verdict": "FAIL",
                "status": status,
                "stop_reason": stop_reason,
                "sha256": None,
            }
    else:
        result["unpaywall"] = {
            "verdict": "SKIP",
            "status": "skipped",
            "stop_reason": "missing_unpaywall_email",
            "sha256": None,
        }

    print(f"NET_SMOKE_RESULT={json.dumps(result, ensure_ascii=False)}")
    has_fail = any(x.get("verdict") == "FAIL" for x in [result["openalex"], result["unpaywall"]])
    return 1 if has_fail else 0

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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_probable_run_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if (path / "state.json").exists():
        return True
    if path.parent.name == "runs":
        return True
    expected = [
        "paper.md",
        "paper_manifest.json",
        "evidence_table.json",
        "critic_report.json",
        "logs",
        "providers",
    ]
    return any((path / name).exists() for name in expected)


def cmd_bundle(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not _is_probable_run_dir(run_dir):
        print(
            f"error: --run-dir must point to a run directory (got: {run_dir})",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.out) if args.out else run_dir.parent / f"{run_dir.name}_bundle.zip"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    include_files: list[Path] = []
    fixed = ["paper.md", "paper_manifest.json", "evidence_table.json", "critic_report.json", "state.json"]
    for name in fixed:
        p = run_dir / name
        if p.exists() and p.is_file():
            include_files.append(p)
    for name in ["logs", "providers"]:
        base = run_dir / name
        if base.exists() and base.is_dir():
            include_files.extend([p for p in base.rglob("*") if p.is_file()])

    if not include_files:
        print(f"error: no bundleable artifacts found under run dir: {run_dir}", file=sys.stderr)
        return 2

    index_lines = []
    for p in sorted(include_files, key=lambda x: str(x.relative_to(run_dir))):
        rel = p.relative_to(run_dir).as_posix()
        index_lines.append(f"{_sha256_file(p)}  {rel}")

    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in include_files:
            zf.write(p, arcname=p.relative_to(run_dir).as_posix())
        zf.writestr("INDEX.txt", "\n".join(index_lines) + "\n")

    print(f"BUNDLE_OUT={out_path}")
    print(f"BUNDLE_INDEX_LINES={len(index_lines)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ara")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--output-dir", default="outputs")
    p_run.add_argument("--output-root")
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

    p_unpaywall_smoke = sub.add_parser("unpaywall-smoke")
    p_unpaywall_smoke.add_argument("--doi", required=True)
    p_unpaywall_smoke.add_argument("--mode", choices=["REPLAY", "LIVE"])
    p_unpaywall_smoke.add_argument("--output-dir", default="outputs_unpaywall_smoke")
    p_unpaywall_smoke.set_defaults(func=cmd_unpaywall_smoke)

    p_net_smoke = sub.add_parser("net-smoke")
    p_net_smoke.add_argument("--mode", choices=["REPLAY", "LIVE"])
    p_net_smoke.add_argument("--output-dir", default="outputs_net_smoke")
    p_net_smoke.add_argument("--query", default="industrial anomaly detection")
    p_net_smoke.add_argument("--doi", default="10.1038/s41586-020-2649-2")
    p_net_smoke.set_defaults(func=cmd_net_smoke)

    p_set_key = sub.add_parser("set-openalex-key")
    p_set_key.add_argument("--key-file", default="data/secrets/openalex_api_key.txt")
    p_set_key.add_argument("--key")
    p_set_key.set_defaults(func=cmd_set_openalex_key)

    p_bundle = sub.add_parser("bundle")
    p_bundle.add_argument("--run-dir", required=True)
    p_bundle.add_argument("--out")
    p_bundle.set_defaults(func=cmd_bundle)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
