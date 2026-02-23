from __future__ import annotations
import os
import json
import time
import hashlib
import logging
import re
import requests
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from sr_pipeline.tools import sanitize_untrusted_text
from sr_pipeline.reliability import ReliabilityLayer, ReliabilityConfig, CircuitBreakerError
from sr_pipeline.logging_utils import get_event_writer

@dataclass
class PaperSource:
    source_type: str  # "local" or "url"
    path_or_url: str
    paper_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class ProviderCallError(Exception):
    def __init__(self, message: str, meta: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.meta = meta or {}

class ProviderReliabilityLayer(ReliabilityLayer):
    """
    Enforces timeout, circuit breaker, and caching for provider calls.
    Inherits from shared ReliabilityLayer.
    """
    def __init__(self, provider_name: str, cache_dir: str = "data/provider_cache"):
        self.provider_name = provider_name
        self.mode = os.environ.get("PROVIDER_MODE", "REPLAY")
        self.fixtures_dir = Path("data/fixtures")
        
        super().__init__(
            name=provider_name,
            config=ReliabilityConfig(cache_dir=cache_dir, cache_mode="READWRITE"),
            event_writer=None # We will get writer on demand or pass it?
        )
        # Note: Base ReliabilityLayer uses passed-in event_writer. 
        # But here we want to look up the global one if not passed?
        # The base class `log_event` uses `self.event_writer`.
        # `get_event_writer` is available.
        # Let's override `log_event` to use `get_event_writer` if self.event_writer is None.
        
    def log_event(self, kind: str, payload: Dict[str, Any]):
        writer = get_event_writer()
        if writer:
            writer.append({
                "kind": kind,
                **payload
            })

    def _canonical_sha256(self, response: Dict[str, Any], meta: Dict[str, Any]) -> str:
        canonical = {"response": response, "meta": meta}
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_artifact(
        self,
        method_name: str,
        payload: Dict[str, Any],
        response: Dict[str, Any],
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        final_meta = dict(meta or {})
        ts = time.time()
        digest = self._canonical_sha256(response, final_meta)
        return {
            "provider": self.name,
            "method": method_name,
            "payload": payload,
            "response": response,
            "meta": final_meta,
            "timestamp": ts,
            "sha256": digest,
        }

    def _normalize_cached_artifact(self, method_name: str, payload: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        required = {"provider", "method", "payload", "response", "meta", "timestamp", "sha256"}
        if isinstance(data, dict) and required.issubset(set(data.keys())):
            return data
        if isinstance(data, dict) and "response" in data:
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            return self._build_artifact(method_name, payload, data.get("response", {}), meta)
        if isinstance(data, dict):
            return self._build_artifact(method_name, payload, data, {"normalized_from": "legacy_cache"})
        return self._build_artifact(method_name, payload, {"value": data}, {"normalized_from": "legacy_cache_non_dict"})

    def _providers_output_dir(self) -> Path:
        out_dir = Path(os.environ.get("OUTPUT_DIR", "outputs")) / "providers"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _safe_name(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")

    def _redact_secrets(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if str(k).lower() == "api_key":
                    out[k] = "<redacted>"
                else:
                    out[k] = self._redact_secrets(v)
            return out
        if isinstance(obj, list):
            return [self._redact_secrets(x) for x in obj]
        if isinstance(obj, str):
            return re.sub(r"([?&]api_key=)[^&]+", r"\1<redacted>", obj, flags=re.IGNORECASE)
        return obj

    def _write_provider_artifact(self, method_name: str, artifact: Dict[str, Any], is_error: bool = False):
        sanitized = self._redact_secrets(artifact)
        hash_input = dict(sanitized)
        hash_input.pop("sha256", None)
        canonical_json = json.dumps(hash_input, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        sanitized["sha256"] = digest
        base_name = f"{self._safe_name(self.name)}_{self._safe_name(method_name)}_{digest[:12]}"
        suffix = "_error.json" if is_error else ".json"
        path = self._providers_output_dir() / f"{base_name}{suffix}"
        path.write_text(json.dumps(sanitized, indent=2, ensure_ascii=False), encoding="utf-8")
        if os.environ.get("PROVIDER_ARTIFACT_DEBUG", "0") == "1":
            print(f"PROVIDER_ARTIFACT_WRITTEN={path}")

    def call(self, method_name: str, payload: Dict[str, Any], func, timeout_s: float = 20.0) -> Dict[str, Any]:
        """
        Generic wrapper for provider calls.
        """
        # 0. Check Replay Mode
        if self.mode == "REPLAY":
            artifact = self._handle_replay(method_name, payload)
            self._write_provider_artifact(method_name, artifact, is_error=False)
            return artifact

        # 1. Circuit Breaker
        self.check_circuit_breaker()

        # 2. Cache Check
        cache_key = self._get_cache_key(method_name, payload)
        cached_data = self.check_cache(cache_key)
        
        if cached_data:
            artifact = self._normalize_cached_artifact(method_name, payload, cached_data)
            self._log_event(method_name, payload, True, cache_hit=True)
            self._write_provider_artifact(method_name, artifact, is_error=False)
            return artifact

        # 3. Execute with Timeout
        start_ts = time.time()
        result = None
        error = None
        
        try:
            result = func()
            response_dict: Dict[str, Any]
            meta_dict: Dict[str, Any]
            if isinstance(result, tuple) and len(result) == 2:
                response_dict, meta_dict = result
            else:
                response_dict, meta_dict = result, {}
            if not isinstance(response_dict, dict):
                response_dict = {"value": response_dict}
            if not isinstance(meta_dict, dict):
                meta_dict = {"value": meta_dict}

            self.handle_success()

            artifact = self._build_artifact(method_name, payload, response_dict, meta_dict)
            self.write_cache(cache_key, artifact)
            self._write_provider_artifact(method_name, artifact, is_error=False)
            self._log_event(method_name, payload, True, latency_ms=(time.time()-start_ts)*1000)
            return artifact
            
        except Exception as e:
            self.handle_failure()
            error = str(e)
            error_meta = {}
            if hasattr(e, "meta") and isinstance(getattr(e, "meta"), dict):
                error_meta = getattr(e, "meta")
            error_artifact = {
                "provider": self.name,
                "method": method_name,
                "payload": payload,
                "response": {},
                "meta": error_meta,
                "status": "error",
                "error": error,
                "timestamp": time.time(),
            }
            error_artifact["sha256"] = hashlib.sha256(
                json.dumps(error_artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            self._write_provider_artifact(method_name, error_artifact, is_error=True)
            self._log_event(method_name, payload, False, error=error, latency_ms=(time.time()-start_ts)*1000)
            raise e

    def _log_event(self, method: str, payload: Any, success: bool, error: str = None, cache_hit: bool = False, latency_ms: float = 0.0):
        self.log_event("provider_call", {
            "provider": self.name,
            "method": method,
            "cache_hit": cache_hit,
            "ok": success,
            "error": error,
            "latency_ms": latency_ms
        })

    def _handle_replay(self, method_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        method_stub = method_name.split("_")[0] if "_" in method_name else method_name
        candidates = [
            self.fixtures_dir / f"provider_{self.provider_name}_{method_name}.json",
            self.fixtures_dir / f"provider_{self.provider_name}_{method_stub}.json",
        ]
        fixture_path: Optional[Path] = next((p for p in candidates if p.exists()), None)
        if fixture_path is None:
            fallback = sorted(self.fixtures_dir.glob(f"provider_{self.provider_name}_*.json"))
            if fallback:
                fixture_path = fallback[0]
        if fixture_path is None:
            raise FileNotFoundError(f"No fixtures found for {self.provider_name}:{method_name} in REPLAY mode")

        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        required = {"provider", "method", "payload", "response", "meta", "timestamp", "sha256"}
        if isinstance(fixture_data, dict) and required.issubset(set(fixture_data.keys())):
            artifact = dict(fixture_data)
            merged_meta = dict(artifact.get("meta", {}))
            merged_meta["replay_fixture"] = str(fixture_path)
            merged_meta["replay_mode"] = True
            artifact["meta"] = merged_meta
            artifact["provider"] = self.name
            artifact["method"] = method_name
            artifact["payload"] = payload
            artifact["sha256"] = self._canonical_sha256(artifact.get("response", {}), merged_meta)
            artifact["timestamp"] = time.time()
            return artifact

        replay_meta = {
            "replay_fixture": str(fixture_path),
            "replay_mode": True,
            "status": "replay",
        }
        return self._build_artifact(method_name, payload, fixture_data, replay_meta)


class PaperProvider:
    def resolve_paper_sources(self, config: Dict[str, Any]) -> List[PaperSource]:
        raise NotImplementedError
        
    def fetch_pdf(self, source: PaperSource) -> Dict[str, Any]:
        """
        Returns {local_path, sha256, meta}
        """
        raise NotImplementedError
        
    def fetch_metadata(self, paper_id_or_url: str) -> Dict[str, Any]:
        raise NotImplementedError

class CrossrefProvider(PaperProvider):
    def __init__(self):
        self.reliability = ProviderReliabilityLayer("crossref")
        self.base_url = "https://api.crossref.org/works"

    @staticmethod
    def _normalize_doi(doi: str) -> str:
        value = (doi or "").strip()
        value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
        return value

    def resolve_paper_sources(self, config: Dict[str, Any]) -> List[PaperSource]:
        return []

    def fetch_pdf(self, source: PaperSource) -> Dict[str, Any]:
        raise NotImplementedError("CrossrefProvider does not fetch PDFs")

    def fetch_metadata(self, paper_id_or_url: str) -> Dict[str, Any]:
        doi = self._normalize_doi(paper_id_or_url)
        payload = {"doi": doi}

        if self.reliability.mode == "REPLAY":
            h = hashlib.sha256(doi.encode("utf-8")).hexdigest()[:8]
            response = {
                "message": {
                    "DOI": doi,
                    "title": [f"Replay Crossref Title {h}"],
                    "issued": {"date-parts": [[2024]]},
                    "container-title": ["Replay Journal"],
                    "author": [{"given": "Replay", "family": "Author"}],
                }
            }
            meta = {
                "status": "replay",
                "status_code": 200,
                "final_url": f"{self.base_url}/{doi}",
                "attempts": [{"attempt": 1, "status_code": 200}],
            }
            artifact = self.reliability._build_artifact("lookup_work", payload, response, meta)
            self.reliability._write_provider_artifact("lookup_work", artifact, is_error=False)
            return artifact

        def _call():
            url = f"{self.base_url}/{requests.utils.quote(doi, safe='')}"
            attempts: List[Dict[str, Any]] = []
            resp = requests.get(url, timeout=10)
            attempts.append({"attempt": 1, "status_code": resp.status_code})
            if resp.status_code >= 400:
                raise ProviderCallError(
                    f"crossref_http_{resp.status_code}",
                    meta={
                        "status": "http_error",
                        "status_code": resp.status_code,
                        "final_url": resp.url,
                        "attempts": attempts,
                    },
                )
            return resp.json(), {
                "status": "ok",
                "status_code": resp.status_code,
                "final_url": resp.url,
                "attempts": attempts,
            }

        return self.reliability.call("lookup_work", payload, _call)

class OpenAlexProvider(PaperProvider):
    def __init__(self):
        self.reliability = ProviderReliabilityLayer("openalex")
        self.base_url = "https://api.openalex.org/works"
        self.crossref = CrossrefProvider()

    @staticmethod
    def _default_key_file() -> Path:
        return Path("data/secrets/openalex_api_key.txt")

    @classmethod
    def get_key_file_path(cls) -> Path:
        env_path = os.environ.get("OPENALEX_API_KEY_FILE", "").strip()
        return Path(env_path) if env_path else cls._default_key_file()

    @classmethod
    def _read_secret_key(cls) -> str:
        env_key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if env_key:
            return env_key
        key_file = cls.get_key_file_path()
        if key_file.exists():
            try:
                return key_file.read_text(encoding="utf-8").strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def _redact_url_api_key(url: str) -> str:
        return re.sub(r"([?&]api_key=)[^&]+", r"\1<redacted>", url, flags=re.IGNORECASE)
        
    def resolve_paper_sources(self, config: Dict[str, Any]) -> List[PaperSource]:
        # Config might contain "topics" or "urls"
        sources = []
        
        # 1. Explicit URLs
        if "urls" in config:
            for url in config["urls"]:
                sources.append(PaperSource(source_type="url", path_or_url=url))
                
        # 2. Topic Search (Metadata Provider)
        if "topics" in config:
            for topic in config["topics"]:
                # Fetch metadata first to get PDFs?
                # For M3, let's assume we search OpenAlex and get open access URLs
                meta = self.fetch_metadata(topic) # This searches by topic/keywords
                # Parse results
                # This is a simplification. Real OpenAlex search returns a list.
                results = meta.get("response", {}).get("results", [])
                for result in results:
                    oa_url = result.get("open_access", {}).get("oa_url")
                    if oa_url:
                        sources.append(PaperSource(
                            source_type="url", 
                            path_or_url=oa_url,
                            paper_id=result.get("id"),
                            metadata=result
                        ))
                        
        return sources

    def _extract_rate_limit_headers(self, headers: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in headers.items():
            k = str(key)
            if "rate" in k.lower() or "limit" in k.lower():
                out[k] = value
        return out

    @staticmethod
    def _extract_doi(result: Dict[str, Any]) -> str:
        doi = result.get("doi")
        if doi:
            return str(doi)
        ids = result.get("ids", {})
        if isinstance(ids, dict):
            doi = ids.get("doi")
            if doi:
                return str(doi)
        return ""

    @staticmethod
    def _extract_year_from_crossref(msg: Dict[str, Any]) -> Optional[int]:
        issued = msg.get("issued", {})
        if isinstance(issued, dict):
            parts = issued.get("date-parts", [])
            if parts and isinstance(parts[0], list) and parts[0]:
                try:
                    return int(parts[0][0])
                except Exception:
                    return None
        return None

    @staticmethod
    def _extract_authors_from_crossref(msg: Dict[str, Any]) -> List[str]:
        authors = []
        raw_authors = msg.get("author", [])
        if isinstance(raw_authors, list):
            for a in raw_authors:
                if not isinstance(a, dict):
                    continue
                given = str(a.get("given", "")).strip()
                family = str(a.get("family", "")).strip()
                name = " ".join([x for x in [given, family] if x]).strip()
                if name:
                    authors.append(name)
        return authors

    def _apply_crossref_enrichment(self, results: List[Dict[str, Any]], mode: str) -> None:
        if mode != "LIVE":
            return
        max_items = int(os.environ.get("OPENALEX_CROSSREF_MAX", "5"))
        enriched = 0
        for result in results:
            if enriched >= max_items:
                break
            doi = self._extract_doi(result)
            if not doi:
                continue
            enriched += 1
            provenance = result.get("provenance", {})
            if not isinstance(provenance, dict):
                provenance = {}
            provenance.setdefault("title", "openalex")
            provenance.setdefault("year", "openalex")
            provenance.setdefault("venue", "openalex")
            provenance.setdefault("authors", "openalex")
            provenance.setdefault("doi", "openalex")
            provenance["lookup_chain"] = "DOI->Crossref->OpenAlex"

            try:
                cross_artifact = self.crossref.fetch_metadata(doi)
                message = cross_artifact.get("response", {}).get("message", {})
                if not isinstance(message, dict):
                    message = {}

                cross_titles = message.get("title", [])
                cross_title = cross_titles[0] if isinstance(cross_titles, list) and cross_titles else None
                cross_year = self._extract_year_from_crossref(message)
                cross_venue_list = message.get("container-title", [])
                cross_venue = cross_venue_list[0] if isinstance(cross_venue_list, list) and cross_venue_list else None
                cross_authors = self._extract_authors_from_crossref(message)

                if cross_title and not result.get("display_name"):
                    result["display_name"] = cross_title
                    provenance["title"] = "crossref"
                if cross_year and not result.get("publication_year"):
                    result["publication_year"] = cross_year
                    provenance["year"] = "crossref"
                if cross_venue and not result.get("host_venue"):
                    result["host_venue"] = {"display_name": cross_venue}
                    provenance["venue"] = "crossref"
                if cross_authors and not result.get("authorships"):
                    result["authorships"] = [{"author": {"display_name": a}} for a in cross_authors]
                    provenance["authors"] = "crossref"
            except Exception:
                provenance.setdefault("crossref_error", "lookup_failed")

            result["provenance"] = provenance

    def fetch_metadata(self, query: str, per_page: Optional[int] = None) -> Dict[str, Any]:
        """
        Search OpenAlex for works matching the query.
        """
        env_per_page = int(os.environ.get("OPENALEX_PER_PAGE", "25"))
        effective_per_page = per_page if per_page is not None else env_per_page

        def _call():
            mode = self.reliability.mode
            api_key = self._read_secret_key()
            if mode == "LIVE" and not api_key:
                raise ProviderCallError(
                    "missing_openalex_api_key",
                    meta={
                        "status": "skipped",
                        "stop_reason": "missing_openalex_api_key",
                        "status_code": None,
                    },
                )

            mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
            params: Dict[str, Any] = {"search": query, "per_page": effective_per_page}
            if mode == "LIVE" and api_key:
                params["api_key"] = api_key
            if mailto:
                params["mailto"] = mailto
            params_redacted = dict(params)
            if "api_key" in params_redacted:
                params_redacted["api_key"] = "<redacted>"

            max_retries = 4
            attempt_count = 0
            attempts: List[Dict[str, Any]] = []
            last_status_code: Optional[int] = None
            last_url = self.base_url

            for retry_idx in range(max_retries + 1):
                attempt_count += 1
                resp = requests.get(self.base_url, params=params, timeout=10)
                last_status_code = resp.status_code
                last_url = self._redact_url_api_key(resp.url)
                rate_headers = self._extract_rate_limit_headers(resp.headers)
                attempts.append(
                    {
                        "attempt": attempt_count,
                        "status_code": resp.status_code,
                        "rate_limit_headers": rate_headers,
                    }
                )

                if resp.status_code == 429 and retry_idx < max_retries:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            wait_seconds = float(retry_after)
                        except ValueError:
                            wait_seconds = float(2 ** retry_idx)
                    else:
                        wait_seconds = float(2 ** retry_idx)
                    time.sleep(wait_seconds)
                    continue

                if resp.status_code >= 400:
                    meta = {
                        "status": "http_error",
                        "attempt_count": attempt_count,
                        "status_code": resp.status_code,
                        "last_status_code": last_status_code,
                        "final_url": last_url,
                        "rate_limit_headers": rate_headers,
                        "attempts": attempts,
                        "request": {"url": self.base_url, "params": params_redacted},
                    }
                    raise ProviderCallError(f"openalex_http_{resp.status_code}", meta=meta)

                response_json = resp.json()
                results = response_json.get("results", [])
                if isinstance(results, list):
                    self._apply_crossref_enrichment(results, mode)
                meta = {
                    "status": "ok",
                    "attempt_count": attempt_count,
                    "status_code": resp.status_code,
                    "last_status_code": last_status_code,
                    "final_url": last_url,
                    "rate_limit_headers": rate_headers,
                    "attempts": attempts,
                    "request": {"url": self.base_url, "params": params_redacted},
                }
                return response_json, meta

            raise ProviderCallError(
                "openalex_retry_exhausted",
                meta={
                    "status": "retry_exhausted",
                    "attempt_count": attempt_count,
                    "last_status_code": last_status_code,
                    "final_url": last_url,
                    "attempts": attempts,
                    "request": {"url": self.base_url, "params": params_redacted},
                },
            )

        call_payload = {"query": query, "per_page": effective_per_page}
        return self.reliability.call("search_works", call_payload, _call)

    def fetch_pdf(self, source: PaperSource) -> Dict[str, Any]:
        if source.source_type == "url":
            return self._fetch_url(source.path_or_url)
        else:
            # Local file
            path = Path(source.path_or_url)
            if not path.exists():
                raise FileNotFoundError(source.path_or_url)
            
            sha = self._compute_sha256(path)
            return {
                "local_path": str(path),
                "sha256": sha,
                "meta": {"source": "local", "size": path.stat().st_size}
            }

    def _fetch_url(self, url: str) -> Dict[str, Any]:
        # 0. Check Replay Mode
        if self.reliability.mode == "REPLAY":
            # Return a stub for replay
            # To ensure unique paper_ids for different URLs (for n_papers test),
            # we generate unique content.
            
            # Use provider cache dir for these replay artifacts
            cache_dir = Path("data/provider_cache")
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            unique_id = hashlib.md5(url.encode()).hexdigest()
            fixture_path = cache_dir / f"replay_{unique_id}.pdf"
            
            if not fixture_path.exists():
                # Load base fixture
                base_fixture = self.reliability.fixtures_dir / "tiny.pdf"
                if not base_fixture.exists():
                    raise FileNotFoundError("tiny.pdf fixture missing for replay")
                    
                content = base_fixture.read_bytes()
                # Append unique comment to change hash
                content += f"\n% Replay ID: {unique_id}".encode()
                fixture_path.write_bytes(content)
            
            sha = self._compute_sha256(fixture_path)
            
            # We also need to write the METADATA artifact expected by the test
            # The test checks for artifacts in data/provider_cache.
            # And expects "url", "sha256" etc.
            # My logic below normally does this.
            # In Replay mode, I should also write the artifact?
            # Yes, the test expects "provider artifacts are written".
            
            result = {
                "url": url,
                "local_path": str(fixture_path),
                "sha256": sha,
                "size": fixture_path.stat().st_size,
                "meta": {"content_type": "application/pdf", "replay": True}
            }
            
            # Write metadata artifact (simulating the cache write below)
            # The key logic below is:
            cache_key = hashlib.sha256(f"pdf_fetch:{url}".encode()).hexdigest()
            meta_path = cache_dir / f"pdf_fetch_{cache_key}.json"
            meta_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            
            # Log event
            self.reliability._log_event("fetch_pdf", {"url": url}, True, cache_hit=False)
            
            return result

        # Use reliability layer for caching, but custom logic for streaming download
        # We need to cache the FILE, not just the JSON response.
        # But the reliability layer is designed for JSON artifacts.
        # Let's adapt.
        
        # 1. Check if we have a metadata artifact for this URL (cache hit)
        cache_key = hashlib.sha256(f"pdf_fetch:{url}".encode()).hexdigest()
        cache_dir = Path("data/provider_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        meta_path = cache_dir / f"pdf_fetch_{cache_key}.json"
        
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                # Verify file exists
                if Path(meta["local_path"]).exists():
                    # Log hit
                    self.reliability._log_event("fetch_pdf", {"url": url}, True, cache_hit=True)
                    return meta
            except:
                pass
                
        # 2. Download
        try:
            start_ts = time.time()
            response = requests.get(url, stream=True, timeout=20)
            response.raise_for_status()
            
            # Stream to temp file and compute hash
            hasher = hashlib.sha256()
            temp_path = cache_dir / f"temp_{cache_key}.pdf"
            size = 0
            
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        hasher.update(chunk)
                        size += len(chunk)
                        
            file_hash = hasher.hexdigest()
            final_path = cache_dir / f"{file_hash}.pdf"
            
            if not final_path.exists():
                os.rename(temp_path, final_path)
            else:
                os.remove(temp_path)
                
            # Create artifact
            result = {
                "url": url,
                "local_path": str(final_path),
                "sha256": file_hash,
                "size": size,
                "meta": {"content_type": response.headers.get("Content-Type")}
            }
            
            # Write metadata artifact
            meta_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            
            self.reliability._log_event("fetch_pdf", {"url": url}, True, latency_ms=(time.time()-start_ts)*1000)
            
            return result
            
        except Exception as e:
            self.reliability._log_event("fetch_pdf", {"url": url}, False, error=str(e))
            raise e

    def _compute_sha256(self, path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

# Singleton or factory
def get_provider(name: str = "openalex") -> PaperProvider:
    if name == "openalex":
        return OpenAlexProvider()
    raise ValueError(f"Unknown provider: {name}")
