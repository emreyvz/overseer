"""Pure plate-string normalization and comparison. No model, no I/O."""
from __future__ import annotations

# Optional confusable folding for OCR noise. Applied only when fold_confusable=True,
# because it is lossy (it would merge genuinely different plates in strict mode).
_CONFUSABLE: dict[str, str] = {
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "Z": "2", "B": "8", "S": "5", "G": "6",
}


def normalize_plate(raw: str, fold_confusable: bool = False) -> str:
    """Uppercase, drop everything non-alphanumeric, optionally fold OCR-confusable
    glyphs to a canonical form. Deterministic."""
    s = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    if fold_confusable:
        s = "".join(_CONFUSABLE.get(ch, ch) for ch in s)
    return s


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def plate_similarity(a: str, b: str, fold_confusable: bool = False) -> float:
    """Normalized edit similarity in [0,1]. Two empty strings are identical (1.0);
    one empty is 0.0."""
    a = normalize_plate(a, fold_confusable)
    b = normalize_plate(b, fold_confusable)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    dist = _levenshtein(a, b)
    return 1.0 - dist / max(len(a), len(b))


def plates_match(a: str, b: str, threshold: float = 0.85,
                 fold_confusable: bool = False) -> bool:
    return plate_similarity(a, b, fold_confusable) >= threshold
