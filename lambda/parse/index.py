import boto3
import json
import os
import base64
import hashlib
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')
APP_BUCKET     = os.environ.get('APP_BUCKET', '')


def compute_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def compute_entry_hash(entry: dict) -> str:
    """Hash based on date + total amount + first 30 chars of description."""
    date = entry.get('date', '')
    desc = entry.get('description', '')[:30].strip().lower()
    # Sum all debit amounts as the canonical amount
    # Stable with the pre-Decimal float hashes for amounts with <= 2 decimals.
    total = sum(Decimal(str(l['amount'])) for l in entry.get('lines', []) if l['direction'] == 'DEBIT')
    raw = f"{date}|{total:.2f}|{desc}"
    return hashlib.md5(raw.encode()).hexdigest()


def validate_balance(lines: list) -> bool:
    debit  = sum(Decimal(str(l['amount'])) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(Decimal(str(l['amount'])) for l in lines if l['direction'] == 'CREDIT')
    return debit == credit


_FENCED_JSON = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.S)


def _reject_constant(name: str):
    raise ValueError(f'non-finite number in model output: {name}')


def load_claude_json(raw: str) -> dict:
    """Extract the JSON object from Claude's reply and parse it with Decimal (never float).

    Tolerates a markdown fence (also on one line) and prose before/after the JSON.
    NaN/Infinity are rejected.
    """
    m = _FENCED_JSON.search(raw)
    body = m.group(1) if m else raw[raw.find('{'): raw.rfind('}') + 1] or raw
    return json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)


def get_accounts() -> list:
    table = dynamodb.Table(ACCOUNTS_TABLE)
    result = table.scan()
    return result.get('Items', [])


def parse_with_claude(file_data: bytes, media_type: str, accounts: list) -> list:
    account_list = [{'accountId': a['accountId'], 'name': a['name'], 'type': a['type']} for a in accounts]
    account_json = json.dumps(account_list, ensure_ascii=False)
    prompt = f"""You are a professional accountant. Analyze this bank statement or receipt and output double-entry bookkeeping journal entries in JSON format.

Accounts must be selected from the following list:
{account_json}

For each transaction, produce one journal entry with balanced debit and credit lines.
Every amount must be a number with exactly 2 decimal places, and debits must equal credits exactly.
If classification is uncertain, add a note.

Output ONLY valid JSON, no explanation:
{{
  "entries": [
    {{
      "date": "YYYY-MM-DD",
      "description": "...",
      "lines": [
        {{ "accountId": "...", "direction": "DEBIT", "amount": 0.00, "note": "..." }},
        {{ "accountId": "...", "direction": "CREDIT", "amount": 0.00, "note": "..." }}
      ]
    }}
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
        "max_tokens": 4096,
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

    response = bedrock.invoke_model(
        modelId='us.anthropic.claude-sonnet-4-6',
        body=body
    )
    result = json.loads(response['body'].read())
    parsed = load_claude_json(result['content'][0]['text'])
    return parsed.get('entries', [])


def save_pending_entries(entries: list, file_key: str, file_hash: str, source: str, session_id: str = None):
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    for entry in entries:
        if not validate_balance(entry['lines']):
            print(json.dumps({'event': 'entry_unbalanced_skipped', 'date': entry.get('date'),
                              'debit': str(sum(Decimal(str(l['amount'])) for l in entry['lines'] if l['direction'] == 'DEBIT')),
                              'credit': str(sum(Decimal(str(l['amount'])) for l in entry['lines'] if l['direction'] == 'CREDIT'))}))
            continue

        entry_id   = str(uuid.uuid4())
        date       = entry['date']
        year_month = date[:7]
        entry_hash = compute_entry_hash(entry)
        status     = 'DUPLICATE_SUSPECT' if is_duplicate_entry(entry_hash) else 'PENDING'

        item = {
            'entryId':     entry_id,
            'date':        date,
            'yearMonth':   year_month,
            'description': entry.get('description', ''),
            'source':      source,
            'status':      status,
            'fileKey':     file_key,
            'fileHash':    file_hash,
            'entryHash':   entry_hash,
            'createdAt':   datetime.now(timezone.utc).isoformat(),
        }
        if session_id:
            item['sessionId'] = session_id

        entries_table.put_item(Item=item)

        for i, line in enumerate(entry['lines']):
            lines_table.put_item(Item={
                'entryId':   entry_id,
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),
                'note':      line.get('note', ''),
            })


def is_duplicate(file_hash: str) -> bool:
    table = dynamodb.Table(ENTRIES_TABLE)
    result = table.scan(
        FilterExpression='fileHash = :h',
        ExpressionAttributeValues={':h': file_hash},
        Limit=1,
    )
    return len(result.get('Items', [])) > 0


def is_duplicate_entry(entry_hash: str) -> bool:
    """Check if a transaction with the same hash already exists."""
    table = dynamodb.Table(ENTRIES_TABLE)
    result = table.scan(
        FilterExpression='entryHash = :h',
        ExpressionAttributeValues={':h': entry_hash},
        Limit=1,
    )
    return len(result.get('Items', [])) > 0


def handler(event, context):
    # Presigned URL request (POST /api/upload)
    if event.get('httpMethod') == 'POST' and '/upload' in event.get('path', ''):
        body = json.loads(event.get('body', '{}'))
        filename = body.get('filename', 'upload')
        content_type = body.get('contentType', 'application/pdf')
        session_id = body.get('sessionId')  # None in owner mode

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

    # S3 event trigger
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key    = record['s3']['object']['key']

        # Extract sessionId from key: uploads/demo-{sid}/file  → sid
        # Owner uploads:             uploads/file              → None
        parts = key.split('/')
        session_id = None
        if len(parts) >= 2 and parts[1].startswith('demo-'):
            session_id = parts[1][5:]  # strip 'demo-' prefix

        file_obj  = s3_client.get_object(Bucket=bucket, Key=key)
        file_data = file_obj['Body'].read()
        file_hash = compute_md5(file_data)

        if is_duplicate(file_hash):
            print(f'Duplicate file skipped: {key}')
            continue

        ext = key.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            media_type = 'application/pdf'
            source = 'PDF'
        elif ext in ('jpg', 'jpeg'):
            media_type = 'image/jpeg'
            source = 'RECEIPT'
        else:
            media_type = 'image/png'
            source = 'RECEIPT'

        accounts = get_accounts()
        entries  = parse_with_claude(file_data, media_type, accounts)
        save_pending_entries(entries, key, file_hash, source, session_id=session_id)
        print(f'Parsed {len(entries)} entries from {key} (session={session_id})')
