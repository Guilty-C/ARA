import hashlib
import json
import shutil
import os
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

def paper_fetch(sources: List[str], cache_dir: str = "data/papers_cache") -> List[Dict[str, Any]]:
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
    print(f"DEBUG: paper_fetch called with {len(sources)} sources")
    
    for src in sources:
        # Handle "URL" placeholder if it's not a local file
        if src.startswith("http"):
            try:
                # Fetch via provider (cached, hashed, reliable)
                res = provider.fetch_pdf(PaperSource(source_type="url", path_or_url=src))
                
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
