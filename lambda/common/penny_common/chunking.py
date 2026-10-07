"""Structure-aware chunking of extracted financial documents into vector records."""
import re
import textwrap
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from penny_common.masking import mask_identifiers

# Bump CHUNKER_VERSION whenever chunk boundaries, headers, or yearMonth rules change:
# keys are positional (docId#p{page}#c{n}), and re-indexing relies on the manifest diff.
CHUNKER_VERSION = '1'
MAX_CHARS = 2000            # ~500 tokens per chunk
OVERLAP_LINES = 2
MAX_SINGLE_CHUNK = 4 * MAX_CHARS   # receipts longer than this are chunked like statements

# Full ISO dates anywhere in the text.
_ISO_DATE = re.compile(r'\b(20\d{2})-(\d{2})-(\d{2})\b')
# Transaction dates: two-digit NN/NN (optionally /YY or /YYYY) at the start of a line.
# Requiring line start and two digits keeps footers like "Page 1/3" or "1/2 off" out.
_LINE_DATE = re.compile(r'^\s*(\d{2})/(\d{2})(?:/(\d{4}|\d{2}))?(?![\d/])', re.M)
# A full date may be at most this far from the reference date to count.
_PLAUSIBLE_PAST = timedelta(days=400)
_PLAUSIBLE_FUTURE = timedelta(days=31)


def vector_key(doc_id: str, page: int, n: int) -> str:
    return f'{doc_id}#p{page}#c{n}'


def _split_long_line(line: str, max_chars: int) -> list:
    if len(line) <= max_chars:
        return [line]
    return textwrap.wrap(line, width=max_chars, break_long_words=True, break_on_hyphens=False)


def chunk_page(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_LINES) -> list:
    """Split on line boundaries; adjacent chunks share up to `overlap` lines.

    A line is only split when it alone exceeds max_chars (e.g. pypdf returned a page
    with no newlines); such lines are wrapped at whitespace.
    """
    lines = [piece for line in (text or '').splitlines() if line.strip()
             for piece in _split_long_line(line, max_chars)]
    chunks, cur = [], []
    for line in lines:
        size = sum(len(l) + 1 for l in cur)
        if cur and size + len(line) + 1 > max_chars:
            chunks.append('\n'.join(cur))
            carry = cur[-overlap:] if overlap else []
            while carry and sum(len(l) + 1 for l in carry) + len(line) + 1 > max_chars:
                carry = carry[1:]
            cur = carry
        cur.append(line)
    if cur:
        chunks.append('\n'.join(cur))
    return chunks


def _safe_date(value):
    """Parse 'YYYY-MM-DD...' into a date; None if missing or malformed."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _reference_date(statement_period, uploaded_at) -> date:
    return (_safe_date((statement_period or {}).get('end'))
            or _safe_date(uploaded_at)
            or datetime.now(timezone.utc).date())


def detect_day_first(text: str) -> bool:
    """True if any line-leading NN/NN date only makes sense as DD/MM (first field > 12)."""
    return any(int(a) > 12 and 1 <= int(b) <= 12 for a, b, _y in _LINE_DATE.findall(text or ''))


def _valid(y: int, m: int, d: int):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def infer_year_month(text: str, statement_period, uploaded_at, day_first: bool = False) -> str:
    """Priority: majority transaction month in text > statement period end month > upload month."""
    ref = _reference_date(statement_period, uploaded_at)
    lo, hi = ref - _PLAUSIBLE_PAST, ref + _PLAUSIBLE_FUTURE
    months = []
    for y, m, d in _ISO_DATE.findall(text or ''):
        dt = _valid(int(y), int(m), int(d))
        if dt and lo <= dt <= hi:
            months.append(f'{dt.year}-{dt.month:02d}')
    for a, b, y in _LINE_DATE.findall(text or ''):
        month, day = (int(b), int(a)) if day_first else (int(a), int(b))
        if y:
            year = int(y) + 2000 if len(y) == 2 else int(y)
            dt = _valid(year, month, day)
            if not dt or not lo <= dt <= hi:
                continue
        else:
            year = ref.year if month <= ref.month else ref.year - 1
            dt = _valid(year, month, day)
            if not dt:
                continue
        months.append(f'{dt.year}-{dt.month:02d}')
    if months:
        counts = Counter(months)
        top = max(counts.values())
        return min(ym for ym, c in counts.items() if c == top)
    end = _safe_date((statement_period or {}).get('end'))
    if end:
        return f'{end.year}-{end.month:02d}'
    return f'{ref.year}-{ref.month:02d}'


def chunk_document(doc: dict) -> list:
    """Turn a text/{docId}.json document into masked chunk records with deterministic keys.

    Expects the shape built by penny_common.textdoc.build_text_doc. Receipts become one
    chunk per page unless longer than MAX_SINGLE_CHUNK; statements use chunk_page.
    """
    records = []
    for page in doc['pages']:
        page_no = page['page']
        page_text = page['text'] or ''
        if doc['docType'] == 'receipt' and len(page_text.strip()) <= MAX_SINGLE_CHUNK:
            bodies = [page_text.strip()] if page_text.strip() else []
        else:
            bodies = chunk_page(page_text)
        day_first = detect_day_first(page_text)
        for n, body in enumerate(bodies):
            ym = infer_year_month(body, doc.get('statementPeriod'), doc['uploadedAt'], day_first)
            text = mask_identifiers(f"[{doc['fileName']} | p{page_no} | {ym}]\n{body}")
            records.append({
                'key': vector_key(doc['docId'], page_no, n),
                'page': page_no,
                'yearMonth': ym,
                'text': text,
            })
    return records
