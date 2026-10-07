"""Demo-session id handling shared by the API Lambdas (one rule, so they can't drift)."""
import re

_SESSION_ID = re.compile(r'[A-Za-z0-9-]{1,64}')
OWNER = 'owner'   # vector metadata tag for the owner's documents


def valid_session_id(value) -> bool:
    """Demo session ids end up in docIds and vector keys; 'owner' is the owner's vector tag."""
    return isinstance(value, str) and bool(_SESSION_ID.fullmatch(value)) and value.lower() != OWNER


def session_from_headers(headers) -> 'str | None':
    """X-Session-Id (any casing) -> sid, or None for the owner. Raises ValueError if malformed."""
    headers = headers or {}
    sid = next((v for k, v in headers.items() if k.lower() == 'x-session-id'), None) or None
    if sid is None:
        return None
    if not valid_session_id(sid):
        raise ValueError('invalid session id')
    return sid
