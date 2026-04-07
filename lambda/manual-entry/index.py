import boto3
import json
import os
import uuid
from datetime import datetime, timezone

dynamodb = boto3.resource('dynamodb')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}


def lines_balance(lines):
    debit  = sum(float(l['amount']) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(float(l['amount']) for l in lines if l['direction'] == 'CREDIT')
    return abs(debit - credit) < 0.01


def parse_tags(value):
    """Accept a list or comma-separated string of tags, return a clean list."""
    if isinstance(value, str):
        return [t.strip() for t in value.split(',') if t.strip()]
    return [t.strip() for t in (value or []) if t.strip()]


def handler(event, context):
    method = event.get('httpMethod', '')
    path   = event.get('path', '')
    params = event.get('pathParameters') or {}
    body   = json.loads(event.get('body') or '{}')

    entries_table  = dynamodb.Table(ENTRIES_TABLE)
    lines_table    = dynamodb.Table(LINES_TABLE)
    accounts_table = dynamodb.Table(ACCOUNTS_TABLE)

    # POST /api/entries (manual entry)
    if method == 'POST' and path.endswith('/entries'):
        lines = body.get('lines', [])
        if not lines_balance(lines):
            return {'statusCode': 400, 'headers': CORS,
                    'body': json.dumps({'error': 'Debit and credit lines must balance'})}

        entry_id   = str(uuid.uuid4())
        date       = body['date']
        year_month = date[:7]

        tags = parse_tags(body.get('tags', []))

        entries_table.put_item(Item={
            'entryId':     entry_id,
            'date':        date,
            'yearMonth':   year_month,
            'description': body.get('description', ''),
            'source':      'MANUAL',
            'status':      'CONFIRMED',
            'tags':        tags,
            'fileKey':     None,
            'fileHash':    None,
            'createdAt':   datetime.now(timezone.utc).isoformat(),
        })

        for i, line in enumerate(lines):
            item = {
                'entryId':   entry_id,
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),
                'note':      line.get('note', ''),
            }
            if line.get('originalCurrency') and line.get('originalAmount'):
                item['originalCurrency'] = line['originalCurrency']
                item['originalAmount']   = str(line['originalAmount'])
                item['exchangeRate']     = str(line.get('exchangeRate', '1'))
            lines_table.put_item(Item=item)

        return {'statusCode': 201, 'headers': CORS, 'body': json.dumps({'entryId': entry_id})}

    # PUT /api/entries/{id}
    if method == 'PUT' and 'id' in params and 'confirm' not in path:
        entry_id = params['id']
        update_expr = []
        expr_vals   = {}
        expr_names  = {}

        if 'description' in body:
            update_expr.append('description = :d')
            expr_vals[':d'] = body['description']
        if 'date' in body:
            update_expr.append('#dt = :dt, yearMonth = :ym')
            expr_names['#dt'] = 'date'
            expr_vals[':dt'] = body['date']
            expr_vals[':ym'] = body['date'][:7]
        if 'tags' in body:
            update_expr.append('tags = :tags')
            expr_vals[':tags'] = parse_tags(body['tags'])

        if update_expr:
            entries_table.update_item(
                Key={'entryId': entry_id},
                UpdateExpression='SET ' + ', '.join(update_expr),
                ExpressionAttributeNames=expr_names if expr_names else None,
                ExpressionAttributeValues=expr_vals,
            )

        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'updated': True})}

    # DELETE /api/entries/{id}
    if method == 'DELETE' and 'id' in params:
        entry_id = params['id']
        lines_resp = lines_table.query(
            KeyConditionExpression='entryId = :e',
            ExpressionAttributeValues={':e': entry_id},
        )
        for line in lines_resp.get('Items', []):
            lines_table.delete_item(Key={'entryId': entry_id, 'lineId': line['lineId']})
        entries_table.delete_item(Key={'entryId': entry_id})
        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'deleted': True})}

    # POST /api/accounts
    if method == 'POST' and path.endswith('/accounts'):
        account_id = body.get('accountId', str(uuid.uuid4())[:8])
        accounts_table.put_item(Item={
            'accountId': account_id,
            'name':      body['name'],
            'type':      body['type'],
            'parentId':  body.get('parentId'),
            'isSystem':  False,
            'currency':  body.get('currency', 'CNY'),
        })
        return {'statusCode': 201, 'headers': CORS, 'body': json.dumps({'accountId': account_id})}

    return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'Not found'})}
