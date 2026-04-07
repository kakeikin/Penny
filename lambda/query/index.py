import boto3
import json
import os
from collections import defaultdict
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def build_account_tree(accounts: list) -> list:
    by_id = {a['accountId']: dict(a, children=[]) for a in accounts}
    roots = []
    for a in by_id.values():
        parent_id = a.get('parentId')
        if parent_id and parent_id in by_id:
            by_id[parent_id]['children'].append(a)
        else:
            roots.append(a)
    return roots


def get_all_accounts() -> dict:
    table = dynamodb.Table(ACCOUNTS_TABLE)
    items = table.scan()['Items']
    return {a['accountId']: a for a in items}


def get_pending_entries() -> list:
    table = dynamodb.Table(ENTRIES_TABLE)
    return table.scan(
        FilterExpression='#s = :pending OR #s = :dup',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':pending': 'PENDING', ':dup': 'DUPLICATE_SUSPECT'},
    )['Items']


def get_confirmed_entries(start_date=None, end_date=None) -> list:
    table = dynamodb.Table(ENTRIES_TABLE)
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
    return table.scan(
        FilterExpression=fe,
        ExpressionAttributeNames=ea_names,
        ExpressionAttributeValues=ea_vals,
    )['Items']


def get_lines_for_entries(entry_ids: list) -> dict:
    if not entry_ids:
        return {}
    lines_table = dynamodb.Table(LINES_TABLE)
    result = defaultdict(list)
    # Single scan is faster than N individual queries for small-medium datasets
    response = lines_table.scan()
    for item in response.get('Items', []):
        if item['entryId'] in set(entry_ids):
            result[item['entryId']].append(item)
    # Handle pagination
    while 'LastEvaluatedKey' in response:
        response = lines_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        for item in response.get('Items', []):
            if item['entryId'] in set(entry_ids):
                result[item['entryId']].append(item)
    return result


def compute_income_statement(entries, lines_by_entry, accounts):
    income_by_account   = defaultdict(float)
    expenses_by_account = defaultdict(float)

    for entry in entries:
        for line in lines_by_entry.get(entry['entryId'], []):
            acct = accounts.get(line['accountId'], {})
            amt  = float(line['amount'])
            if acct.get('type') == 'INCOME' and line['direction'] == 'CREDIT':
                income_by_account[line['accountId']] += amt
            elif acct.get('type') == 'EXPENSE' and line['direction'] == 'DEBIT':
                expenses_by_account[line['accountId']] += amt

    total_income   = sum(income_by_account.values())
    total_expenses = sum(expenses_by_account.values())

    def enrich(d):
        return [{'accountId': k, 'name': accounts.get(k, {}).get('name', k), 'amount': v}
                for k, v in sorted(d.items())]

    return {
        'income':        enrich(income_by_account),
        'expenses':      enrich(expenses_by_account),
        'totalIncome':   total_income,
        'totalExpenses': total_expenses,
        'netIncome':     total_income - total_expenses,
    }


def compute_balance_sheet(accounts_dict, lines_by_entry, all_entries):
    balances = defaultdict(float)
    for entry in all_entries:
        for line in lines_by_entry.get(entry['entryId'], []):
            acct = accounts_dict.get(line['accountId'], {})
            amt  = float(line['amount'])
            t    = acct.get('type')
            if t == 'ASSET':
                balances[line['accountId']] += amt if line['direction'] == 'DEBIT' else -amt
            elif t in ('LIABILITY', 'EQUITY'):
                balances[line['accountId']] += amt if line['direction'] == 'CREDIT' else -amt

    assets      = {k: v for k, v in balances.items() if accounts_dict.get(k, {}).get('type') == 'ASSET'}
    liabilities = {k: v for k, v in balances.items() if accounts_dict.get(k, {}).get('type') == 'LIABILITY'}
    equity      = {k: v for k, v in balances.items() if accounts_dict.get(k, {}).get('type') == 'EQUITY'}

    def enrich(d):
        return [{'accountId': k, 'name': accounts_dict.get(k, {}).get('name', k), 'balance': v}
                for k, v in d.items()]

    return {
        'assets':           enrich(assets),
        'liabilities':      enrich(liabilities),
        'equity':           enrich(equity),
        'totalAssets':      sum(assets.values()),
        'totalLiabilities': sum(liabilities.values()),
        'totalEquity':      sum(equity.values()),
    }


