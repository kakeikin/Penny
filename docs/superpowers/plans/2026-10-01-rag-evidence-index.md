# Penny RAG — Plan 1 of 3: Evidence Layer & Vector Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Git rule for this repo:** the user performs ALL git operations. Agents never run `git`. Each "Commit" step lists the commands for the user to run.

**Goal:** Every parsed journal entry links to the exact line in its source document, and every uploaded document is chunked, masked, embedded, and stored in S3 Vectors. Re-indexing is idempotent, and the work stays near $0.

**Architecture:** ParseLambda extracts per-page text with pypdf, asks Claude (in the existing single call) for per-entry evidence plus transcripts of pages with no text layer, validates evidence against the pypdf text, and writes `text/{docId}.json`. That S3 object triggers a new IndexLambda, which chunks, masks, embeds the text (Titan V2, 512 dimensions), writes the vectors to S3 Vectors under deterministic keys, records a manifest, and backfills `chunkKey` on entries. A new `GET /api/entries/{id}/evidence` returns the evidence with presigned file URLs. Shared logic lives in a `penny_common` package shipped in the Lambda layer.

**Tech Stack:** Python 3.12 Lambdas, boto3 1.43 (`s3vectors` client), pypdf 6.19, Amazon Titan Text Embeddings V2 via Bedrock, S3 Vectors, AWS CDK 2.248 (TypeScript), pytest + unittest.mock, jest.

**Spec:** `docs/superpowers/specs/2026-10-01-penny-rag-evidence-design.md`
**Later plans:** Plan 2 — Advisor agent (Converse tool use, `search_documents`, citation validator). Plan 3 — Frontend evidence UI, eval harness, README.

**Pre-verified:** All code in this plan was run in a scratch copy of the repo before writing. Expected results: 67 pytest tests passing (20 existing + 47 new), 14 jest tests passing, and the layer building in Docker with Linux `.so` files.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `requirements-dev.txt` | create | Local/CI test dependencies |
| `lambda/layer/requirements.txt` | create | Pinned runtime dependencies for the shared layer |
| `scripts/build-layer.sh` | rewrite | Build the layer in the AWS SAM Docker image and copy in `penny_common` |
| `lambda/common/penny_common/__init__.py` | create | Package marker |
| `lambda/common/penny_common/masking.py` | create | `mask_identifiers()`: keep the last 4 digits of long IDs |
| `lambda/common/penny_common/textnorm.py` | create | `normalize()`, `contains_normalized()` |
| `lambda/common/penny_common/pdftext.py` | create | `extract_pdf_pages()`: per-page pypdf text plus scanned-page detection |
| `lambda/common/penny_common/chunking.py` | create | Chunk pages, infer `yearMonth`, build deterministic keys |
| `lambda/common/penny_common/textdoc.py` | create | Shape of `text/{docId}.json` and display names |
| `lambda/common/penny_common/vectors.py` | create | Batched S3 Vectors put/delete, manifests, `delete_document_vectors()` |
| `lambda/parse/index.py` | modify | Decimal money, pypdf extraction, evidence, text doc write |
| `lambda/indexer/index.py` | create | IndexLambda: chunk → embed → put vectors → manifest → backfill chunkKey |
| `lambda/query/index.py` | modify | `GET /api/entries/{id}/evidence` |
| `lib/finance-stack.ts` | modify | Vector bucket/index, IndexLambda, DLQ, IAM, route; remove unused secret |
| `scripts/backfill_index.py` | create | Write `text/` docs for pre-existing uploads |
| `scripts/delete_document_vectors.py` | create | Cleanup CLI around `delete_document_vectors()` |
| `.github/workflows/ci.yml` | create | pytest + jest + cdk synth |
| `test/lambda/lambda_loader.py` | create | `load_lambda()`: order-independent import of `lambda/<name>/index.py` |
| `test/lambda/conftest.py` | create | `lambda_module` fixture; default AWS region; puts `penny_common` on the path |
| `test/lambda/pdf_fixtures.py` | create | Build tiny PDFs in memory |
| `test/lambda/test_query.py` | modify | Use the loader (fixes an existing order-dependence bug) |
| `test/lambda/test_penny_common.py` | create | masking / textnorm / pdftext / chunking / textdoc tests |
| `test/lambda/test_penny_vectors.py` | create | vectors helper tests |
| `test/lambda/test_parse_evidence.py` | create | ParseLambda Decimal + evidence tests |
| `test/lambda/test_indexer.py` | create | IndexLambda tests |
| `test/lambda/test_query_evidence.py` | create | Evidence API tests |
| `test/lambda/test_backfill.py` | create | Backfill script tests |
| `test/finance-stack.test.ts` | modify | Fix stale counts; add RAG resource tests |

---

### Task 0: Branch (user)

- [ ] **Step 1: User creates a feature branch**

```bash
git checkout -b feature/rag-evidence-index
```

---

### Task 1: Green, order-independent test baseline

Two existing problems must be fixed before adding anything:
- (a) Two jest tests assert stale counts (3 tables, 6 functions). The stack actually has 7 tables and 10 functions.
- (b) Every Lambda's entry file is `index.py`. `test_query.py` puts `lambda/query` on `sys.path` at import time, so its result depends on file order. Running `pytest test/lambda/test_query.py test/lambda/test_parse.py test/lambda/test_confirm.py test/lambda/test_manual_entry.py` fails 3 tests today.

**Files:**
- Create: `requirements-dev.txt`, `test/lambda/lambda_loader.py`, `test/lambda/conftest.py`
- Modify: `test/lambda/test_query.py:1-2, 5, 17, 37`, `test/finance-stack.test.ts:12-14, 54-56`

> **As implemented (post-review):** `load_lambda` lives in `test/lambda/lambda_loader.py` (not conftest, so nothing does `from conftest import`); conftest also does `os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')`; `requirements-dev.txt` additionally pins `pywebpush==2.3.0` and `requests==2.34.2` (imported by existing Lambdas/tests). `test_query.py` uses `from lambda_loader import load_lambda`.

- [ ] **Step 1: Reproduce the order bug**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_query.py test/lambda/test_parse.py test/lambda/test_confirm.py test/lambda/test_manual_entry.py -q -p no:cacheprovider`
Expected: `3 failed, 8 passed` (ImportError in test_query).

- [ ] **Step 2: Create `requirements-dev.txt`**

```text
boto3==1.43.106
pypdf==6.19.0
pytest==8.4.2
```

Run: `pip install -r requirements-dev.txt`

- [ ] **Step 3: Create `test/lambda/conftest.py`**

```python
import importlib.util
import os
import sys

import pytest

_LAMBDA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda'))

# Lambdas import penny_common from the layer at runtime; tests import it from source.
sys.path.insert(0, os.path.join(_LAMBDA_ROOT, 'common'))


