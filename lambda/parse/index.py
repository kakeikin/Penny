import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import json
import os
import base64
import hashlib
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote_plus

from penny_common.fx import BASE, FxError, normalize_currency, parse_rates, to_base
from penny_common.masking import mask_identifiers
from penny_common.pdftext import PdfTextError, extract_pdf_pages
from penny_common.session import valid_session_id
from penny_common.textdoc import build_text_doc, doc_id_for, text_doc_key
from penny_common.textnorm import contains_normalized

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
# One attempt with a long read timeout: large statements can take minutes to generate, and the
# S3 async invocation already retries the whole record. Keep read_timeout below the Lambda timeout.
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1',
                       config=Config(read_timeout=270, connect_timeout=10, retries={'total_max_attempts': 1}))

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')
APP_BUCKET     = os.environ.get('APP_BUCKET', '')
EXCHANGE_RATES_TABLE = os.environ.get('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')

MODEL_ID            = 'us.anthropic.claude-sonnet-4-6'
MAX_OUTPUT_TOKENS   = 12000
MAX_EVIDENCE_CHARS  = 500

_DIGITS     = re.compile(r'[0-9]+')
_ISO_DAY    = re.compile(r'\d{4}-\d{2}-\d{2}')
_ISO_CURRENCY = re.compile(r'[A-Z]{3}')
MAX_AMOUNT  = Decimal('1000000000000')     # 1e12: anything larger is a parse error, not a transaction
DIRECTIONS  = ('DEBIT', 'CREDIT')


def compute_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def compute_entry_hash(entry: dict, currency: str = BASE) -> str:
    """Hash based on date + total amount + first 30 chars of description.

    Non-USD entries hash their printed total plus the currency code, so the same receipt
    matches itself on another day even after the exchange rate has changed.
    """
    date = entry.get('date', '')
    desc = entry.get('description', '')[:30].strip().lower()
    # Sum all debit amounts as the canonical amount
    # Stable with the pre-Decimal float hashes for amounts with <= 2 decimals.
    total = sum(Decimal(str(l['amount'])) for l in entry.get('lines', []) if l['direction'] == 'DEBIT')
    raw = f"{date}|{total:.2f}|{desc}" if currency == BASE else f"{date}|{currency}|{total:.2f}|{desc}"
    return hashlib.md5(raw.encode()).hexdigest()


def side_totals(lines: list) -> tuple:
    """(debit total, credit total) as exact Decimals."""
    debit  = sum(Decimal(str(l['amount'])) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(Decimal(str(l['amount'])) for l in lines if l['direction'] == 'CREDIT')
    return debit, credit


def validate_balance(lines: list) -> bool:
    debit, credit = side_totals(lines)
    return debit == credit


_FENCED_JSON = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.S | re.I)


def _reject_constant(name: str):
    raise ValueError(f'non-finite number in model output: {name}')


def load_claude_json(raw: str) -> dict:
    """Extract the JSON object from Claude's reply and parse it with Decimal (never float).

    Tolerates a markdown fence (also on one line) and prose before/after the JSON.
    NaN/Infinity are rejected.
    """
    m = _FENCED_JSON.search(raw)
    body = m.group(1) if m else raw[raw.find('{'): raw.rfind('}') + 1] or raw
    parsed = json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError('model output is not a JSON object')
    return parsed


def load_rates() -> dict:
    """Latest USD-based rates from ExchangeRateLambda's table; {} if unavailable."""
    try:
        item = dynamodb.Table(EXCHANGE_RATES_TABLE).get_item(Key={'base': BASE}).get('Item')
    except (BotoCoreError, ClientError) as e:
        print(json.dumps({'event': 'fx_rates_unavailable', 'errorType': type(e).__name__}))
        return {}
    return parse_rates(item)


