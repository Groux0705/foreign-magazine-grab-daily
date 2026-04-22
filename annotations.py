"""Helpers for vocabulary normalization."""
from __future__ import annotations

import re

_PUNCT_RE = re.compile(r"[^\w'\-]+", flags=re.UNICODE)
_EDGE_RE = re.compile(r"^[-']+|[-']+$")

# Very light English lemmatizer: handles common plural / past-tense / -ing
# Good enough for dedup; we don't pretend to be WordNet.
_IRREGULAR = {
    "are": "be", "is": "be", "was": "be", "were": "be", "been": "be", "being": "be",
    "has": "have", "had": "have", "having": "have",
    "does": "do", "did": "do", "done": "do", "doing": "do",
    "went": "go", "gone": "go", "going": "go",
    "children": "child", "men": "man", "women": "woman", "people": "person",
    "better": "good", "best": "good", "worse": "bad", "worst": "bad",
    "mice": "mouse", "feet": "foot", "teeth": "tooth",
}


def _strip_suffix(word: str) -> str:
    for suf in ("ingly", "edly", "ing", "ied", "ies", "ed", "es", "ly", "s"):
        if len(word) > len(suf) + 2 and word.endswith(suf):
            stem = word[: -len(suf)]
            if suf == "ies":
                return stem + "y"
            if suf == "ied":
                return stem + "y"
            if suf == "ing" and len(stem) >= 3 and stem[-1] == stem[-2]:
                # running -> run
                stem = stem[:-1]
            if suf == "ed" and len(stem) >= 3 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            return stem
    return word


def lemmatize(word: str) -> str:
    w = (word or "").strip().lower()
    w = _PUNCT_RE.sub("", w)
    w = _EDGE_RE.sub("", w)
    if not w:
        return ""
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    return _strip_suffix(w)


def normalize_phrase(phrase: str) -> str:
    """For multi-word selections: lemmatize each token then join with space."""
    tokens = [lemmatize(t) for t in re.split(r"\s+", phrase.strip())]
    return " ".join(t for t in tokens if t)
