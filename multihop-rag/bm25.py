"""Keyword ranking (BM25) over indexed filing chunks.

Dense embeddings can miss a passage whose key terms a filing uses verbatim — "customer
concentration", "tax credits" — when the surrounding text pulls the vector elsewhere. BM25
scores each chunk by term overlap, weighting rare terms more, so it surfaces those passages.
It's written out here rather than pulled in as a package so the Docker image and Render
build don't change.
"""
import math
import re
from collections import Counter
from typing import List

# Words that appear in nearly every filing and carry no signal for matching passages.
_STOPWORDS = frozenset(
    "a an and are as at be been by can could do does for from had has have how i if in into "
    "is it its more most no not of on or our so such than that the their them then there these "
    "they this to up us was we were what when where which who why will with would your about "
    "over under also any each other some what s".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS]


class BM25Index:
    """Okapi BM25 over a fixed list of texts. Build once per query; the corpus is one company's chunks."""

    def __init__(self, texts: List[str], k1: float = 1.5, b: float = 0.75):
        self._term_counts = [Counter(tokenize(text)) for text in texts]
        self._lengths = [sum(counts.values()) for counts in self._term_counts]
        total = sum(self._lengths)
        self._avg_length = total / len(texts) if texts else 0.0
        doc_freq: Counter = Counter()
        for counts in self._term_counts:
            doc_freq.update(counts.keys())
        n_docs = len(texts)
        self._idf = {
            term: math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
            for term, df in doc_freq.items()
        }
        self._k1 = k1
        self._b = b

    def scores(self, query: str) -> List[float]:
        terms = set(tokenize(query))
        result = []
        for counts, length in zip(self._term_counts, self._lengths):
            score = 0.0
            for term in terms:
                freq = counts.get(term, 0)
                if not freq:
                    continue
                norm = (1 - self._b + self._b * length / self._avg_length) if self._avg_length else 1.0
                score += self._idf[term] * freq * (self._k1 + 1) / (freq + self._k1 * norm)
            result.append(score)
        return result

    def top_k(self, query: str, k: int) -> List[int]:
        """Indices of the k best-scoring texts, best first. Texts with no matching term are excluded."""
        scores = self.scores(query)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [i for i in order[:k] if scores[i] > 0]