def load_lambda(name: str):
    """Import lambda/<name>/index.py as its own module.

    Every Lambda's entry file is named index.py, so importing them via sys.path
    makes tests depend on execution order. Loading by file path avoids that.
    """
    path = os.path.join(_LAMBDA_ROOT, name, 'index.py')
    spec = importlib.util.spec_from_file_location(f"lambda_{name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lambda_module():
    return load_lambda
```

- [ ] **Step 4: Update `test/lambda/test_query.py` to use the loader**

Replace lines 1–2:
```python
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/query'))
```
with:
```python
from conftest import load_lambda

query = load_lambda('query')
```
Then replace the three in-function imports:
- `    from index import build_account_tree` → `    build_account_tree = query.build_account_tree`
- `    from index import compute_income_statement` → `    compute_income_statement = query.compute_income_statement`
- `    from index import compute_balance_sheet` → `    compute_balance_sheet = query.compute_balance_sheet`

- [ ] **Step 5: Fix the stale jest counts in `test/finance-stack.test.ts`**

Replace:
```ts
test('creates three DynamoDB tables', () => {
  template.resourceCountIs('AWS::DynamoDB::Table', 3);
});
```
with:
```ts
test('creates seven DynamoDB tables', () => {
  template.resourceCountIs('AWS::DynamoDB::Table', 7);
});
```
Replace:
```ts
test('creates five Lambda functions', () => {
  template.resourceCountIs('AWS::Lambda::Function', 6);
});
```
with:
```ts
test('creates nine app Lambda functions plus the S3 notifications handler', () => {
  template.resourceCountIs('AWS::Lambda::Function', 10);
});
```

- [ ] **Step 6: Verify the baseline is green in both orders**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda -q -p no:cacheprovider`
Expected: `20 passed`
Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_query.py test/lambda/test_parse.py test/lambda/test_confirm.py test/lambda/test_manual_entry.py -q -p no:cacheprovider`
Expected: `11 passed`
Run: `npx jest`
Expected: `Tests: 9 passed, 9 total`

- [ ] **Step 7: Commit (user)**

```bash
git add requirements-dev.txt test/lambda/conftest.py test/lambda/test_query.py test/finance-stack.test.ts
git commit -m "test: make Lambda tests order-independent and fix stale CDK counts"
```

---

### Task 2: Reproducible Linux layer build

The current `lambda/layer/python/` was built on macOS (`*-darwin.so` for cryptography/cffi). Those binaries cannot load on Lambda's Linux runtime, which likely breaks `pywebpush` in production. `scripts/build-layer.sh` is also stale: it deletes the directory and writes a placeholder. `http_ece` (a pywebpush dependency) ships only as an sdist, so `pip --platform` cross-installs fail. Building inside the AWS SAM image solves both problems.

**Files:**
- Create: `lambda/layer/requirements.txt`, `lambda/common/penny_common/__init__.py`
- Rewrite: `scripts/build-layer.sh`

- [ ] **Step 1: Create `lambda/layer/requirements.txt`**

```text
boto3==1.43.106
pypdf==6.19.0
pywebpush==2.3.0
requests==2.34.2
```

- [ ] **Step 2: Create an empty `lambda/common/penny_common/__init__.py`** (an empty file; later tasks add modules next to it)

- [ ] **Step 3: Rewrite `scripts/build-layer.sh`**

```bash
#!/bin/bash
# Build the shared Python layer for Lambda (Python 3.12, linux/x86_64).
# Runs pip inside the AWS SAM build image so compiled wheels (cryptography, cffi)
# match the Lambda runtime and sdist-only packages (http_ece) build on Linux.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf lambda/layer/python
mkdir -p lambda/layer/python

docker run --rm --platform linux/amd64 \
  -v "$PWD/lambda/layer":/layer \
  public.ecr.aws/sam/build-python3.12 \
  pip install --no-cache-dir -r /layer/requirements.txt -t /layer/python

cp -r lambda/common/penny_common lambda/layer/python/
echo "Layer built: $(du -sh lambda/layer/python | cut -f1)"
```

- [ ] **Step 4: Build and verify Linux binaries**

Run: `chmod +x scripts/build-layer.sh && ./scripts/build-layer.sh`
Expected: ends with `Layer built:  67M` (approximately).
Run: `find lambda/layer/python -name "*.so" | head -3`
Expected: filenames end in `-x86_64-linux-gnu.so`, and none contain `darwin`.
Run: `ls lambda/layer/python | grep -E "^(penny_common|pypdf|boto3|http_ece|pywebpush)$"`
Expected: all five listed.

- [ ] **Step 5: Commit (user)** (`lambda/layer/python/` is gitignored, so only the sources are committed)

```bash
git add lambda/layer/requirements.txt lambda/common/penny_common/__init__.py scripts/build-layer.sh
git commit -m "build: build Lambda layer in SAM Docker image with pinned deps"
```

---

### Task 3: `penny_common` masking and text normalization

**Files:**
- Create: `lambda/common/penny_common/masking.py`, `lambda/common/penny_common/textnorm.py`
- Test: `test/lambda/test_penny_common.py`

- [ ] **Step 1: Write failing tests** — create `test/lambda/test_penny_common.py`:

```python
from penny_common.masking import mask_identifiers
from penny_common.textnorm import normalize, contains_normalized


def test_mask_card_keeps_last_four():
    assert mask_identifiers('Card 1234567890123456 ok') == 'Card ****3456 ok'


def test_mask_eight_digits_before_sentence_period():
    assert mask_identifiers('Acct 12345678.') == 'Acct ****5678.'


def test_mask_leaves_short_numbers_dates_and_amounts():
    for s in ['1234567', '2026-03-14', '03/14/2026', '1234.56', '1,234.56', '-120.00', '12345678.90']:
        assert mask_identifiers(s) == s, s


def test_normalize_collapses_whitespace_and_case():
    assert normalize('  03/14  ABC\n Utilities ') == '03/14 abc utilities'


def test_contains_normalized():
    assert contains_normalized('x\n03/14   ABC UTILITIES  -120.00\ny', '03/14 abc utilities -120.00')
    assert not contains_normalized('03/14 ABC -120.00', '03/14 ABC -121.00')
    assert not contains_normalized('anything', '   ')
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/lambda/test_penny_common.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'penny_common.masking'`

- [ ] **Step 3: Create `lambda/common/penny_common/masking.py`**

```python
"""Mask long numeric identifiers (account / card numbers) before storage or embedding."""
import re

# 8+ consecutive digits that are not part of a date, amount, or longer number.
_LONG_ID = re.compile(r'(?<!\d)(?<!\d[/.,-])(\d{8,})(?!\d)(?![/.,-]\d)')


def mask_identifiers(text: str) -> str:
    """Preserve only the last 4 digits for long numeric identifiers."""
    return _LONG_ID.sub(lambda m: '****' + m.group(1)[-4:], text)
```

- [ ] **Step 4: Create `lambda/common/penny_common/textnorm.py`**

```python
"""Whitespace/case normalization for comparing extracted document text."""
import re

_WS = re.compile(r'\s+')


def normalize(text: str) -> str:
    return _WS.sub(' ', text or '').strip().lower()


def contains_normalized(haystack: str, needle: str) -> bool:
    n = normalize(needle)
    return bool(n) and n in normalize(haystack)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest test/lambda/test_penny_common.py -q`
Expected: `5 passed`

- [ ] **Step 6: Commit (user)**

```bash
git add lambda/common/penny_common/masking.py lambda/common/penny_common/textnorm.py test/lambda/test_penny_common.py
git commit -m "feat: add identifier masking and text normalization helpers"
```

---

### Task 4: `penny_common.pdftext` — per-page PDF text and scanned-page detection

**Files:**
- Create: `lambda/common/penny_common/pdftext.py`, `test/lambda/pdf_fixtures.py`
- Test: `test/lambda/test_penny_common.py` (append)

- [ ] **Step 1: Create the test fixture builder `test/lambda/pdf_fixtures.py`**

```python
"""Build tiny valid PDFs in memory for tests (no extra dependencies)."""


def make_pdf(pages: list) -> bytes:
    """pages: list of lists of text lines; an empty list makes a page with no text (like a scan)."""
    objects = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = ' '.join(f'{3 + 2 * i} 0 R' for i in range(n_pages))
    objects.append('<< /Type /Catalog /Pages 2 0 R >>')
    objects.append(f'<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>')
    for i, lines in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            f'/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>'
        )
        ops = ['BT', '/F1 10 Tf', '14 TL', '50 750 Td']
        for line in lines:
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            ops.append(f'({escaped}) Tj T*')
        ops.append('ET')
        stream = '\n'.join(ops)
        objects.append(f'<< /Length {len(stream)} >>\nstream\n{stream}\nendstream')
    objects.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')

    out = '%PDF-1.4\n'
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out.encode('latin-1')))
        out += f'{num} 0 obj\n{body}\nendobj\n'
    xref_at = len(out.encode('latin-1'))
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'
    out += ''.join(f'{o:010d} 00000 n \n' for o in offsets)
    out += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n'
    return out.encode('latin-1')
```

- [ ] **Step 2: Append a failing test to `test/lambda/test_penny_common.py`**

```python
from penny_common.pdftext import extract_pdf_pages
from pdf_fixtures import make_pdf


def test_extract_pdf_pages_text_and_scanned():
    data = make_pdf([['03/14 ABC UTILITIES -120.00', '03/15 COFFEE SHOP -4.50'], []])
    pages = extract_pdf_pages(data)
    assert pages[0]['extractor'] == 'pypdf'
    assert contains_normalized(pages[0]['text'], '03/14 ABC UTILITIES -120.00')
    assert pages[1] == {'page': 2, 'text': '', 'extractor': 'claude'}
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest test/lambda/test_penny_common.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'penny_common.pdftext'`

- [ ] **Step 4: Create `lambda/common/penny_common/pdftext.py`**

```python
"""Per-page text extraction for PDFs, flagging scanned pages that need transcription."""
import io
import re

from pypdf import PdfReader

SCANNED_MIN_CHARS = 20


def extract_pdf_pages(data: bytes) -> list:
    """Return [{'page', 'text', 'extractor'}]; scanned pages get extractor 'claude' and empty text."""
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ''
        if len(re.sub(r'\s', '', text)) < SCANNED_MIN_CHARS:
            pages.append({'page': i, 'text': '', 'extractor': 'claude'})
        else:
            pages.append({'page': i, 'text': text, 'extractor': 'pypdf'})
    return pages
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest test/lambda/test_penny_common.py -q`
Expected: `6 passed`

- [ ] **Step 6: Commit (user)**

```bash
git add lambda/common/penny_common/pdftext.py test/lambda/pdf_fixtures.py test/lambda/test_penny_common.py
git commit -m "feat: add per-page PDF text extraction with scanned-page detection"
```

---

### Task 5: `penny_common.chunking` — structure-aware chunks, yearMonth, keys

**Files:**
- Create: `lambda/common/penny_common/chunking.py`
- Test: `test/lambda/test_penny_common.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from penny_common.chunking import chunk_page, infer_year_month, chunk_document, vector_key


def test_chunk_page_short_drops_blank_lines():
    assert chunk_page('a\n\nb\n') == ['a\nb']


def test_chunk_page_overlap_and_line_integrity():
    lines = [f'{i:02d} ' + 'x' * 96 for i in range(30)]
    chunks = chunk_page('\n'.join(lines), max_chars=1000, overlap=2)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 1000
        for line in c.split('\n'):
            assert line in lines          # a transaction line is never split
    for a, b in zip(chunks, chunks[1:]):
        assert a.split('\n')[-2:] == b.split('\n')[:2]   # 2-line overlap
    assert set(l for c in chunks for l in c.split('\n')) == set(lines)


def test_chunk_page_huge_lines_do_not_duplicate_via_overlap():
    lines = ['a' * 600, 'b' * 600, 'c' * 600]
    assert chunk_page('\n'.join(lines), max_chars=1000, overlap=2) == lines


def test_chunk_page_deterministic():
    t = '\n'.join(f'line {i}' for i in range(500))
    assert chunk_page(t) == chunk_page(t)


PERIOD = {'start': '2026-03-15', 'end': '2026-04-14'}


def test_year_month_majority_of_transaction_dates():
    assert infer_year_month('03/16 A\n03/20 B\n04/01 C', PERIOD, '2026-04-20T00:00:00Z') == '2026-03'


def test_year_month_tie_picks_earliest():
    assert infer_year_month('03/30 A\n04/02 B', PERIOD, '2026-04-20T00:00:00Z') == '2026-03'


def test_year_month_infers_year_across_december():
    period = {'start': '2025-12-15', 'end': '2026-01-14'}
    assert infer_year_month('12/20 X\n12/22 Y', period, '2026-01-20T00:00:00Z') == '2025-12'


def test_year_month_iso_and_full_us_dates():
    assert infer_year_month('2026-05-01 coffee', None, '2026-06-01T00:00:00Z') == '2026-05'
    assert infer_year_month('07/04/2025 fireworks', None, '2026-06-01T00:00:00Z') == '2025-07'


def test_year_month_falls_back_to_statement_period_end():
    assert infer_year_month('Summary page, totals only', PERIOD, '2026-04-20T00:00:00Z') == '2026-04'


