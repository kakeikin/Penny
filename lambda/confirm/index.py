import boto3
import json
import os
from decimal import Decimal, InvalidOperation

from penny_common.session import session_from_headers

dynamodb = boto3.resource('dynamodb')

ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE   = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}
DIRECTIONS = ('DEBIT', 'CREDIT')
MAX_AMOUNT = Decimal('1000000000000')
FX_FIELDS = ('originalCurrency', 'originalAmount', 'exchangeRate')   # same as ParseLambda/ManualEntry


def _response(status, body):
    return {'statusCode': status, 'headers': CORS, 'body': json.dumps(body)}


def lines_balance(lines: list) -> bool:
    """Exact Decimal comparison: 100.00 vs 99.99 never passes."""
    debit  = sum((Decimal(str(l['amount'])) for l in lines if l['direction'] == 'DEBIT'), Decimal('0'))
    credit = sum((Decimal(str(l['amount'])) for l in lines if l['direction'] == 'CREDIT'), Decimal('0'))
    return debit == credit


def clean_lines(raw) -> list:
    """Validated copies of client-supplied lines, or None if any line is malformed."""
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    out = []
    for line in raw:
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
            return None
        note = line.get('note', '')
        item = {'accountId': account, 'direction': direction, 'amount': amount,
                'note': note if isinstance(note, str) else ''}
        if all(line.get(k) not in (None, '') for k in FX_FIELDS):
            item.update({k: str(line[k]) for k in FX_FIELDS})
        out.append(item)
    return out


def handler(event, context):
    entry_id = (event.get('pathParameters') or {}).get('id', '')
    try:
        session_id = session_from_headers(event.get('headers'))
        body = json.loads(event.get('body') or '{}', parse_float=Decimal)
    except (ValueError, TypeError):
        return _response(400, {'error': 'invalid request'})
    if not isinstance(body, dict):
        return _response(400, {'error': 'invalid request'})

    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    entry = entries_table.get_item(Key={'entryId': entry_id}).get('Item') if entry_id else None
    # Owner entries have no sessionId and owner requests send none: a session can only
    # confirm its own entries.
    if not entry or entry.get('sessionId') != session_id:
        return _response(404, {'error': 'Entry not found'})

    if entry.get('status') == 'CONFIRMED':
        return _response(200, {'message': 'Already confirmed'})

    if entry.get('fxStatus') == 'unconverted' and body.get('acknowledgeUnconverted') is not True:
        return _response(409, {'error': 'Amounts were not converted to USD. Review them, then confirm again.',
                               'fxStatus': 'unconverted'})

    stored = lines_table.query(
        KeyConditionExpression='entryId = :e',
        ExpressionAttributeValues={':e': entry_id},
    ).get('Items', [])

    new_lines = None
    if 'lines' in body:
        new_lines = clean_lines(body['lines'])
        if new_lines is None:
            return _response(400, {'error': 'Invalid lines'})
    if not lines_balance(new_lines if new_lines is not None else stored):
        return _response(400, {'error': 'Debit/credit lines do not balance'})

    if new_lines is not None:
        for line in stored:
            lines_table.delete_item(Key={'entryId': entry_id, 'lineId': line['lineId']})
        for i, line in enumerate(new_lines):
            lines_table.put_item(Item={**line, 'entryId': entry_id, 'lineId': f'{i:03d}',
                                       'amount': str(line['amount'])})

    if isinstance(body.get('description'), str):
        entries_table.update_item(
            Key={'entryId': entry_id},
            UpdateExpression='SET description = :d',
            ExpressionAttributeValues={':d': body['description']},
        )

    entries_table.update_item(
        Key={'entryId': entry_id},
        UpdateExpression='SET #s = :s REMOVE fxStatus',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'CONFIRMED'},
    )

    return _response(200, {'entryId': entry_id, 'status': 'CONFIRMED'})
