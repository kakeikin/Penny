"""Per-page text extraction for PDFs, flagging pages that need transcription by Claude."""
import io
import re

from pypdf import PdfReader

# A page whose text layer has fewer non-whitespace characters than this is treated as
# scanned. Known blind spots: a scanned page with a real text header/footer, or a garbled
# text layer (fonts without a ToUnicode map), is classified as 'pypdf' and its evidence
# will then fail validation (safe, but evidence is lost).
SCANNED_MIN_CHARS = 20


class PdfTextError(ValueError):
    """The whole PDF could not be read (empty, malformed, or password-protected)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def extract_pdf_pages(data: bytes) -> list[dict]:
    """Return [{'page', 'text', 'extractor'}], one per page, 1-based.

    extractor 'pypdf' means `text` is an independent text layer; 'claude' means the page
    still needs transcription (text is '' until ParseLambda merges Claude's transcript).
    Raises PdfTextError when the document as a whole cannot be read.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(''):
            raise PdfTextError('encrypted')
        page_objs = list(reader.pages)
    except PdfTextError:
        raise
    except Exception as e:  # pypdf raises many types for malformed input
        raise PdfTextError('malformed') from e

    pages = []
    for i, page in enumerate(page_objs, start=1):
        try:
            text = page.extract_text() or ''
        except Exception as e:  # one bad page must not fail the document; Claude transcribes it
            print(f'pypdf failed on page {i}: {type(e).__name__}')
            text = ''
        if len(re.sub(r'\s', '', text)) < SCANNED_MIN_CHARS:
            pages.append({'page': i, 'text': '', 'extractor': 'claude'})
        else:
            pages.append({'page': i, 'text': text, 'extractor': 'pypdf'})
    return pages