def printed_currency(entry: dict) -> str:
    """The entry's currency as printed: a supported ISO code, a sanitized unsupported one
    (e.g. 'HKD'), 'XXX' for anything unreadable, or BASE if the model reported none."""
    try:
        return normalize_currency(entry.get('currency')) or BASE
    except FxError:
        raw = entry.get('currency')
        code = raw.strip().upper() if isinstance(raw, str) else ''
        return code if _ISO_CURRENCY.fullmatch(code) else 'XXX'


def convert_to_base(entry: dict, currency: str, rates_cache: dict) -> bool:
    """Convert entry['lines'] to USD in place. False if the amounts had to stay as printed."""
    if currency == BASE:
        return True
    try:
        if 'rates' not in rates_cache:
            rates_cache['rates'] = load_rates()
        entry['lines'] = to_base(entry['lines'], currency, rates_cache['rates'])
    except FxError:
        return False
    return True


def get_accounts() -> list:
    table = dynamodb.Table(ACCOUNTS_TABLE)
    result = table.scan()
    return result.get('Items', [])


def build_transcribe_instruction(transcribe_pages: list) -> str:
    if not transcribe_pages:
        return 'Return "transcripts": [].'
    pages = ', '.join(str(p) for p in transcribe_pages)
    return (f'Pages {pages} have no extractable text layer. For each of those pages, add an item to '
            '"transcripts" with "page" (1-based integer) and "text" (one string, lines joined with \\n) '
            'holding a faithful transcription of all visible text. Write "transcripts" after "entries".')


def parse_with_claude(file_data: bytes, media_type: str, accounts: list, transcribe_pages: list) -> dict:
    account_list = [{'accountId': a['accountId'], 'name': a['name'], 'type': a['type']} for a in accounts]
    account_json = json.dumps(account_list, ensure_ascii=False)
    prompt = f"""You are a professional accountant. Analyze this bank statement or receipt and output double-entry bookkeeping journal entries in JSON format.

Accounts must be selected from the following list:
{account_json}

For each transaction, produce one journal entry with balanced debit and credit lines.
Every amount must be a number with exactly 2 decimal places, and debits must equal credits exactly.
If classification is uncertain, add a note.
Set "currency" on each entry to the ISO 4217 code of its amounts as printed (e.g. "USD", "CNY"). Copy amounts exactly as printed; never convert them.
For each entry, set "evidence" to {{"page": <1-based integer>, "text": <one string: the exact source line(s) copied verbatim, multiple lines joined with \\n>}}. Do not paraphrase or reformat; omit "evidence" if there is no exact source line.
{build_transcribe_instruction(transcribe_pages)}

Output ONLY valid JSON, no explanation:
{{
  "entries": [
    {{
      "date": "YYYY-MM-DD",
      "description": "...",
      "currency": "USD",
      "lines": [
        {{ "accountId": "...", "direction": "DEBIT", "amount": 0.00, "note": "..." }},
        {{ "accountId": "...", "direction": "CREDIT", "amount": 0.00, "note": "..." }}
      ],
      "evidence": {{ "page": 1, "text": "verbatim source line" }}
    }}
  ],
  "transcripts": [
    {{ "page": 1, "text": "..." }}
  ]
}}"""

    # Build content block based on media type
    if media_type == 'application/pdf':
        content_block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(file_data).decode('utf-8')
            }
        }
    else:
        content_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(file_data).decode('utf-8')
            }
        }

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": prompt}
                ]
            }
        ]
    })

    response = bedrock.invoke_model(modelId=MODEL_ID, body=body)
    result = json.loads(response['body'].read())
    if result.get('stop_reason') == 'max_tokens' and transcribe_pages:
        # Transcripts share the output budget; drop them rather than lose the bookkeeping.
        print(json.dumps({'event': 'transcripts_truncated', 'pages': len(transcribe_pages)}))
        return parse_with_claude(file_data, media_type, accounts, [])
    parsed = load_claude_json(result['content'][0]['text'])
    entries = parsed.get('entries') or []
    transcripts = parsed.get('transcripts') or []
    return {'entries': entries if isinstance(entries, list) else [],
            'transcripts': transcripts if isinstance(transcripts, list) else []}


