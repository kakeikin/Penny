import boto3
import json
import os
import csv
import io
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')

ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')
ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')

CORS = {'Access-Control-Allow-Origin': '*'}


def handler(event, context):
    params = event.get('queryStringParameters') or {}
    start_date = params.get('startDate')
    end_date   = params.get('endDate')

    entries_table  = dynamodb.Table(ENTRIES_TABLE)
    lines_table    = dynamodb.Table(LINES_TABLE)
    accounts_table = dynamodb.Table(ACCOUNTS_TABLE)

    # Fetch account names
    accounts = {a['accountId']: a['name']
                for a in accounts_table.scan()['Items']}

    # Fetch confirmed entries
    fe = '#s = :confirmed'
    ea_names = {'#s': 'status'}
    ea_vals  = {':confirmed': 'CONFIRMED'}
    if start_date:
        fe += ' AND #d >= :start'
        ea_names['#d'] = 'date'
        ea_vals[':start'] = start_date
    if end_date:
        fe += ' AND #d <= :end'
        ea_names.setdefault('#d', 'date')
        ea_vals[':end'] = end_date

    entries = entries_table.scan(
        FilterExpression=fe,
        ExpressionAttributeNames=ea_names,
        ExpressionAttributeValues=ea_vals,
    )['Items']
    entries.sort(key=lambda e: e['date'])

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Description', 'Source', 'Account', 'Direction', 'Amount', 'Note', 'Tags'])

    for entry in entries:
        lines = lines_table.query(
            KeyConditionExpression='entryId = :e',
            ExpressionAttributeValues={':e': entry['entryId']},
        )['Items']
        for line in lines:
            writer.writerow([
                entry['date'],
                entry['description'],
                entry['source'],
                accounts.get(line['accountId'], line['accountId']),
                line['direction'],
                float(line['amount']) if not isinstance(line['amount'], Decimal) else float(line['amount']),
                line.get('note', ''),
                ','.join(entry.get('tags', [])),
            ])

    return {
        'statusCode': 200,
        'headers': {
            **CORS,
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename="transactions.csv"',
        },
        'body': output.getvalue(),
    }
