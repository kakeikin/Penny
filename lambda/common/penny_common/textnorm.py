"""Unicode/whitespace/case normalization for comparing extracted document text."""
import re
import unicodedata

_WS = re.compile(r'\s+')
_INVISIBLE = dict.fromkeys(map(ord, '\u200b\u200c\u200d\xad\ufeff'))  # zero-width chars, soft hyphen, BOM


def normalize(text: str) -> str:
    """NFKC-normalize (folds ligatures like 'ﬁ'), drop invisible chars, collapse whitespace, casefold."""
    text = unicodedata.normalize('NFKC', text or '').translate(_INVISIBLE)
    return _WS.sub(' ', text).strip().casefold()


def contains_normalized(haystack: str, needle: str) -> bool:
    """True if the normalized needle occurs in the normalized haystack; an empty needle never matches."""
    n = normalize(needle)
    return bool(n) and n in normalize(haystack)