def test_year_month_falls_back_to_upload_month():
    assert infer_year_month('no dates', None, '2026-03-02T10:00:00Z') == '2026-03'


def test_year_month_ignores_amounts():
    assert infer_year_month('Total 1,234.56 balance 120.00', None, '2026-03-02T10:00:00Z') == '2026-03'


def test_chunk_document_header_mask_and_key():
    doc = {'docId': 'abc', 'docType': 'bank_statement', 'fileName': 'mar.pdf',
           'uploadedAt': '2026-04-20T00:00:00Z', 'statementPeriod': PERIOD,
           'pages': [{'page': 1, 'text': 'Acct 1234567890\n03/16 A -1.00', 'extractor': 'pypdf'},
                     {'page': 2, 'text': '', 'extractor': 'claude'}]}
    recs = chunk_document(doc)
    assert len(recs) == 1                       # empty page produces no chunk
    r = recs[0]
    assert r['key'] == vector_key('abc', 1, 0) == 'abc#p1#c0'
    assert r['yearMonth'] == '2026-03'
    assert r['text'].startswith('[mar.pdf | p1 | 2026-03]\n')
    assert '****7890' in r['text'] and '1234567890' not in r['text']


def test_chunk_document_receipt_is_one_chunk():
    doc = {'docId': 'r1', 'docType': 'receipt', 'fileName': 'r.jpg', 'uploadedAt': '2026-04-20T00:00:00Z',
           'statementPeriod': None, 'pages': [{'page': 1, 'text': '\n'.join(['item'] * 1000), 'extractor': 'claude'}]}
    assert len(chunk_document(doc)) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test/lambda/test_penny_common.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'penny_common.chunking'`

- [ ] **Step 3: Create `lambda/common/penny_common/chunking.py`**

```python
"""Structure-aware chunking of extracted financial documents into vector records."""
import re
from collections import Counter
from datetime import date

from penny_common.masking import mask_identifiers

CHUNKER_VERSION = '1'
MAX_CHARS = 2000      # ~500 tokens
OVERLAP_LINES = 2

_ISO_DATE = re.compile(r'\b(20\d{2})-(0[1-9]|1[0-2])-\d{2}\b')
_US_DATE = re.compile(r'(?<![\d/])(0?[1-9]|1[0-2])/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])')


def vector_key(doc_id: str, page: int, n: int) -> str:
    return f'{doc_id}#p{page}#c{n}'


def chunk_page(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_LINES) -> list:
    """Split on line boundaries; a line is never split. Adjacent chunks share up to `overlap` lines."""
    lines = [line for line in (text or '').splitlines() if line.strip()]
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


def _reference_date(statement_period, uploaded_at: str) -> date:
    end = (statement_period or {}).get('end')
    return date.fromisoformat(end) if end else date.fromisoformat(uploaded_at[:10])


def infer_year_month(text: str, statement_period, uploaded_at: str) -> str:
    """Priority: majority transaction month in text > statement period end month > upload month."""
    ref = _reference_date(statement_period, uploaded_at)
    months = [f'{y}-{m}' for y, m in _ISO_DATE.findall(text)]
    for m, _d, y in _US_DATE.findall(text):
        month = int(m)
        if y:
            year = int(y) + 2000 if len(y) == 2 else int(y)
        else:
            year = ref.year if month <= ref.month else ref.year - 1
        months.append(f'{year}-{month:02d}')
    if months:
        counts = Counter(months)
        top = max(counts.values())
        return min(ym for ym, c in counts.items() if c == top)
    end = (statement_period or {}).get('end')
    if end:
        return end[:7]
    return uploaded_at[:7]


def chunk_document(doc: dict) -> list:
    """Turn a text/{docId}.json document into masked chunk records with deterministic keys."""
    records = []
    for page in doc['pages']:
        page_no = page['page']
        if doc['docType'] == 'receipt':
            bodies = [page['text'].strip()] if page['text'].strip() else []
        else:
            bodies = chunk_page(page['text'])
        for n, body in enumerate(bodies):
            ym = infer_year_month(body, doc.get('statementPeriod'), doc['uploadedAt'])
            text = mask_identifiers(f"[{doc['fileName']} | p{page_no} | {ym}]\n{body}")
            records.append({
                'key': vector_key(doc['docId'], page_no, n),
                'page': page_no,
                'yearMonth': ym,
                'text': text,
            })
    return records
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest test/lambda/test_penny_common.py -q`
Expected: `19 passed`

- [ ] **Step 5: Commit (user)**

```bash
git add lambda/common/penny_common/chunking.py test/lambda/test_penny_common.py
git commit -m "feat: add structure-aware chunker with per-chunk yearMonth"
```

---

### Task 6: `penny_common.textdoc` and `penny_common.vectors`

**Files:**
- Create: `lambda/common/penny_common/textdoc.py`, `lambda/common/penny_common/vectors.py`
- Test: `test/lambda/test_penny_common.py` (append), `test/lambda/test_penny_vectors.py`

- [ ] **Step 1: Append a failing textdoc test to `test/lambda/test_penny_common.py`**

```python
from penny_common.textdoc import display_name, build_text_doc, text_doc_key


def test_display_name_strips_uuid_and_demo_prefix():
    assert display_name('uploads/123e4567-e89b-12d3-a456-426614174000-bank-mar.pdf') == 'bank-mar.pdf'
    assert display_name('uploads/demo-abc/123e4567-e89b-12d3-a456-426614174000-r.jpg') == 'r.jpg'
    assert display_name('uploads/plain.pdf') == 'plain.pdf'


def test_build_text_doc_shape():
    doc = build_text_doc('h1', 'uploads/123e4567-e89b-12d3-a456-426614174000-a.pdf', 'bank_statement',
                         [{'page': 1, 'text': 't', 'extractor': 'pypdf'}], [], None, '2026-03-01T00:00:00Z')
    assert text_doc_key('h1') == 'text/h1.json'
    assert doc == {'docId': 'h1', 'fileKey': 'uploads/123e4567-e89b-12d3-a456-426614174000-a.pdf',
                   'fileName': 'a.pdf', 'docType': 'bank_statement', 'uploadedAt': '2026-03-01T00:00:00Z',
                   'statementPeriod': None, 'sessionId': None,
                   'pages': [{'page': 1, 'text': 't', 'extractor': 'pypdf'}], 'entries': []}
```

- [ ] **Step 2: Create failing `test/lambda/test_penny_vectors.py`**

```python
import io
import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from penny_common import vectors


def _no_such_key():
    return ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')


def test_read_manifest_missing_returns_none():
    s3 = MagicMock()
    s3.get_object.side_effect = _no_such_key()
    assert vectors.read_manifest(s3, 'b', 'd') is None


def test_put_vectors_batches_of_500():
    sv = MagicMock()
    vectors.put_vectors(sv, 'vb', 'idx', [{'key': str(i)} for i in range(1201)])
    sizes = [len(c.kwargs['vectors']) for c in sv.put_vectors.call_args_list]
    assert sizes == [500, 500, 201]


def test_delete_document_vectors_uses_manifest_keys():
    s3 = MagicMock()
    s3.get_object.return_value = {'Body': io.BytesIO(json.dumps({'keys': ['d#p1#c0', 'd#p1#c1']}).encode())}
    sv = MagicMock()
    assert vectors.delete_document_vectors(s3, sv, 'b', 'vb', 'idx', 'd') == 2
    sv.delete_vectors.assert_called_once_with(vectorBucketName='vb', indexName='idx', keys=['d#p1#c0', 'd#p1#c1'])
    sv.list_vectors.assert_not_called()
    s3.delete_object.assert_called_once_with(Bucket='b', Key='manifests/d.json')


def test_delete_document_vectors_falls_back_to_listing():
    s3 = MagicMock()
    s3.get_object.side_effect = _no_such_key()
    sv = MagicMock()
    sv.list_vectors.side_effect = [
        {'vectors': [{'key': 'd#p1#c0', 'metadata': {'docId': 'd'}},
                     {'key': 'x#p1#c0', 'metadata': {'docId': 'x'}}], 'nextToken': 't'},
        {'vectors': [{'key': 'd#p2#c0', 'metadata': {'docId': 'd'}}]},
    ]
    assert vectors.delete_document_vectors(s3, sv, 'b', 'vb', 'idx', 'd') == 2
    sv.delete_vectors.assert_called_once_with(vectorBucketName='vb', indexName='idx', keys=['d#p1#c0', 'd#p2#c0'])
    s3.delete_object.assert_not_called()
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest test/lambda/test_penny_common.py test/lambda/test_penny_vectors.py -q`
Expected: FAIL with `ModuleNotFoundError` for `penny_common.textdoc` / `penny_common.vectors`

- [ ] **Step 4: Create `lambda/common/penny_common/textdoc.py`**

```python
"""Shape of text/{docId}.json — written by ParseLambda and the backfill script, read by IndexLambda."""


def text_doc_key(doc_id: str) -> str:
    return f'text/{doc_id}.json'


def display_name(file_key: str) -> str:
    """uploads/[demo-sid/]<uuid4>-<filename> -> <filename>"""
    name = file_key.rsplit('/', 1)[-1]
    parts = name.split('-', 5)
    return parts[5] if len(parts) == 6 else name


def build_text_doc(doc_id, file_key, doc_type, pages, entries, session_id, uploaded_at,
                   statement_period=None) -> dict:
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
```

- [ ] **Step 5: Create `lambda/common/penny_common/vectors.py`**

```python
"""S3 Vectors helpers: batched writes/deletes and per-document manifests."""
import json

from botocore.exceptions import ClientError

from penny_common.chunking import CHUNKER_VERSION

BATCH_SIZE = 500  # PutVectors / DeleteVectors limit


def manifest_key(doc_id: str) -> str:
    return f'manifests/{doc_id}.json'


def read_manifest(s3, bucket: str, doc_id: str):
    try:
        obj = s3.get_object(Bucket=bucket, Key=manifest_key(doc_id))
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') in ('NoSuchKey', '404'):
            return None
        raise
    return json.loads(obj['Body'].read())


