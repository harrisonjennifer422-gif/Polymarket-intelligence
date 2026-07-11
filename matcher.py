"""
Cross-platform market matching.

Polymarket and Kalshi have no shared ID scheme, so we match on question/title
similarity. This is a heuristic, not ground truth - false matches are the
main risk here (e.g. two different Fed-related markets that use similar
language but resolve on different criteria). We surface a similarity score
with every match so you can sanity-check before trusting a flagged
deviation.
"""

import re
from difflib import SequenceMatcher

from config import TITLE_MATCH_THRESHOLD

_STOPWORDS = {
    "the", "a", "an", "will", "be", "to", "in", "on", "of", "by",
    "for", "at", "is", "are", "this", "that", "2025", "2026",
}


def _normalize(text: str) -> set:
    if not text:
        return set()
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    words = [w for w in text.split() if w not in _STOPWORDS and len(w) > 1]
    return set(words)


def _similarity(a: str, b: str) -> float:
    """
    Blend of token-overlap (Jaccard) and sequence similarity.
    Token overlap catches reordered phrasing; sequence similarity
    catches near-identical strings. Neither alone is reliable.
    """
    tokens_a, tokens_b = _normalize(a), _normalize(b)
    if not tokens_a or not tokens_b:
        return 0.0

    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    seq = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return 0.6 * jaccard + 0.4 * seq


def find_matches(poly_markets: list, kalshi_markets: list, threshold: float = None):
    """
    For each Polymarket market, find the best-matching Kalshi market
    (if any) above the similarity threshold.

    Returns list of dicts: {poly_market, kalshi_market, similarity}
    """
    threshold = threshold if threshold is not None else TITLE_MATCH_THRESHOLD
    matches = []

    for pm in poly_markets:
        pm_title = pm.get("question") or ""
        best_match = None
        best_score = 0.0

        for km in kalshi_markets:
            km_title = km.get("title") or ""
            score = _similarity(pm_title, km_title)
            if score > best_score:
                best_score = score
                best_match = km

        if best_match and best_score >= threshold:
            matches.append({
                "poly_market": pm,
                "kalshi_market": best_match,
                "similarity": round(best_score, 3),
            })

    return matches
