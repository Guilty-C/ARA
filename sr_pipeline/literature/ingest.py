import hashlib
import json
import shutil
import os
import atexit
from urllib.parse import urlparse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any

from sr_pipeline.providers import get_provider, PaperSource, ProviderCallError

# Milestone 1: Untrusted content boundary
from sr_pipeline.tools import sanitize_untrusted_text

try:
    import pypdf
except ImportError:
    pypdf = None

_LAST_BUDGET_USAGE: Dict[str, Any] = {
    "max_pdfs": 20,
    "max_pdf_bytes": 50_000_000,
    "pdf_count": 0,
    "pdf_bytes": 0,
    "budget_skip_count": 0,
    "budget_enforced": False,
    "budget_stop_reason": "none",
}
_PENDING_STOP_REASON: Optional[str] = None
_ATEXIT_REGISTERED = False


def _write_budget_error_artifact(reason: str, source: str) -> None:
    out_dir = Path(os.environ.get("OUTPUT_DIR", "outputs"))
    providers_dir = out_dir / "providers"
    providers_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": "literature_budget_guard",
        "method": "fetch_pdf",
        "payload": {"url": source},
        "response": {},
        "meta": {"status": "budget_error", "stop_reason": reason},
        "status": "error",
        "error": reason,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    payload["sha256"] = digest
    path = providers_dir / f"literature_budget_guard_fetch_pdf_{digest[:12]}_error.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _persist_state_stop_reason(reason: str) -> None:
    out_dir = Path(os.environ.get("OUTPUT_DIR", "outputs"))
    state_path = out_dir / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    state["stop_reason"] = reason
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _flush_pending_stop_reason() -> None:
    global _PENDING_STOP_REASON
    if _PENDING_STOP_REASON:
        _persist_state_stop_reason(_PENDING_STOP_REASON)


def _register_pending_stop_reason(reason: str) -> None:
    global _PENDING_STOP_REASON, _ATEXIT_REGISTERED
    _PENDING_STOP_REASON = reason
    if not _ATEXIT_REGISTERED:
        atexit.register(_flush_pending_stop_reason)
        _ATEXIT_REGISTERED = True

@dataclass
class Section:
    name: str
    start_char: int
    end_char: int

@dataclass
class ParsedPaper:
    paper_id: str
    title: str
    year: Optional[int]
    full_text: str
    sections: List[Section]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def get_file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()

def _breach_pdf_budget(
    budget_usage: Dict[str, Any],
    reason: str,
    fail_fast: bool,
    source: str,
) -> None:
    budget_usage["budget_enforced"] = True
    budget_usage["budget_stop_reason"] = reason
    budget_usage["budget_skip_count"] = int(budget_usage.get("budget_skip_count", 0)) + 1
    _write_budget_error_artifact(reason, source)
    _persist_state_stop_reason(reason)
    _register_pending_stop_reason(reason)
    if fail_fast:
        raise ProviderCallError(reason, meta={"stop_reason": reason, "status": "budget_exceeded"})


def get_last_budget_usage() -> Dict[str, Any]:
    return dict(_LAST_BUDGET_USAGE)