def write_manifest(s3, bucket: str, doc_id: str, keys: list) -> None:
    body = {'docId': doc_id, 'chunkerVersion': CHUNKER_VERSION, 'keys': keys}
    s3.put_object(Bucket=bucket, Key=manifest_key(doc_id),
                  Body=json.dumps(body).encode(), ContentType='application/json')


def _batches(items: list, size: int = BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def put_vectors(s3vectors, vector_bucket: str, index: str, vectors: list) -> None:
    for batch in _batches(vectors):
        s3vectors.put_vectors(vectorBucketName=vector_bucket, indexName=index, vectors=batch)


def delete_keys(s3vectors, vector_bucket: str, index: str, keys: list) -> None:
    for batch in _batches(keys):
        s3vectors.delete_vectors(vectorBucketName=vector_bucket, indexName=index, keys=batch)


def list_keys_for_doc(s3vectors, vector_bucket: str, index: str, doc_id: str) -> list:
    """Fallback when a manifest is missing: scan the whole index (ListVectors has no filter)."""
    keys, token = [], None
    while True:
        kwargs = {'vectorBucketName': vector_bucket, 'indexName': index,
                  'maxResults': 1000, 'returnMetadata': True}
        if token:
            kwargs['nextToken'] = token
        resp = s3vectors.list_vectors(**kwargs)
        keys += [v['key'] for v in resp.get('vectors', [])
                 if (v.get('metadata') or {}).get('docId') == doc_id]
        token = resp.get('nextToken')
        if not token:
            return keys


def delete_document_vectors(s3, s3vectors, bucket: str, vector_bucket: str, index: str, doc_id: str) -> int:
    """Delete every vector for doc_id using its manifest; returns the number of keys deleted."""
    manifest = read_manifest(s3, bucket, doc_id)
    keys = manifest['keys'] if manifest else list_keys_for_doc(s3vectors, vector_bucket, index, doc_id)
    delete_keys(s3vectors, vector_bucket, index, keys)
    if manifest:
        s3.delete_object(Bucket=bucket, Key=manifest_key(doc_id))
    return len(keys)
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest test/lambda/test_penny_common.py test/lambda/test_penny_vectors.py -q`
Expected: `25 passed`

- [ ] **Step 7: Commit (user)**

```bash
git add lambda/common/penny_common/textdoc.py lambda/common/penny_common/vectors.py test/lambda/test_penny_common.py test/lambda/test_penny_vectors.py
git commit -m "feat: add text-doc schema and S3 Vectors manifest helpers"
```

---

### Task 7: ParseLambda — Decimal money end to end

**Files:**
- Modify: `lambda/parse/index.py` (imports; `validate_balance` lines 33–36; the fence-stripping block in `parse_with_claude` lines 109–119)
- Test: `test/lambda/test_parse_evidence.py`

- [ ] **Step 1: Write failing tests** — create `test/lambda/test_parse_evidence.py`:

```python
import io
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pdf_fixtures import make_pdf


@pytest.fixture
def parse(lambda_module, monkeypatch):
    index = lambda_module('parse')
    monkeypatch.setattr(index, 'APP_BUCKET', 'app')
    return index


def test_load_claude_json_uses_decimal(parse):
    fence = '`' * 3  # Claude sometimes wraps JSON in a markdown code fence
    out = parse.load_claude_json(f'{fence}json\n{{"entries": [{{"lines": [{{"amount": 0.1}}]}}]}}\n{fence}')
    assert out['entries'][0]['lines'][0]['amount'] == Decimal('0.1')


def test_validate_balance_exact_decimal(parse):
    lines = [{'direction': 'DEBIT', 'amount': Decimal('0.1')}, {'direction': 'DEBIT', 'amount': Decimal('0.2')},
             {'direction': 'CREDIT', 'amount': Decimal('0.3')}]
    assert parse.validate_balance(lines) is True
    assert parse.validate_balance([{'direction': 'DEBIT', 'amount': Decimal('100.00')},
                                   {'direction': 'CREDIT', 'amount': Decimal('99.99')}]) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_parse_evidence.py -q`
Expected: FAIL — `AttributeError: module 'lambda_parse' has no attribute 'load_claude_json'`

- [ ] **Step 3: Implement** in `lambda/parse/index.py`.

Add after `from datetime import datetime, timezone`:
```python
from decimal import Decimal
```
Replace `validate_balance`:
````python
def validate_balance(lines: list) -> bool:
    debit  = sum(Decimal(str(l['amount'])) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(Decimal(str(l['amount'])) for l in lines if l['direction'] == 'CREDIT')
    return debit == credit


def load_claude_json(raw: str) -> dict:
    """Strip optional markdown fences and parse with Decimal so money never passes through float."""
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1]
        raw = raw.rsplit('```', 1)[0]
    return json.loads(raw, parse_float=Decimal)
````
In `parse_with_claude`, replace everything from `    raw = result['content'][0]['text']` through `    parsed = json.loads(raw)` with:
```python
    parsed = load_claude_json(result['content'][0]['text'])
```
(keep `return parsed.get('entries', [])` for now; Task 8 changes it).

- [ ] **Step 4: Run to verify pass, including the existing parse tests**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_parse_evidence.py test/lambda/test_parse.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit (user)**

```bash
git add lambda/parse/index.py test/lambda/test_parse_evidence.py
git commit -m "fix: parse Claude amounts as Decimal and check balance exactly"
```

---

### Task 8: ParseLambda — pypdf pages, evidence, transcripts, text doc

**Files:**
- Modify: `lambda/parse/index.py`
- Test: `test/lambda/test_parse_evidence.py` (append)

- [ ] **Step 1: Append failing tests**

```python
def test_transcribe_instruction(parse):
    assert '"transcripts": []' in parse.build_transcribe_instruction([])
    assert 'Pages 2, 3' in parse.build_transcribe_instruction([2, 3])


PAGES = [{'page': 1, 'text': 'Statement\n03/14  ABC UTILITIES   -120.00\n', 'extractor': 'pypdf'},
         {'page': 2, 'text': 'scanned text 4111111111111111', 'extractor': 'claude'}]


def test_evidence_accepted(parse):
    ev = parse.build_evidence({'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -120.00'}},
                              PAGES, 'd1', 'bank_statement')
    assert ev == [{'docId': 'd1', 'sourceType': 'bank_statement', 'page': 1,
                   'text': '03/14 ABC UTILITIES -120.00', 'chunkKey': None}]


def test_evidence_fabricated_rejected(parse, capsys):
    ev = parse.build_evidence({'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -210.00'}},
                              PAGES, 'd1', 'bank_statement')
    assert ev == []
    assert 'evidence_rejected' in capsys.readouterr().out


def test_evidence_unknown_page_rejected(parse):
    assert parse.build_evidence({'evidence': {'page': 9, 'text': 'x'}}, PAGES, 'd1', 'bank_statement') == []


def test_evidence_on_transcribed_page_not_validated_but_masked(parse):
    ev = parse.build_evidence({'evidence': {'page': 2, 'text': 'card 4111111111111111'}},
                              PAGES, 'd1', 'bank_statement')
    assert ev[0]['text'] == 'card ****1111'


def test_evidence_missing(parse):
    assert parse.build_evidence({}, PAGES, 'd1', 'receipt') == []


def test_merge_transcripts_only_fills_claude_pages(parse):
    pages = [{'page': 1, 'text': 'pdf text', 'extractor': 'pypdf'}, {'page': 2, 'text': '', 'extractor': 'claude'}]
    out = parse.merge_transcripts(pages, [{'page': 2, 'text': 'from claude'}, {'page': 1, 'text': 'ignored'}])
    assert out[0]['text'] == 'pdf text' and out[1]['text'] == 'from claude'


def test_s3_event_writes_evidence_and_text_doc(parse, monkeypatch):
    pdf = make_pdf([['Statement March', '03/14 ABC UTILITIES -120.00']])
    s3 = MagicMock()
    s3.get_object.return_value = {'Body': io.BytesIO(pdf)}
    monkeypatch.setattr(parse, 's3_client', s3)
    table = MagicMock()
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    monkeypatch.setattr(parse, 'is_duplicate', lambda h: False)
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda h: False)
    monkeypatch.setattr(parse, 'get_accounts', lambda: [])
    seen = {}

    def fake_claude(data, media, accounts, transcribe_pages):
        seen['transcribe'] = transcribe_pages
        return {'entries': [{'date': '2026-03-14', 'description': 'ABC UTILITIES',
                             'lines': [{'accountId': '5100', 'direction': 'DEBIT', 'amount': Decimal('120.00')},
                                       {'accountId': '1100', 'direction': 'CREDIT', 'amount': Decimal('120.00')}],
                             'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -120.00'}}],
                'transcripts': []}

    monkeypatch.setattr(parse, 'parse_with_claude', fake_claude)
    key = 'uploads/123e4567-e89b-12d3-a456-426614174000-mar.pdf'
    parse.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': key}}}]}, None)

    assert seen['transcribe'] == []
    entry_item = table.put_item.call_args_list[0].kwargs['Item']
    assert entry_item['evidence'][0]['page'] == 1
    assert entry_item['evidence'][0]['docId'] == entry_item['fileHash']
    put = s3.put_object.call_args.kwargs
    assert put['Key'] == f"text/{entry_item['fileHash']}.json"
    doc = json.loads(put['Body'])
    assert doc['fileName'] == 'mar.pdf' and doc['docType'] == 'bank_statement' and doc['sessionId'] is None
    assert doc['pages'][0]['extractor'] == 'pypdf'
    assert doc['entries'] == [{'entryId': entry_item['entryId'], 'page': 1,
                               'evidenceText': '03/14 ABC UTILITIES -120.00'}]
```

- [ ] **Step 2: Run to verify failure**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_parse_evidence.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'build_transcribe_instruction'` (and similar)

- [ ] **Step 3: Add imports** below `from decimal import Decimal` in `lambda/parse/index.py`:

```python

from penny_common.masking import mask_identifiers
from penny_common.pdftext import extract_pdf_pages
from penny_common.textdoc import build_text_doc, text_doc_key
from penny_common.textnorm import contains_normalized
```

- [ ] **Step 4: Add the transcription instruction and change the `parse_with_claude` signature.** Replace the line `def parse_with_claude(file_data: bytes, media_type: str, accounts: list) -> list:` with:

```python
def build_transcribe_instruction(transcribe_pages: list) -> str:
    if not transcribe_pages:
        return 'Return "transcripts": [].'
    pages = ', '.join(str(p) for p in transcribe_pages)
    return (f'Pages {pages} have no extractable text layer. For each of those pages, add an item to '
            '"transcripts" with the page number and a faithful line-by-line transcription of all visible text.')


def parse_with_claude(file_data: bytes, media_type: str, accounts: list, transcribe_pages: list) -> dict:
```

- [ ] **Step 5: Update the prompt inside `parse_with_claude`.** After the line `If classification is uncertain, add a note.`, insert:

```text
For each entry, set "evidence" to the 1-based page number and the exact source line(s) copied verbatim from the document (do not paraphrase or reformat).
{build_transcribe_instruction(transcribe_pages)}
```
Then replace the JSON example's closing part:
```text
        {{ "accountId": "...", "direction": "CREDIT", "amount": 0.00, "note": "..." }}
      ]
    }}
  ]
}}"""
```
with:
```text
        {{ "accountId": "...", "direction": "CREDIT", "amount": 0.00, "note": "..." }}
      ],
      "evidence": {{ "page": 1, "text": "verbatim source line" }}
    }}
  ],
  "transcripts": [
    {{ "page": 1, "text": "..." }}
  ]
}}"""
```
Change `"max_tokens": 4096,` to `"max_tokens": 16000,` (transcripts of scanned pages need room).

- [ ] **Step 6: Change the return value** of `parse_with_claude` from `return parsed.get('entries', [])` to:

```python
    return {'entries': parsed.get('entries', []), 'transcripts': parsed.get('transcripts', [])}
