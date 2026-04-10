import boto3
import json
import os

dynamodb = boto3.resource('dynamodb')

ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE   = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}


def lines_balance(lines: list) -> bool:
    debit  = sum(float(l['amount']) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(float(l['amount']) for l in lines if l['direction'] == 'CREDIT')
    return abs(debit - credit) < 0.01


def handler(event, context):
    entry_id = event['pathParameters']['id']
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    entry_resp = entries_table.get_item(Key={'entryId': entry_id})
    entry = entry_resp.get('Item')
    if not entry:
        return {'statusCode': 404, 'headers': CORS,
                'body': json.dumps({'error': 'Entry not found'})}

    if entry.get('status') == 'CONFIRMED':
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps({'message': 'Already confirmed'})}

    lines_resp = lines_table.query(
        KeyConditionExpression='entryId = :e',
        ExpressionAttributeValues={':e': entry_id},
    )
    lines = lines_resp.get('Items', [])

    if not lines_balance(lines):
        return {'statusCode': 400, 'headers': CORS,
                'body': json.dumps({'error': 'Debit/credit lines do not balance'})}

    body = json.loads(event.get('body') or '{}')
    if 'lines' in body:
        for line in lines:
            lines_table.delete_item(Key={'entryId': entry_id, 'lineId': line['lineId']})
        for i, line in enumerate(body['lines']):
            lines_table.put_item(Item={
                'entryId':   entry_id,
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),
                'note':      line.get('note', ''),
            })

    if 'description' in body:
        entries_table.update_item(
            Key={'entryId': entry_id},
            UpdateExpression='SET description = :d',
            ExpressionAttributeValues={':d': body['description']},
        )

    entries_table.update_item(
        Key={'entryId': entry_id},
        UpdateExpression='SET #s = :s',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'CONFIRMED'},
    )

    return {
        'statusCode': 200,
        'headers': CORS,
        'body': json.dumps({'entryId': entry_id, 'status': 'CONFIRMED'}),
    }