def _as_page(value):
    """A positive integer page number from int / integral Decimal / digit string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and value > 0 else None
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        return int(value) or None
    return None


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return '\n'.join(value)
    return ''


def merge_transcripts(pages: list, transcripts: list) -> list:
    """Fill Claude transcripts into pages that had no text layer; malformed items are ignored."""
    by_page = {}
    for t in transcripts if isinstance(transcripts, list) else []:
        n = _as_page(t.get('page')) if isinstance(t, dict) else None
        if n is not None:
            by_page[n] = _as_text(t.get('text'))
    return [
        {**p, 'text': by_page.get(p['page'], '')} if p['extractor'] == 'claude' else p
        for p in pages
    ]


def _reject_evidence(doc_id: str, reason: str) -> list:
    print(json.dumps({'event': 'evidence_rejected', 'docId': doc_id, 'reason': reason}))
    return []


def build_evidence(entry: dict, pages: list, doc_id: str, source_type: str) -> list:
    """Validate Claude's evidence against the page text, then mask it. Never raises on bad input."""
    ev = entry.get('evidence') if isinstance(entry, dict) else None
    if not isinstance(ev, dict):
        return []
    text = _as_text(ev.get('text')).strip()
    if not text:
        return []
    page_no = _as_page(ev.get('page'))
    if page_no is None:
        return _reject_evidence(doc_id, 'bad_page')
    if len(text) > MAX_EVIDENCE_CHARS:
        return _reject_evidence(doc_id, 'too_long')
    page = next((p for p in pages if p['page'] == page_no), None)
    if page is None:
        return _reject_evidence(doc_id, 'unknown_page')
    # pypdf pages are an independent baseline. Transcribed pages are only a consistency check
    # against Claude's own transcript (and are skipped if no transcript came back).
    if page['text'] and not contains_normalized(page['text'], text):
        return _reject_evidence(doc_id, 'not_in_page')
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


def normalize_entry(entry):
    """Return a bookable copy of a model-produced entry (Decimal amounts), or None if malformed."""
    if not isinstance(entry, dict):
        return None
    date = entry.get('date')
    if not isinstance(date, str) or not _ISO_DAY.fullmatch(date):
        return None
    lines = entry.get('lines')
    if not isinstance(lines, list) or len(lines) < 2:
        return None
    clean = []
    for line in lines:
        if not isinstance(line, dict):
            return None
        account, direction = line.get('accountId'), line.get('direction')
        if not isinstance(account, str) or not account or direction not in DIRECTIONS:
            return None
        try:
            amount = Decimal(str(line.get('amount')))
        except InvalidOperation:
            return None
        if not amount.is_finite() or amount < 0 or amount >= MAX_AMOUNT:
            return None             # direction carries the sign; amounts are never negative
        note = line.get('note', '')
        clean.append({'accountId': account, 'direction': direction, 'amount': amount,
                      'note': note if isinstance(note, str) else ''})
    description = entry.get('description', '')
    return {**entry, 'date': date, 'description': description if isinstance(description, str) else '',
            'lines': clean}