```

- [ ] **Step 7: Add helpers** directly after `parse_with_claude`:

```python
def merge_transcripts(pages: list, transcripts: list) -> list:
    """Fill Claude transcripts into pages that had no text layer."""
    by_page = {int(t['page']): t.get('text', '') for t in transcripts}
    return [
        {**p, 'text': by_page.get(p['page'], '')} if p['extractor'] == 'claude' else p
        for p in pages
    ]


def build_evidence(entry: dict, pages: list, doc_id: str, source_type: str) -> list:
    """Validate Claude's evidence against independently extracted text, then mask it."""
    ev = entry.get('evidence') or {}
    text = (ev.get('text') or '').strip()
    if not text:
        return []
    page_no = int(ev.get('page') or 1)
    page = next((p for p in pages if p['page'] == page_no), None)
    if page is None:
        print(json.dumps({'event': 'evidence_rejected', 'docId': doc_id, 'reason': 'unknown_page'}))
        return []
    # Only text-layer PDF pages have an independent baseline to check against.
    if page['extractor'] == 'pypdf' and not contains_normalized(page['text'], text):
        print(json.dumps({'event': 'evidence_rejected', 'docId': doc_id, 'reason': 'not_in_page'}))
        return []
    return [{
        'docId':      doc_id,
        'sourceType': source_type,
        'page':       page_no,
        'text':       mask_identifiers(text),
        'chunkKey':   None,
    }]


def write_text_doc(doc: dict) -> None:
    """ParseLambda is the only writer of text/; this object triggers IndexLambda."""
    s3_client.put_object(
        Bucket=APP_BUCKET,
        Key=text_doc_key(doc['docId']),
        Body=json.dumps(doc, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json',
    )
```

- [ ] **Step 8: Make `save_pending_entries` store evidence and return the saved links.** Replace its signature and first lines:

```python
def save_pending_entries(entries: list, file_key: str, file_hash: str, source: str,
                         session_id: str = None, pages: list = None, source_type: str = 'receipt') -> list:
    """Persist entries; return [{entryId, page, evidenceText}] for entries that kept evidence."""
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)
    saved = []
```
After the existing `if session_id:` / `item['sessionId'] = session_id` block and before `entries_table.put_item(Item=item)`, insert:
```python
        evidence = build_evidence(entry, pages or [], file_hash, source_type)
        if evidence:
            item['evidence'] = evidence
            saved.append({'entryId': entry_id, 'page': evidence[0]['page'], 'evidenceText': evidence[0]['text']})
```
After the inner `for i, line in enumerate(entry['lines']):` loop (at function indentation, after the outer `for`), add:
```python
    return saved
```

- [ ] **Step 9: Update the S3-event branch of `handler`.** Replace the block from `        ext = key.rsplit('.', 1)[-1].lower()` through the final `print(...)` with:

```python
        ext = key.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            media_type = 'application/pdf'
            source = 'PDF'
            source_type = 'bank_statement'
            pages = extract_pdf_pages(file_data)
        else:
            media_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/png'
            source = 'RECEIPT'
            source_type = 'receipt'
            pages = [{'page': 1, 'text': '', 'extractor': 'claude'}]

        transcribe_pages = [p['page'] for p in pages if p['extractor'] == 'claude']
        accounts = get_accounts()
        parsed   = parse_with_claude(file_data, media_type, accounts, transcribe_pages)
        pages    = merge_transcripts(pages, parsed['transcripts'])
        entries  = parsed['entries']
        saved    = save_pending_entries(entries, key, file_hash, source, session_id=session_id,
                                        pages=pages, source_type=source_type)
        write_text_doc(build_text_doc(file_hash, key, source_type, pages, saved, session_id,
                                      datetime.now(timezone.utc).isoformat()))
        print(f'Parsed {len(entries)} entries from {key} (session={session_id}, evidence={len(saved)})')
```

- [ ] **Step 10: Run all tests**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda -q -p no:cacheprovider`
Expected: `55 passed`

- [ ] **Step 11: Commit (user)**

```bash
git add lambda/parse/index.py test/lambda/test_parse_evidence.py
git commit -m "feat: capture validated per-entry evidence and write text docs on parse"
```

---

### Task 9: IndexLambda

**Files:**
- Create: `lambda/indexer/index.py`
- Test: `test/lambda/test_indexer.py`

- [ ] **Step 1: Write failing tests** — create `test/lambda/test_indexer.py`:

```python
import io
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def idx(lambda_module, monkeypatch):
    index = lambda_module('indexer')
    monkeypatch.setattr(index, 'VECTOR_BUCKET', 'vb')
    monkeypatch.setattr(index, 'APP_BUCKET', 'app')
    monkeypatch.setattr(index, 's3', MagicMock())
    monkeypatch.setattr(index, 's3vectors', MagicMock())
    monkeypatch.setattr(index, 'dynamodb', MagicMock())
    monkeypatch.setattr(index, 'embed', lambda text: ([0.1] * 512, 7))
    return index


DOC = {'docId': 'd1', 'docType': 'bank_statement', 'fileName': 'mar.pdf', 'fileKey': 'uploads/u-mar.pdf',
       'uploadedAt': '2026-04-20T00:00:00Z', 'statementPeriod': None, 'sessionId': None,
       'pages': [{'page': 1, 'text': '03/14 ABC UTILITIES -120.00\n03/15 COFFEE -4.50', 'extractor': 'pypdf'}],
       'entries': [{'entryId': 'e1', 'page': 1, 'evidenceText': '03/14 ABC UTILITIES -120.00'},
                   {'entryId': 'e2', 'page': 1, 'evidenceText': 'not in doc'}]}


def _no_manifest(idx):
    idx.s3.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')


def test_build_vector_metadata(idx):
    v = idx.build_vector({'key': 'd1#p1#c0', 'page': 1, 'yearMonth': '2026-03', 'text': 't'}, DOC, [0.5] * 512)
    assert v['metadata']['sessionId'] == 'owner'
    assert v['metadata']['docId'] == 'd1'
    assert len(v['data']['float32']) == 512


def test_index_document_puts_vectors_writes_manifest_backfills(idx):
    _no_manifest(idx)
    summary = idx.index_document(DOC)
    assert summary['chunks'] == 1 and summary['embedTokens'] == 7
    put = idx.s3vectors.put_vectors.call_args.kwargs
    assert put['indexName'] == 'penny-docs' and put['vectors'][0]['key'] == 'd1#p1#c0'
    idx.s3vectors.delete_vectors.assert_not_called()
    assert json.loads(idx.s3.put_object.call_args.kwargs['Body'])['keys'] == ['d1#p1#c0']
    upd = idx.dynamodb.Table.return_value.update_item
    upd.assert_called_once()
    assert upd.call_args.kwargs['Key'] == {'entryId': 'e1'}
    assert upd.call_args.kwargs['ExpressionAttributeValues'] == {':k': 'd1#p1#c0'}
    assert summary['chunkKeysFilled'] == 1


def test_reindex_deletes_stale_keys(idx):
    idx.s3.get_object.return_value = {
        'Body': io.BytesIO(json.dumps({'keys': ['d1#p1#c0', 'd1#p1#c1', 'd1#p2#c0']}).encode())}
    summary = idx.index_document(DOC)
    idx.s3vectors.delete_vectors.assert_called_once_with(
        vectorBucketName='vb', indexName='penny-docs', keys=['d1#p1#c1', 'd1#p2#c0'])
    assert summary['staleDeleted'] == 2


def test_demo_session_metadata(idx):
    _no_manifest(idx)
    idx.index_document({**DOC, 'sessionId': 'abc'})
    assert idx.s3vectors.put_vectors.call_args.kwargs['vectors'][0]['metadata']['sessionId'] == 'abc'


def test_handler_reads_text_and_never_writes_text_prefix(idx):
    calls = []

    def get_object(Bucket, Key):
        calls.append(Key)
        if Key.startswith('manifests/'):
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(json.dumps(DOC).encode())}

    idx.s3.get_object.side_effect = get_object
    idx.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'text/d1.json'}}}]}, None)
    assert calls[0] == 'text/d1.json'
    assert [c.kwargs['Key'] for c in idx.s3.put_object.call_args_list] == ['manifests/d1.json']
```

