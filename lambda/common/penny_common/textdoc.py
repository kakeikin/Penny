"""Shape of text/{docId}.json — written by ParseLambda and the backfill script, read by IndexLambda."""
import re
from datetime import date, datetime, timedelta, timezone

_ISO_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
_UUID_PREFIX = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-', re.I)


def doc_id_for(file_hash: str, session_id=None) -> str:
    """Owner documents use the file hash; demo-session documents are namespaced so identical
    files uploaded by a visitor never overwrite the owner's text doc or vectors."""
    return f'demo-{session_id}-{file_hash}' if session_id else file_hash


def text_doc_key(doc_id: str) -> str:
    return f'text/{doc_id}.json'


def display_name(file_key: str) -> str:
    """uploads/[demo-sid/]<uuid4>-<filename> -> <filename>; names without a UUID prefix pass through."""
    name = file_key.rsplit('/', 1)[-1]
    return _UUID_PREFIX.sub('', name, count=1) or name


def _real_day(value):
    if not isinstance(value, str) or not _ISO_DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)              # rejects impossible days such as 2026-02-30
    except ValueError:
        return None


def statement_period(dates, uploaded_at=None) -> 'dict | None':
    """{'start', 'end'} spanning the document's entry dates, or None if there are none.

    IndexLambda falls back to the period end for a chunk's yearMonth when the text has no
    NN/NN transaction dates (e.g. a receipt printed "Feb 11, 2026"), and uses it as the reference
    year for yearless NN/NN dates. So impossible days and days after the upload (+1 day, for time
    zones) are ignored: one mistyped future date must not move a whole statement into next year.
    """
    days = sorted(filter(None, map(_real_day, dates)))
    upload = _upload_day(uploaded_at)
    if upload:
        days = [d for d in days if d <= upload + timedelta(days=1)]
    return {'start': days[0].isoformat(), 'end': days[-1].isoformat()} if days else None


def _upload_day(uploaded_at):
    try:
        return datetime.fromisoformat(str(uploaded_at).replace('Z', '+00:00')).astimezone(timezone.utc).date()
    except ValueError:
        return None


def build_text_doc(doc_id: str, file_key: str, doc_type: str, pages: list, entries: list,
                   session_id, uploaded_at: str, statement_period=None) -> dict:
    """Build the text/{docId}.json payload.

    pages:   [{'page': int (1-based), 'text': str, 'extractor': 'pypdf' | 'claude'}]
    entries: [{'entryId': str, 'page': int, 'evidenceText': str (masked)}]
    session_id: demo session id, or None for the owner.
    """
    return {
        'docId':           doc_id,
        'fileKey':         file_key,
        'fileName':        display_name(file_key),
        'docType':         doc_type,
        'uploadedAt':      uploaded_at,
        'statementPeriod': statement_period,
        'sessionId':       session_id,
        'pages':           pages,
        'entries':         entries,
    }