def save_pending_entries(entries: list, file_key: str, file_hash: str, source: str,
                         session_id: str = None, pages: list = None, source_type: str = None,
                         doc_id: str = None) -> list:
    """Persist entries; return [{entryId, page, evidenceText}] for entries that kept evidence."""
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)
    saved = []
    prepared = []
    rates_cache = {}    # exchange rates are read at most once per document, and only if needed

    # Pass 1: validate and build every item before writing anything. If a malformed entry
    # raised mid-write, the retry would see the file as a duplicate and the rest would be lost.
    for raw in entries if isinstance(entries, list) else []:
        entry = normalize_entry(raw)
        if entry is None:
            print(json.dumps({'event': 'entry_invalid_skipped', 'docId': doc_id or file_hash}))
            continue
        if not validate_balance(entry['lines']):
            # No amounts or dates in logs: transaction data stays out of CloudWatch.
            print(json.dumps({'event': 'entry_unbalanced_skipped', 'docId': doc_id or file_hash}))
            continue
        if not entry.get('currency'):
            print(json.dumps({'event': 'currency_missing_assumed_usd', 'docId': doc_id or file_hash}))
        currency   = printed_currency(entry)
        entry_hash = compute_entry_hash(entry, currency)      # printed amounts: rate-independent
        converted  = convert_to_base(entry, currency, rates_cache)
        if not converted:
            print(json.dumps({'event': 'fx_unconverted', 'docId': doc_id or file_hash}))

        entry_id   = str(uuid.uuid4())
        date       = entry['date']
        status     = 'DUPLICATE_SUSPECT' if is_duplicate_entry(entry_hash, session_id) else 'PENDING'

        item = {
            'entryId':     entry_id,
            'date':        date,
            'yearMonth':   date[:7],
            'description': entry['description'],
            'source':      source,
            'status':      status,
            'fileKey':     file_key,
            'fileHash':    file_hash,
            'entryHash':   entry_hash,
            'createdAt':   datetime.now(timezone.utc).isoformat(),
        }
        if session_id:
            item['sessionId'] = session_id
        if not converted:
            # Amounts are as printed, not USD: the Upload page warns and ConfirmLambda requires
            # an explicit acknowledgement before booking them.
            item['fxStatus'] = 'unconverted'
            item['printedCurrency'] = currency
        evidence = []
        if source_type:
            try:
                evidence = build_evidence(entry, pages or [], doc_id or file_hash, source_type)
            except Exception as e:  # defense in depth: evidence must never block bookkeeping
                print(json.dumps({'event': 'evidence_error', 'docId': doc_id or file_hash,
                                  'errorType': type(e).__name__}))
        if evidence:
            item['evidence'] = evidence
            saved.append({'entryId': entry_id, 'page': evidence[0]['page'], 'evidenceText': evidence[0]['text']})
        prepared.append((item, entry['lines']))

    # Pass 2: writes only. (A transient DynamoDB failure here can still leave a partial
    # document; see spec §12.)
    for item, lines in prepared:
        entries_table.put_item(Item=item)
        for i, line in enumerate(lines):
            line_item = {
                'entryId':   item['entryId'],
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),
                'note':      line['note'],
            }
            if 'originalCurrency' in line:      # same fields ManualEntryLambda writes
                line_item['originalCurrency'] = line['originalCurrency']
                line_item['originalAmount']   = str(line['originalAmount'])
                line_item['exchangeRate']     = str(line['exchangeRate'])
            lines_table.put_item(Item=line_item)
    return saved


def _exists_in_session(attr: str, value: str, session_id) -> bool:
    """True if any entry in the same session (owner = no sessionId) has attr == value.

    A Scan's Limit caps items *evaluated*, not matches, so we page until a match or the end.
    """
    table = dynamodb.Table(ENTRIES_TABLE)
    session = Attr('sessionId').eq(session_id) if session_id else Attr('sessionId').not_exists()
    kwargs = {'FilterExpression': Attr(attr).eq(value) & session, 'ProjectionExpression': 'entryId'}
    while True:
        result = table.scan(**kwargs)
        if result.get('Items'):
            return True
        if 'LastEvaluatedKey' not in result:
            return False
        kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']


def is_duplicate(file_hash: str, session_id=None) -> bool:
    return _exists_in_session('fileHash', file_hash, session_id)


def is_duplicate_entry(entry_hash: str, session_id=None) -> bool:
    """Check if a transaction with the same hash already exists in this session."""
    return _exists_in_session('entryHash', entry_hash, session_id)