- [ ] **Step 2: Run to verify failure**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_indexer.py -q`
Expected: FAIL — `FileNotFoundError` for `lambda/indexer/index.py`

- [ ] **Step 3: Create `lambda/indexer/index.py`**

```python
import json
import os
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config

from penny_common.chunking import chunk_document
from penny_common.textnorm import contains_normalized
from penny_common.vectors import read_manifest, write_manifest, put_vectors, delete_keys

_retry = Config(retries={'max_attempts': 8, 'mode': 'adaptive'})
s3        = boto3.client('s3')
bedrock   = boto3.client('bedrock-runtime', region_name='us-east-1', config=_retry)
s3vectors = boto3.client('s3vectors', region_name='us-east-1', config=_retry)
dynamodb  = boto3.resource('dynamodb')

APP_BUCKET    = os.environ.get('APP_BUCKET', '')
VECTOR_BUCKET = os.environ.get('VECTOR_BUCKET', '')
VECTOR_INDEX  = os.environ.get('VECTOR_INDEX', 'penny-docs')
ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')

EMBED_MODEL_ID   = 'amazon.titan-embed-text-v2:0'
EMBED_DIMENSIONS = 512


def embed(text: str) -> tuple:
    """Return (embedding, input_token_count) from Titan Text Embeddings V2."""
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=json.dumps({'inputText': text, 'dimensions': EMBED_DIMENSIONS, 'normalize': True}),
    )
    out = json.loads(resp['body'].read())
    return out['embedding'], out.get('inputTextTokenCount', 0)


def build_vector(chunk: dict, doc: dict, embedding: list) -> dict:
    return {
        'key': chunk['key'],
        'data': {'float32': [float(x) for x in embedding]},
        'metadata': {
            # filterable
            'docId':     doc['docId'],
            'docType':   doc['docType'],
            'yearMonth': chunk['yearMonth'],
            'sessionId': doc.get('sessionId') or 'owner',
            # non-filterable (configured on the index)
            'text':      chunk['text'],
            'page':      chunk['page'],
            'fileKey':   doc['fileKey'],
            'fileName':  doc['fileName'],
        },
    }


def find_chunk_key(chunks: list, page: int, evidence_text: str):
    for c in chunks:
        if c['page'] == page and contains_normalized(c['text'], evidence_text):
            return c['key']
    return None


def backfill_chunk_keys(doc: dict, chunks: list) -> int:
    table = dynamodb.Table(ENTRIES_TABLE)
    filled = 0
    for e in doc.get('entries', []):
        key = find_chunk_key(chunks, e['page'], e['evidenceText'])
        if key is None:
            print(json.dumps({'event': 'chunk_key_not_found', 'docId': doc['docId'], 'entryId': e['entryId']}))
            continue
        table.update_item(
            Key={'entryId': e['entryId']},
            UpdateExpression='SET evidence[0].chunkKey = :k',
            ConditionExpression='attribute_exists(evidence)',
            ExpressionAttributeValues={':k': key},
        )
        filled += 1
    return filled


def index_document(doc: dict) -> dict:
    chunks = chunk_document(doc)
    new_keys = [c['key'] for c in chunks]

    vectors, tokens = [], 0
    for c in chunks:
        emb, n = embed(c['text'])
        tokens += n
        vectors.append(build_vector(c, doc, emb))
    put_vectors(s3vectors, VECTOR_BUCKET, VECTOR_INDEX, vectors)

    old = read_manifest(s3, APP_BUCKET, doc['docId'])
    stale = sorted(set(old['keys']) - set(new_keys)) if old else []
    delete_keys(s3vectors, VECTOR_BUCKET, VECTOR_INDEX, stale)
    write_manifest(s3, APP_BUCKET, doc['docId'], new_keys)

    filled = backfill_chunk_keys(doc, chunks)
    summary = {'event': 'indexed', 'docId': doc['docId'], 'chunks': len(chunks),
               'staleDeleted': len(stale), 'chunkKeysFilled': filled, 'embedTokens': tokens}
    print(json.dumps(summary))
    return summary


def handler(event, context):
    # Reads text/ only; never writes to text/ (that would re-trigger this Lambda).
    for record in event.get('Records', []):
        key = unquote_plus(record['s3']['object']['key'])
        obj = s3.get_object(Bucket=record['s3']['bucket']['name'], Key=key)
        index_document(json.loads(obj['Body'].read()))
```

- [ ] **Step 4: Run to verify pass**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda -q -p no:cacheprovider`
Expected: `60 passed`

- [ ] **Step 5: Commit (user)**

```bash
git add lambda/indexer/index.py test/lambda/test_indexer.py
git commit -m "feat: add IndexLambda to embed documents into S3 Vectors"
```

---

### Task 10: Evidence API

**Files:**
- Modify: `lambda/query/index.py` (constants after line 15; new function before `handler`; route at the top of `handler`)
- Test: `test/lambda/test_query_evidence.py`

- [ ] **Step 1: Write failing tests** — create `test/lambda/test_query_evidence.py`:

```python
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def q(lambda_module, monkeypatch):
    index = lambda_module('query')
    monkeypatch.setattr(index, 'APP_BUCKET', 'app')
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = 'https://signed'
    monkeypatch.setattr(index, 's3_client', s3)
    return index


def _entry(q, monkeypatch, item):
    table = MagicMock()
    table.get_item.return_value = {'Item': item} if item else {}
    monkeypatch.setattr(q, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))


EV = {'docId': 'h1', 'sourceType': 'bank_statement', 'page': Decimal('2'), 'text': 't', 'chunkKey': None}


def _call(q, session=None):
    headers = {'X-Session-Id': session} if session else {}
    return q.handler({'path': '/api/entries/e1/evidence', 'pathParameters': {'id': 'e1'},
                      'headers': headers}, None)


def test_evidence_owner_array_with_deduped_presign(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'uploads/x.pdf',
                            'evidence': [EV, {**EV, 'page': Decimal('3')}]})
    resp = _call(q)
    body = json.loads(resp['body'])
    assert resp['statusCode'] == 200
    assert [e['page'] for e in body['evidence']] == [2, 3]
    assert all(e['fileUrl'] == 'https://signed' for e in body['evidence'])
    q.s3_client.generate_presigned_url.assert_called_once()


def test_evidence_absent_returns_empty_list(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k'})
    assert json.loads(_call(q)['body']) == {'evidence': []}


def test_evidence_other_session_is_404(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'sessionId': 'abc', 'evidence': [EV]})
    assert _call(q)['statusCode'] == 404
    assert _call(q, session='zzz')['statusCode'] == 404
    assert _call(q, session='abc')['statusCode'] == 200


def test_evidence_missing_entry_is_404(q, monkeypatch):
    _entry(q, monkeypatch, None)
    assert _call(q)['statusCode'] == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_query_evidence.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 's3_client'`

- [ ] **Step 3: Implement** in `lambda/query/index.py`. After the `MONTHLY_CACHE_TABLE = ...` line add:

```python
APP_BUCKET           = os.environ.get('APP_BUCKET', '')
EVIDENCE_URL_TTL     = 300  # seconds

s3_client = boto3.client('s3')
```
Immediately before `def handler(event, context):` add:
```python
def get_entry_evidence(entry_id: str, session_id) -> dict:
    """Return {'evidence': [...]} for an entry, or None if missing or owned by another session."""
    entry = dynamodb.Table(ENTRIES_TABLE).get_item(Key={'entryId': entry_id}).get('Item')
    if not entry or entry.get('sessionId') != session_id:
        return None
    urls = {}
    out = []
    for ev in entry.get('evidence', []):
        doc_id = ev.get('docId')
        if doc_id not in urls:
            # v1: every evidence item comes from the entry's own uploaded file.
            same_file = doc_id == entry.get('fileHash') and entry.get('fileKey')
            urls[doc_id] = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': APP_BUCKET, 'Key': entry['fileKey']},
                ExpiresIn=EVIDENCE_URL_TTL,
            ) if same_file else None
        out.append({**ev, 'page': int(ev['page']), 'fileUrl': urls[doc_id]})
    return {'evidence': out}


```
In `handler`, replace:
```python
    session_id = headers.get('x-session-id') or headers.get('X-Session-Id') or None

    accounts_dict = get_all_accounts()
```
with:
```python
    session_id = headers.get('x-session-id') or headers.get('X-Session-Id') or None

    # GET /api/entries/{id}/evidence — handled before the accounts scan below
    if path.endswith('/evidence'):
        entry_id = (event.get('pathParameters') or {}).get('id', '')
        result = get_entry_evidence(entry_id, session_id)
        if result is None:
            return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'not found'})}
        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps(result, cls=DecimalEncoder)}

    accounts_dict = get_all_accounts()
```