def handler(event, context):
    path   = event.get('path', '')
    params = event.get('queryStringParameters') or {}

    accounts_dict = get_all_accounts()

    # GET /api/accounts
    if path.endswith('/accounts'):
        tree = build_account_tree(list(accounts_dict.values()))
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(tree, cls=DecimalEncoder)}

    # GET /api/entries
    if path.endswith('/entries'):
        status_filter = params.get('status')
        if status_filter == 'PENDING':
            entries = get_pending_entries()
        else:
            entries = get_confirmed_entries(
                start_date=params.get('startDate'),
                end_date=params.get('endDate'),
            )
        if params.get('accountId'):
            lines_table = dynamodb.Table(LINES_TABLE)
            matching_ids = set()
            for entry in entries:
                resp = lines_table.query(
                    KeyConditionExpression='entryId = :e',
                    ExpressionAttributeValues={':e': entry['entryId']},
                )
                for line in resp['Items']:
                    if line['accountId'] == params['accountId']:
                        matching_ids.add(entry['entryId'])
            entries = [e for e in entries if e['entryId'] in matching_ids]

        tag_filter = params.get('tag')
        if tag_filter:
            entries = [e for e in entries if tag_filter in e.get('tags', [])]

        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        for entry in entries:
            entry['lines'] = lines_by_entry.get(entry['entryId'], [])

        entries.sort(key=lambda e: e['date'], reverse=True)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(entries, cls=DecimalEncoder)}

    # GET /api/summary
    if path.endswith('/summary'):
        from datetime import datetime
        now = datetime.utcnow()
        month_start = f"{now.year}-{now.month:02d}-01"
        entries = get_confirmed_entries(start_date=month_start)
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        stmt = compute_income_statement(entries, lines_by_entry, accounts_dict)

        all_entries = get_confirmed_entries()
        all_lines   = get_lines_for_entries([e['entryId'] for e in all_entries])
        bs = compute_balance_sheet(accounts_dict, all_lines, all_entries)

        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({
            'thisMonth': stmt,
            'balanceSheet': bs,
            'netWorth': bs['totalAssets'] - bs['totalLiabilities'],
        }, cls=DecimalEncoder)}

    # GET /api/reports/income-statement
    if 'income-statement' in path:
        entries = get_confirmed_entries(
            start_date=params.get('startDate'),
            end_date=params.get('endDate'),
        )
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        result = compute_income_statement(entries, lines_by_entry, accounts_dict)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(result, cls=DecimalEncoder)}

    # GET /api/reports/balance-sheet
    if 'balance-sheet' in path:
        all_entries = get_confirmed_entries(end_date=params.get('asOf'))
        all_lines   = get_lines_for_entries([e['entryId'] for e in all_entries])
        result = compute_balance_sheet(accounts_dict, all_lines, all_entries)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(result, cls=DecimalEncoder)}

    # GET /api/reports/net-worth
    if 'net-worth' in path:
        from datetime import datetime
        now = datetime.utcnow()
        monthly = []
        for i in range(6):
            m = (now.month - i - 1) % 12 + 1
            y = now.year if now.month - i > 0 else now.year - 1
            end = f'{y}-{m:02d}-31'
            entries = get_confirmed_entries(end_date=end)
            lines   = get_lines_for_entries([e['entryId'] for e in entries])
            bs = compute_balance_sheet(accounts_dict, lines, entries)
            monthly.append({
                'month': f'{y}-{m:02d}',
                'netWorth': bs['totalAssets'] - bs['totalLiabilities'],
            })
        monthly.reverse()
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(monthly, cls=DecimalEncoder)}

    # GET /api/tags
    if path.endswith('/tags'):
        all_entries = dynamodb.Table(ENTRIES_TABLE).scan(
            ProjectionExpression='tags',
        )['Items']
        tag_set = set()
        for e in all_entries:
            for t in e.get('tags', []):
                tag_set.add(t)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(sorted(tag_set), cls=DecimalEncoder)}

    return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'Not found'})}