def paper_fetch(
    sources: List[str],
    cache_dir: str = "data/papers_cache",
    budget_limits: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    
    manifest_path = cache_path / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except:
            pass
            
    results = []
    provider = get_provider()
    fail_fast = os.environ.get("FAIL_FAST", "0") == "1" or os.environ.get("FAIL_FAST_TOOL", "0") == "1"
    limits = budget_limits or {}
    max_pdfs = int(limits.get("max_pdfs", int(os.environ.get("LITERATURE_MAX_PDFS", "20"))))
    max_pdf_bytes = int(
        limits.get("max_pdf_bytes", int(os.environ.get("LITERATURE_MAX_PDF_BYTES", "50000000")))
    )
    budget_usage = {
        "max_pdfs": max_pdfs,
        "max_pdf_bytes": max_pdf_bytes,
        "pdf_count": 0,
        "pdf_bytes": 0,
        "budget_skip_count": 0,
        "budget_enforced": False,
        "budget_stop_reason": "none",
    }
    if os.environ.get("PAPER_FETCH_DEBUG", "0") == "1":
        print(f"DEBUG: paper_fetch called with {len(sources)} sources")
    
    for src in sources:
        # Handle "URL" placeholder if it's not a local file
        if src.startswith("http"):
            try:
                if budget_usage["pdf_count"] >= max_pdfs:
                    _breach_pdf_budget(budget_usage, "budget_pdf_limit_reached", fail_fast, src)
                    continue
                if budget_usage["pdf_bytes"] >= max_pdf_bytes:
                    _breach_pdf_budget(budget_usage, "budget_pdf_bytes_limit_reached", fail_fast, src)
                    continue

                # Fetch via provider (cached, hashed, reliable)
                res = provider.fetch_pdf(PaperSource(source_type="url", path_or_url=src))
                res_size = int(res.get("size", 0)) if isinstance(res.get("size", 0), int) else 0
                if budget_usage["pdf_bytes"] + res_size > max_pdf_bytes:
                    _breach_pdf_budget(budget_usage, "budget_pdf_bytes_limit_reached", fail_fast, src)
                    continue
                
                # Copy to local paper cache for consistent pipeline access
                paper_id = res["sha256"]
                cached_file = cache_path / f"{paper_id}.pdf"
                
                if not cached_file.exists():
                    shutil.copy2(res["local_path"], cached_file)
                    
                entry = {
                    "paper_id": paper_id,
                    "title": Path(urlparse(src).path).name or "web_resource",
                    "year": None, 
                    "source_path": src,
                    "cached_path": str(cached_file)
                }
                manifest[paper_id] = entry
                results.append(entry)
                budget_usage["pdf_count"] += 1
                budget_usage["pdf_bytes"] += res_size
            except ProviderCallError as e:
                stop_reason = str(e.meta.get("stop_reason")) if isinstance(e.meta, dict) else str(e)
                print(f"Failed to fetch URL {src}: {stop_reason}")
                if fail_fast:
                    raise
            except Exception as e:
                print(f"Failed to fetch URL {src}: {e}")
                if fail_fast:
                    raise
            continue
            
        src_path = Path(src)
        if not src_path.exists():
            continue
            
        # Deterministic ID based on content
        paper_id = get_file_hash(src_path)
        
        # Check cache
        cached_file = cache_path / f"{paper_id}.pdf"
        
        if not cached_file.exists():
            shutil.copy2(src_path, cached_file)
            
        entry = {
            "paper_id": paper_id,
            "title": src_path.stem,
            "year": None, # Todo: extract from filename if possible
            "source_path": str(src_path),
            "cached_path": str(cached_file)
        }
        
        manifest[paper_id] = entry
        results.append(entry)
        
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    global _LAST_BUDGET_USAGE
    _LAST_BUDGET_USAGE = dict(budget_usage)
    return results

def pdf_parse(cached_path: str, paper_id: str, title: str, year: Optional[int] = None) -> ParsedPaper:
    if not pypdf:
        raise ImportError("pypdf is required for pdf_parse")
        
    path = Path(cached_path)
    text_parts = []
    
    try:
        reader = pypdf.PdfReader(path)
        for page in reader.pages:
            text = page.extract_text()
            if text:
                # Sanitize untrusted text (Milestone 1)
                clean_text, _ = sanitize_untrusted_text(text)
                text_parts.append(clean_text)
                
        full_text = "\n\n---page---\n\n".join(text_parts)
        
        # Simple section heuristics
        sections = []
        lower_text = full_text.lower()
        keywords = ["abstract", "introduction", "related work", "method", "experiments", "conclusion"]
        
        # Find headers (naively)
        hits = []
        for kw in keywords:
            # Look for kw followed by newline or similar, to avoid matching in-sentence
            # This is very rough
            idx = lower_text.find(kw) 
            if idx != -1:
                # Basic check: is it near a newline?
                # For safety, let's just use it.
                hits.append((idx, kw.title()))
        
        hits.sort()
        
        # Deduplicate close hits or overlaps? No, just keep simple.
        
        if not hits:
             sections.append(Section(name="Full Text", start_char=0, end_char=len(full_text)))
        else:
            # Preamble
            if hits[0][0] > 0:
                sections.append(Section(name="Preamble", start_char=0, end_char=hits[0][0]))
            
            for i in range(len(hits)):
                start = hits[i][0]
                name = hits[i][1]
                end = hits[i+1][0] if i < len(hits) - 1 else len(full_text)
                sections.append(Section(name=name, start_char=start, end_char=end))

        return ParsedPaper(
            paper_id=paper_id,
            title=title,
            year=year,
            full_text=full_text,
            sections=sections
        )
    except Exception as e:
        # Return empty/error paper
        return ParsedPaper(
            paper_id=paper_id,
            title=title,
            year=year,
            full_text=f"Error parsing PDF: {e}",
            sections=[]
        )