- [ ] **Step 4: Run to verify pass**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda -q -p no:cacheprovider`
Expected: `64 passed`

- [ ] **Step 5: Commit (user)**

```bash
git add lambda/query/index.py test/lambda/test_query_evidence.py
git commit -m "feat: add GET /api/entries/{id}/evidence with presigned source URLs"
```

---

### Task 11: CDK — vector store, IndexLambda, DLQ, IAM, route; remove unused secret

**Files:**
- Modify: `lib/finance-stack.ts`, `test/finance-stack.test.ts`

- [ ] **Step 1: Write failing CDK tests.** In `test/finance-stack.test.ts`:

Change the Lambda count test to:
```ts
test('creates ten app Lambda functions plus the S3 notifications handler', () => {
  template.resourceCountIs('AWS::Lambda::Function', 11);
});
```
Replace the `creates Secrets Manager secret for Claude API key` test with:
```ts
test('does not create the unused Claude API key secret', () => {
  const secrets = template.findResources('AWS::SecretsManager::Secret', {
    Properties: { Name: 'finance/claude-api-key' },
  });
  expect(Object.keys(secrets)).toHaveLength(0);
});
```
Append:
```ts
describe('RAG resources', () => {
  test('vector index is 512-dim cosine with non-filterable text metadata', () => {
    template.hasResourceProperties('AWS::S3Vectors::Index', {
      IndexName: 'penny-docs',
      DataType: 'float32',
      Dimension: 512,
      DistanceMetric: 'cosine',
      MetadataConfiguration: { NonFilterableMetadataKeys: ['text', 'page', 'fileKey', 'fileName'] },
    });
  });

  test('IndexLambda has a DLQ and 2 async retries', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'index.handler',
      DeadLetterConfig: Match.objectLike({ TargetArn: Match.anyValue() }),
    });
    template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', { MaximumRetryAttempts: 2 });
  });

  test('S3 notifies IndexLambda only for the text/ prefix', () => {
    const notif = Object.values(template.findResources('Custom::S3BucketNotifications'))[0] as any;
    const configs = notif.Properties.NotificationConfiguration.LambdaFunctionConfigurations;
    const prefixes = configs.map((c: any) => c.Filter.Key.FilterRules[0].Value).sort();
    expect(prefixes).toEqual(['text/', 'uploads/']);
  });

  test('embedding permission is scoped to Titan V2 only', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'bedrock:InvokeModel',
            Resource: Match.objectLike({
              'Fn::Join': Match.arrayWith([Match.arrayWith([Match.stringLikeRegexp('titan-embed-text-v2')])]),
            }),
          }),
        ]),
      },
    });
  });

  test('evidence route exists', () => {
    template.hasResourceProperties('AWS::ApiGateway::Resource', { PathPart: 'evidence' });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx jest`
Expected: FAIL — 6 failing (count 10 vs 11, secret present, no `AWS::S3Vectors::Index`, …)

- [ ] **Step 3: Edit imports in `lib/finance-stack.ts`.** Replace:
```ts
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
```
with:
```ts
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import * as sqs from 'aws-cdk-lib/aws-sqs';
```

- [ ] **Step 4: Delete the unused secret** (lines 22–26):
```ts
    // ── Secrets ──────────────────────────────────────────────
    const claudeSecret = new secretsmanager.Secret(this, 'ClaudeApiKey', {
      secretName: 'finance/claude-api-key',
      description: 'Anthropic Claude API key for finance app',
    });

```

- [ ] **Step 5: Update the layer description and the ParseLambda timeout.** Change `description: 'anthropic + boto3',` to `description: 'pinned boto3, pypdf, pywebpush + penny_common',`. In the `ParseLambda` props, change `timeout: cdk.Duration.seconds(60),` to `timeout: cdk.Duration.seconds(120),` (pypdf plus a longer Claude response).

- [ ] **Step 6: Add the RAG resources** immediately before `    // ── API Gateway ───────────────────────────────────────────`:

```ts
    // ── RAG: vector store + IndexLambda ───────────────────────
    const VECTOR_INDEX_NAME = 'penny-docs';
    const EMBED_MODEL_ARN = `arn:aws:bedrock:${cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0`;

    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'VectorBucket', {
      vectorBucketName: `penny-vectors-${cdk.Aws.ACCOUNT_ID}`,
    });
    const vectorIndex = new s3vectors.CfnIndex(this, 'DocsVectorIndex', {
      vectorBucketArn: vectorBucket.attrVectorBucketArn,
      indexName: VECTOR_INDEX_NAME,
      dataType: 'float32',
      dimension: 512,
      distanceMetric: 'cosine',
      metadataConfiguration: { nonFilterableMetadataKeys: ['text', 'page', 'fileKey', 'fileName'] },
    });

    const indexDlq = new sqs.Queue(this, 'IndexDlq', { retentionPeriod: cdk.Duration.days(14) });
    const indexFn = new lambda.Function(this, 'IndexLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/indexer')),
      layers: [pyLayer],
      environment: {
        ...lambdaEnv,
        VECTOR_BUCKET: vectorBucket.vectorBucketName!,
        VECTOR_INDEX: VECTOR_INDEX_NAME,
      },
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      retryAttempts: 2,
      deadLetterQueue: indexDlq,
    });
    indexFn.node.addDependency(vectorIndex);

    appBucket.grantRead(indexFn, 'text/*');
    appBucket.grantReadWrite(indexFn, 'manifests/*');
    entriesTable.grant(indexFn, 'dynamodb:UpdateItem');
    indexFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3vectors:PutVectors', 's3vectors:DeleteVectors', 's3vectors:ListVectors', 's3vectors:GetVectors'],
      resources: [vectorIndex.attrIndexArn],
    }));
    indexFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [EMBED_MODEL_ARN],
    }));

    // ParseLambda writes text/{docId}.json → IndexLambda. IndexLambda never writes text/.
    appBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(indexFn),
      { prefix: 'text/' },
    );

```

- [ ] **Step 7: Add the evidence route.** Directly before `    const confirmRes = entryRes.addResource('confirm');` insert:
```ts
    entryRes.addResource('evidence').addMethod('GET', new apigw.LambdaIntegration(queryFn));

```

- [ ] **Step 8: Add outputs.** After the `DistributionId` output add:
```ts
    new cdk.CfnOutput(this, 'VectorBucketName', { value: vectorBucket.vectorBucketName! });
    new cdk.CfnOutput(this, 'IndexDlqUrl', { value: indexDlq.queueUrl });
```

- [ ] **Step 9: Typecheck and test**

Run: `npx tsc --noEmit -p .`
Expected: no output
Run: `npx jest`
Expected: `Tests: 14 passed, 14 total`

- [ ] **Step 10: Commit (user)**

```bash
git add lib/finance-stack.ts test/finance-stack.test.ts
git commit -m "feat: provision S3 Vectors index, IndexLambda, DLQ, and evidence route"
```

---

### Task 12: Backfill and cleanup scripts

**Files:**
- Create: `scripts/backfill_index.py`, `scripts/delete_document_vectors.py`
- Test: `test/lambda/test_backfill.py`

- [ ] **Step 1: Write failing tests** — create `test/lambda/test_backfill.py`:

```python
import importlib.util
import io
import os
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from pdf_fixtures import make_pdf

_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts/backfill_index.py'))


@pytest.fixture
def backfill():
    spec = importlib.util.spec_from_file_location('backfill_index', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_documents_dedupes_paginates_and_skips_manual(backfill):
    table = MagicMock()
    table.scan.side_effect = [
        {'Items': [{'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '2026-03-02'},
                   {'entryId': 'manual'}], 'LastEvaluatedKey': {'entryId': 'x'}},
        {'Items': [{'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '2026-03-01'},
                   {'fileHash': 'h2', 'fileKey': 'uploads/demo-s/b.jpg', 'sessionId': 's',
                    'createdAt': '2026-03-05'}]},
    ]
    assert backfill.list_documents(table) == [
        {'docId': 'h1', 'fileKey': 'uploads/a.pdf', 'sessionId': None, 'uploadedAt': '2026-03-01'},
        {'docId': 'h2', 'fileKey': 'uploads/demo-s/b.jpg', 'sessionId': 's', 'uploadedAt': '2026-03-05'},
    ]
    assert table.scan.call_args_list[1].kwargs['ExclusiveStartKey'] == {'entryId': 'x'}


def test_build_pages_pdf_and_image_rules(backfill):
    s3 = MagicMock()
    s3.get_object.return_value = {'Body': io.BytesIO(make_pdf([['03/14 ABC UTILITIES -120.00 statement']]))}
    doc_type, pages = backfill.build_pages(s3, MagicMock(), 'app', 'uploads/a.pdf', include_images=False)
    assert doc_type == 'bank_statement' and pages[0]['extractor'] == 'pypdf'
    assert backfill.build_pages(s3, MagicMock(), 'app', 'uploads/r.jpg', include_images=False) is None
    assert backfill.build_pages(s3, MagicMock(), 'app', 'uploads/notes.txt', include_images=True) is None


def test_main_skips_existing_and_writes_new(backfill, monkeypatch):
    s3 = MagicMock()

    def head_object(Bucket, Key):
        if Key == 'text/h1.json':
            return {}
        raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')

    s3.head_object.side_effect = head_object
    s3.get_object.return_value = {'Body': io.BytesIO(make_pdf([['03/14 ABC UTILITIES -120.00 statement']]))}
    table = MagicMock()
    table.scan.return_value = {'Items': [
        {'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'},
        {'fileHash': 'h2', 'fileKey': 'uploads/b.pdf', 'createdAt': '2'},
        {'fileHash': 'h3', 'fileKey': 'uploads/c.png', 'createdAt': '3'},
    ]}
    monkeypatch.setattr(backfill.boto3, 'client', lambda name, **kw: s3 if name == 's3' else MagicMock())
    monkeypatch.setattr(backfill.boto3, 'resource', lambda name, **kw: MagicMock(Table=lambda n: table))
    stats = backfill.main(['--bucket', 'app'])
    assert stats == {'written': 1, 'exists': 1, 'skipped': 1}
    assert s3.put_object.call_args.kwargs['Key'] == 'text/h2.json'
```

- [ ] **Step 2: Run to verify failure**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda/test_backfill.py -q`
Expected: FAIL — `FileNotFoundError` for `scripts/backfill_index.py`

- [ ] **Step 3: Create `scripts/backfill_index.py`**

```python
#!/usr/bin/env python3
"""Backfill text/{docId}.json for documents uploaded before RAG indexing existed.

Each text/ object written here triggers IndexLambda, which chunks, embeds, and indexes it.
Old entries carry no evidence, so the backfilled docs have no entries[] to link.

Usage:
  python scripts/backfill_index.py --bucket <AppBucket output> [--include-images] [--force] [--dry-run]

PDFs use pypdf only (no model cost). Image receipts need a Claude transcription and are
skipped unless --include-images is given. Scanned PDF pages stay empty in backfill.
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lambda', 'common'))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from penny_common.pdftext import extract_pdf_pages  # noqa: E402
from penny_common.textdoc import build_text_doc, text_doc_key  # noqa: E402

TRANSCRIBE_MODEL_ID = 'us.anthropic.claude-sonnet-4-6'
IMAGE_TYPES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}


def list_documents(entries_table) -> list:
    """One record per uploaded file, from the earliest entry that references it."""
    docs = {}
    kwargs = {'ProjectionExpression': 'fileHash, fileKey, sessionId, createdAt'}
    while True:
        resp = entries_table.scan(**kwargs)
        for item in resp.get('Items', []):
            doc_id, file_key = item.get('fileHash'), item.get('fileKey')
            if not doc_id or not file_key:
                continue  # manual entries have no source file
            created = item.get('createdAt', '')
            if doc_id not in docs or created < docs[doc_id]['uploadedAt']:
                docs[doc_id] = {'docId': doc_id, 'fileKey': file_key,
                                'sessionId': item.get('sessionId'), 'uploadedAt': created}
        if 'LastEvaluatedKey' not in resp:
            return sorted(docs.values(), key=lambda d: d['docId'])
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']


def text_doc_exists(s3, bucket: str, doc_id: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=text_doc_key(doc_id))
        return True
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
            return False
        raise


def transcribe_image(bedrock, data: bytes, media_type: str) -> str:
    body = json.dumps({
        'anthropic_version': 'bedrock-2023-05-31',
        'max_tokens': 4096,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type,
                                         'data': base64.b64encode(data).decode('utf-8')}},
            {'type': 'text', 'text': 'Transcribe all visible text in this receipt line by line. Output only the text.'},
        ]}],
    })
    resp = bedrock.invoke_model(modelId=TRANSCRIBE_MODEL_ID, body=body)
    return json.loads(resp['body'].read())['content'][0]['text']


def build_pages(s3, bedrock, bucket: str, file_key: str, include_images: bool):
    """Return (doc_type, pages) or None when the file should be skipped."""
    ext = file_key.rsplit('.', 1)[-1].lower()
    if ext != 'pdf' and (ext not in IMAGE_TYPES or not include_images):
        return None
    data = s3.get_object(Bucket=bucket, Key=file_key)['Body'].read()
    if ext == 'pdf':
        return 'bank_statement', extract_pdf_pages(data)
    text = transcribe_image(bedrock, data, IMAGE_TYPES[ext])
    return 'receipt', [{'page': 1, 'text': text, 'extractor': 'claude'}]


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bucket', required=True, help='App bucket name (CDK output BucketName)')
    ap.add_argument('--table', default='finance-journal-entries')
    ap.add_argument('--include-images', action='store_true')
    ap.add_argument('--force', action='store_true', help='Rewrite text docs that already exist (re-index)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    s3 = boto3.client('s3')
    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
    table = boto3.resource('dynamodb').Table(args.table)

    stats = {'written': 0, 'exists': 0, 'skipped': 0}
    for doc in list_documents(table):
        if not args.force and text_doc_exists(s3, args.bucket, doc['docId']):
            stats['exists'] += 1
            continue
        built = build_pages(s3, bedrock, args.bucket, doc['fileKey'], args.include_images)
        if built is None:
            stats['skipped'] += 1
            continue
        doc_type, pages = built
        text_doc = build_text_doc(doc['docId'], doc['fileKey'], doc_type, pages, [],
                                  doc['sessionId'], doc['uploadedAt'])
        if args.dry_run:
            print(f"[dry-run] would write {text_doc_key(doc['docId'])} ({len(pages)} pages)")
        else:
            s3.put_object(Bucket=args.bucket, Key=text_doc_key(doc['docId']),
                          Body=json.dumps(text_doc, ensure_ascii=False).encode('utf-8'),
                          ContentType='application/json')
        stats['written'] += 1
    print(json.dumps(stats))
    return stats


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Create `scripts/delete_document_vectors.py`**

```python
#!/usr/bin/env python3
"""Delete all vectors (and the manifest) for one document.

Usage:
  python scripts/delete_document_vectors.py --bucket <BucketName> --vector-bucket <VectorBucketName> --doc-id <fileHash>
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lambda', 'common'))

import boto3  # noqa: E402

from penny_common.vectors import delete_document_vectors  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--vector-bucket', required=True)
    ap.add_argument('--index', default='penny-docs')
    ap.add_argument('--doc-id', required=True)
    args = ap.parse_args(argv)
    n = delete_document_vectors(boto3.client('s3'), boto3.client('s3vectors', region_name='us-east-1'),
                                args.bucket, args.vector_bucket, args.index, args.doc_id)
    print(f'Deleted {n} vectors for {args.doc_id}')
    return n


if __name__ == '__main__':
    main()
```

- [ ] **Step 5: Run all Python tests**

Run: `AWS_DEFAULT_REGION=us-east-1 python -m pytest test/lambda -q -p no:cacheprovider`
Expected: `67 passed`
Run: `python scripts/delete_document_vectors.py --help`
Expected: usage text printed, exit 0

- [ ] **Step 6: Commit (user)**

```bash
git add scripts/backfill_index.py scripts/delete_document_vectors.py test/lambda/test_backfill.py
git commit -m "feat: add backfill and vector cleanup scripts"
```

---

### Task 13: CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    env:
      AWS_DEFAULT_REGION: us-east-1   # boto3 clients are created at import time
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements-dev.txt
      - run: python -m pytest test/lambda -q

      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
      - run: npm ci
      - run: npx jest
      - run: npx cdk synth --quiet
```

- [ ] **Step 2: Verify the synth step locally**

Run: `npx cdk synth --quiet`
Expected: exits 0 with no errors. (`lambda/layer/` exists in a clean checkout because `requirements.txt` is tracked, so the asset path resolves.)

- [ ] **Step 3: Commit (user)**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run pytest, jest, and cdk synth on push"
```

---

### Task 14: Deploy and smoke test (user runs deploy)

`cdk deploy` changes live AWS resources, so the user runs it. **Use only synthetic documents.** The live demo has no auth (spec §12).

- [ ] **Step 1: Build the layer and deploy (user)**

```bash
./scripts/build-layer.sh
```
```bash
npx cdk deploy
```
Expected: the stack updates and new outputs `VectorBucketName` and `IndexDlqUrl` appear. CloudFormation deletes the `finance/claude-api-key` secret (scheduled deletion).

- [ ] **Step 2: Confirm Titan V2 model access** in the Bedrock console (us-east-1 → Model access → "Titan Text Embeddings V2" = Access granted). Without it, IndexLambda fails with `AccessDeniedException` and events land in the DLQ.

- [ ] **Step 3: Upload a synthetic text-based PDF statement** through the app's Upload page.

- [ ] **Step 4: Verify the pipeline in logs**

Run: `aws logs tail /aws/lambda/$(aws lambda list-functions --query "Functions[?contains(FunctionName,'ParseLambda')].FunctionName" --output text) --since 10m`
Expected: `Parsed N entries from uploads/... (session=None, evidence=N)`
Run: `aws logs tail /aws/lambda/$(aws lambda list-functions --query "Functions[?contains(FunctionName,'IndexLambda')].FunctionName" --output text) --since 10m`
Expected: `{"event": "indexed", "docId": "...", "chunks": ..., "chunkKeysFilled": N, ...}`

- [ ] **Step 5: Verify the evidence API** for one of the new entries (get an `entryId` from `GET /api/entries?status=PENDING`):

Run: `curl -s https://<SiteUrl>/api/entries/<entryId>/evidence`
Expected: `{"evidence": [{"docId": "...", "page": 1, "text": "...", "chunkKey": "<docId>#p1#c0", "fileUrl": "https://..."}]}`. Opening `fileUrl` shows the PDF.

- [ ] **Step 6: Verify the DLQ is empty**

Run: `aws sqs get-queue-attributes --queue-url <IndexDlqUrl> --attribute-names ApproximateNumberOfMessages`
Expected: `"ApproximateNumberOfMessages": "0"`

- [ ] **Step 7 (optional): Backfill existing uploads** (dry run first)

```bash
python scripts/backfill_index.py --bucket <BucketName> --dry-run
```
```bash
python scripts/backfill_index.py --bucket <BucketName>
```

- [ ] **Step 8: Check push notifications still work.** The layer now has Linux binaries, so this may fix them. Subscribe in the app and trigger the MonthlyReport Lambda from the console. Expected: a push notification arrives, and the PushNotification Lambda logs show no `ImportError`.

---

## Self-Review Notes

- **Spec coverage (Plan 1 scope):**

  | Spec section | Where it is covered |
  |---|---|
  | §2 decoupling, write-prefix isolation, secret removal, CDK resources, IAM | Tasks 9 and 11 |
  | §3 docId, demo-session isolation, money, evidence array, `text/` and manifest shapes, vector record | Tasks 6–11 |
  | §4 extraction, scanned pages, validation (text PDFs only), chunking, yearMonth priority, masking order, chunkKey backfill, deletion, backfill script | Tasks 4–9 and 12 |
  | §5 `GET /api/entries/{id}/evidence` | Task 10 |
  | §6 error handling: evidence rejection, DLQ, Titan retries via adaptive botocore config, chunkKey not found | Tasks 8, 9 and 11 |
  | §8 unit tests for this scope | Tasks 1–12 |
  | §10 CI | Task 13 |

- **Deferred to Plan 2:**
  - §5 agent, tools, citation validator, `POST /api/advisor` changes;
  - AdvisorLambda's `s3vectors:QueryVectors` + `s3vectors:GetVectors` + Titan permissions.
- **Deferred to Plan 3:**
  - §5 frontend;
  - §7 cost table with verified prices;
  - §9 eval.
- **Statement period:** `statementPeriod` is always written as null in v1, because the parse prompt does not request it. The yearMonth priority still works through the chunk's transaction dates and the upload-date fallback. Adding a `statementPeriod` field to the Claude prompt is a small Plan 2/3 follow-up if eval shows misassigned summary pages.