def handler(event, context):
    # Presigned URL request (POST /api/upload)
    if event.get('httpMethod') == 'POST' and '/upload' in event.get('path', ''):
        body = json.loads(event.get('body', '{}'))
        filename = body.get('filename', 'upload')
        content_type = body.get('contentType', 'application/pdf')
        session_id = body.get('sessionId')  # None in owner mode
        if session_id is not None and not valid_session_id(session_id):
            return {
                'statusCode': 400,
                'headers': {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'invalid sessionId'}),
            }

        # Encode sessionId in the S3 key so the S3-triggered handler can read it
        if session_id:
            key = f'uploads/demo-{session_id}/{uuid.uuid4()}-{filename}'
        else:
            key = f'uploads/{uuid.uuid4()}-{filename}'

        url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': APP_BUCKET, 'Key': key, 'ContentType': content_type},
            ExpiresIn=300,
        )
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'},
            'body': json.dumps({'uploadUrl': url, 'key': key}),
        }

    # S3 event trigger. Process every record; re-raise the first failure at the end so
    # the async invocation is retried without one bad record skipping the others.
    failures = []
    for n, record in enumerate(event.get('Records', [])):
        try:
            process_upload(record['s3']['bucket']['name'],
                           unquote_plus(record['s3']['object']['key']))  # S3 event keys are URL-encoded
        except Exception as e:
            print(json.dumps({'event': 'parse_failed', 'record': n, 'errorType': type(e).__name__}))
            failures.append(e)
    if failures:
        raise failures[0]


def process_upload(bucket: str, key: str) -> None:
    # Extract sessionId from key: uploads/demo-{sid}/file  → sid
    # Owner uploads:             uploads/file              → None
    parts = key.split('/')
    session_id = None
    if len(parts) >= 2 and parts[1].startswith('demo-'):
        session_id = parts[1][5:]  # strip 'demo-' prefix
        if not valid_session_id(session_id):
            print(json.dumps({'event': 'invalid_session_key_skipped'}))
            return

    file_obj  = s3_client.get_object(Bucket=bucket, Key=key)
    file_data = file_obj['Body'].read()
    file_hash = compute_md5(file_data)
    doc_id    = doc_id_for(file_hash, session_id)

    if is_duplicate(file_hash, session_id):
        print(json.dumps({'event': 'duplicate_file_skipped', 'docId': doc_id}))
        return

    ext = key.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        media_type = 'application/pdf'
        source = 'PDF'
        source_type = 'bank_statement'
        try:
            pages = extract_pdf_pages(file_data)
        except PdfTextError as e:
            # RAG must never block bookkeeping: Claude still parses the PDF, just without evidence/index.
            print(json.dumps({'event': 'pdf_text_unavailable', 'docId': doc_id, 'reason': e.reason}))
            pages = []
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
    # No pages (unreadable PDF) means no evidence to look for: skip it instead of logging rejections.
    saved    = save_pending_entries(entries, key, file_hash, source, session_id=session_id, pages=pages,
                                    source_type=source_type if pages else None, doc_id=doc_id)
    if pages and not any((p['text'] or '').strip() for p in pages):
        # e.g. transcripts dropped after truncation: an empty doc would block a later backfill.
        print(json.dumps({'event': 'text_doc_skipped_empty', 'docId': doc_id}))
    elif pages:
        try:
            write_text_doc(build_text_doc(doc_id, key, source_type, pages, saved, session_id,
                                          datetime.now(timezone.utc).isoformat()))
        except Exception as e:  # bookkeeping already succeeded; backfill_index.py can recover
            print(json.dumps({'event': 'text_doc_write_failed', 'docId': doc_id, 'errorType': type(e).__name__}))
    print(json.dumps({'event': 'parsed', 'docId': doc_id, 'entries': len(entries), 'evidence': len(saved)}))
