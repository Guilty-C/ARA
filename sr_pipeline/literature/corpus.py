from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
import math
import re
from .ingest import ParsedPaper

@dataclass
class Chunk:
    chunk_id: str
    paper_id: str
    section: str
    span_start: int
    span_end: int
    text: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class SearchResult:
    paper_id: str
    title: str
    year: Optional[int]
    section: str
    span_start: int
    span_end: int
    snippet_text: str
    score: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class Corpus:
    def __init__(self):
        self.chunks: List[Chunk] = []
        self.papers: Dict[str, ParsedPaper] = {}
        # Simple TF-IDF
        self.doc_freqs: Dict[str, int] = {}
        self.num_docs = 0
        
    def add_paper(self, paper: ParsedPaper):
        self.papers[paper.paper_id] = paper
        
        # We chunk per section to keep context clean
        if not paper.sections:
            # Fallback
            self._chunk_text(paper.full_text, paper.paper_id, "Full Text", 0)
        else:
            for sec in paper.sections:
                sec_text = paper.full_text[sec.start_char:sec.end_char]
                self._chunk_text(sec_text, paper.paper_id, sec.name, sec.start_char)
                
    def _chunk_text(self, text: str, paper_id: str, section: str, offset: int):
        chunk_size = 1000
        overlap = 150
        
        n = len(text)
        if n == 0:
            return
            
        step = chunk_size - overlap
        if step <= 0: step = chunk_size # should not happen given defaults
        
        for i in range(0, n, step):
            end = min(i + chunk_size, n)
            chunk_text = text[i:end]
            span_start = offset + i
            span_end = offset + end
            
            chunk_id = f"{paper_id}_{span_start}"
            chunk = Chunk(chunk_id, paper_id, section, span_start, span_end, chunk_text)
            self.chunks.append(chunk)
            
            # Update index stats
            self.num_docs += 1
            words = set(self._tokenize(chunk_text))
            for w in words:
                self.doc_freqs[w] = self.doc_freqs.get(w, 0) + 1
                
    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'\w+', text.lower())
        
    def retrieve(self, query: str, k: int = 5) -> List[SearchResult]:
        q_words = self._tokenize(query)
        if not q_words:
            return []
            
        scores = []
        for chunk in self.chunks:
            score = 0
            c_words = self._tokenize(chunk.text)
            # TF-IDF scoring
            
            c_counts = {}
            for w in c_words:
                c_counts[w] = c_counts.get(w, 0) + 1
            
            for qw in q_words:
                if qw in c_counts:
                    tf = c_counts[qw]
                    df = self.doc_freqs.get(qw, 1) # avoid div by zero
                    idf = math.log(1 + self.num_docs / df)
                    score += tf * idf
            
            if score > 0:
                scores.append((score, chunk))
                
        scores.sort(key=lambda x: x[0], reverse=True)
        top_k = scores[:k]
        
        results = []
        for score, chunk in top_k:
            paper = self.papers[chunk.paper_id]
            results.append(SearchResult(
                paper_id=paper.paper_id,
                title=paper.title,
                year=paper.year,
                section=chunk.section,
                span_start=chunk.span_start,
                span_end=chunk.span_end,
                snippet_text=chunk.text,
                score=score
            ))
            
        return results
