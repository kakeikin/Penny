"""Unicode/whitespace/case normalization for comparing extracted document text."""
import re
import unicodedata

_WS = re.compile(r'\s+')
_NON_WORD = re.compile(r'[\W_]+')         # Unicode-aware: CJK and other letters are word characters
_INVISIBLE = dict.fromkeys(map(ord, '\u200b\u200c\u200d\xad\ufeff'))  # zero-width chars, soft hyphen, BOM


def normalize(text: str) -> str:
    """NFKC-normalize (folds ligatures like 'ﬁ'), drop invisible chars, collapse whitespace, casefold."""
    text = unicodedata.normalize('NFKC', text or '').translate(_INVISIBLE)
    return _WS.sub(' ', text).strip().casefold()


def fold(text: str) -> str:
    """Letters and digits only, accents removed, casefolded: "Trader Joes" == "TRADER JOE'S #552",
    "Crème" == "CREME", and "星巴克" stays "星巴克" (never folds to empty for non-Latin text)."""
    decomposed = unicodedata.normalize('NFKD', text or '')
    stripped = ''.join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_WORD.sub('', stripped.casefold())


def contains_normalized(haystack: str, needle: str) -> bool:
    """True if the normalized needle occurs in the normalized haystack; an empty needle never matches."""
    n = normalize(needle)
    return bool(n) and n in normalize(haystack)