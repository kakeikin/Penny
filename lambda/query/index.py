import boto3
from botocore.exceptions import ClientError
import json
import os
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from penny_common.textdoc import doc_id_for

dynamodb = boto3.resource('dynamodb')

ACCOUNTS_TABLE       = os.environ.get('ACCOUNTS_TABLE',       'finance-accounts')
ENTRIES_TABLE        = os.environ.get('ENTRIES_TABLE',        'finance-journal-entries')
LINES_TABLE          = os.environ.get('LINES_TABLE',          'finance-journal-lines')
BUDGETS_TABLE        = os.environ.get('BUDGETS_TABLE',        'finance-budgets')
EXCHANGE_RATES_TABLE = os.environ.get('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')
MONTHLY_CACHE_TABLE  = os.environ.get('MONTHLY_CACHE_TABLE',  'finance-monthly-cache')
APP_BUCKET           = os.environ.get('APP_BUCKET', '')
EVIDENCE_URL_TTL     = 300  # seconds
EVIDENCE_FIELDS      = ('docId', 'sourceType', 'text', 'chunkKey')   # public contract (spec §5)
EVIDENCE_CONTENT_TYPES = {'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}

s3_client = boto3.client('s3')

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


def _session_filter(session_id):
    """Return (filter_expr_fragment, attr_names, attr_values) for session isolation.

    Owner mode  (session_id is None): entries where sessionId attribute does NOT exist.
    Demo mode   (session_id set):     entries where sessionId = <sid>.
    """
    if session_id:
        return 'sessionId = :sid', {}, {':sid': session_id}
    # attribute_not_exists cannot be used in FilterExpression the same way;
    # use a workaround: filter where sessionId = '' OR attribute_not_exists.
    # Simpler: store owner entries without sessionId and filter with attribute_not_exists.
    return 'attribute_not_exists(sessionId)', {}, {}


def get_pending_entries(session_id=None) -> list:
    table = dynamodb.Table(ENTRIES_TABLE)
    sf, sf_names, sf_vals = _session_filter(session_id)
    fe = f'(#s = :pending OR #s = :dup) AND {sf}'
    ea_names = {'#s': 'status', **sf_names}
    ea_vals  = {':pending': 'PENDING', ':dup': 'DUPLICATE_SUSPECT', **sf_vals}
    kwargs = dict(
        FilterExpression=fe,
        ExpressionAttributeNames=ea_names,
        ExpressionAttributeValues=ea_vals,
    )
    items = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
    return items


def get_confirmed_entries(start_date=None, end_date=None, session_id=None) -> list:
    table = dynamodb.Table(ENTRIES_TABLE)
    sf, sf_names, sf_vals = _session_filter(session_id)
    fe = f'#s = :confirmed AND {sf}'
    ea_names = {'#s': 'status', **sf_names}
    ea_vals  = {':confirmed': 'CONFIRMED', **sf_vals}
    if start_date:
        fe += ' AND #d >= :start'
        ea_names['#d'] = 'date'
        ea_vals[':start'] = start_date
    if end_date:
        fe += ' AND #d <= :end'
        ea_names.setdefault('#d', 'date')
        ea_vals[':end'] = end_date
    items = []
    kwargs = dict(
        FilterExpression=fe,
        ExpressionAttributeNames=ea_names,
        ExpressionAttributeValues=ea_vals,
    )
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
    return items


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


def _presign_source(file_key: str):
    """Presigned GET for one uploaded file, served inline with a content type we choose."""
    content_type = EVIDENCE_CONTENT_TYPES.get(file_key.rsplit('.', 1)[-1].lower())
    if content_type is None:
        return None
    return s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': APP_BUCKET, 'Key': file_key,
                'ResponseContentType': content_type, 'ResponseContentDisposition': 'inline'},
        ExpiresIn=EVIDENCE_URL_TTL,
    )


def get_entry_evidence(entry_id: str, session_id) -> dict:
    """Return {'evidence': [...]} for an entry, or None if missing or owned by another session.

    Owner entries have no sessionId and owner requests send no X-Session-Id, so the
    comparison is None == None; a demo session can never read an owner entry or vice versa.
    """
    if not entry_id:
        return None
    entry = dynamodb.Table(ENTRIES_TABLE).get_item(Key={'entryId': entry_id}).get('Item')
    if not entry or entry.get('sessionId') != session_id:
        return None
    own_doc = doc_id_for(entry['fileHash'], entry.get('sessionId')) if entry.get('fileHash') else None
    urls = {}
    out = []
    for ev in entry.get('evidence', []):
        try:
            page = int(ev['page'])
        except (KeyError, TypeError, ValueError, ArithmeticError):
            print(json.dumps({'event': 'evidence_malformed_skipped', 'entryId': entry_id}))
            continue
        doc_id = ev.get('docId')
        if doc_id not in urls:
            # v1: evidence can only point at the entry's own uploaded file.
            own = doc_id is not None and doc_id == own_doc and entry.get('fileKey')
            urls[doc_id] = _presign_source(entry['fileKey']) if own else None
        out.append({**{k: ev.get(k) for k in EVIDENCE_FIELDS}, 'page': page, 'fileUrl': urls[doc_id]})
    return {'evidence': out}


def handler(event, context):
    path      = event.get('path', '')
    params    = event.get('queryStringParameters') or {}
    headers   = event.get('headers') or {}
    session_id = headers.get('x-session-id') or headers.get('X-Session-Id') or None

    # GET /api/entries/{id}/evidence — handled before the accounts scan below
    if path.endswith('/evidence'):
        entry_id = (event.get('pathParameters') or {}).get('id', '')
        try:
            result = get_entry_evidence(entry_id, session_id)
        except ClientError as e:
            print(json.dumps({'event': 'evidence_lookup_failed', 'errorType': e.response.get('Error', {}).get('Code')}))
            return {'statusCode': 500, 'headers': CORS, 'body': json.dumps({'error': 'internal error'})}
        if result is None:
            return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'not found'})}
        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps(result, cls=DecimalEncoder)}

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
            entries = get_pending_entries(session_id=session_id)
        else:
            entries = get_confirmed_entries(
                start_date=params.get('startDate'),
                end_date=params.get('endDate'),
                session_id=session_id,
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
        now = datetime.utcnow()
        month_start = f"{now.year}-{now.month:02d}-01"
        entries = get_confirmed_entries(start_date=month_start, session_id=session_id)
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        stmt = compute_income_statement(entries, lines_by_entry, accounts_dict)

        all_entries = get_confirmed_entries(session_id=session_id)
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
            session_id=session_id,
        )
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        result = compute_income_statement(entries, lines_by_entry, accounts_dict)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(result, cls=DecimalEncoder)}

    # GET /api/reports/balance-sheet
    if 'balance-sheet' in path:
        all_entries = get_confirmed_entries(end_date=params.get('asOf'), session_id=session_id)
        all_lines   = get_lines_for_entries([e['entryId'] for e in all_entries])
        result = compute_balance_sheet(accounts_dict, all_lines, all_entries)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(result, cls=DecimalEncoder)}

    # GET /api/reports/net-worth
    if 'net-worth' in path:
        now = datetime.utcnow()
        monthly = []
        for i in range(6):
            m = (now.month - i - 1) % 12 + 1
            y = now.year if now.month - i > 0 else now.year - 1
            end = f'{y}-{m:02d}-31'
            entries = get_confirmed_entries(end_date=end, session_id=session_id)
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
            FilterExpression='#s = :confirmed',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':confirmed': 'CONFIRMED'},
            ProjectionExpression='tags',
        )['Items']
        tag_set = set()
        for e in all_entries:
            for t in e.get('tags', []):
                tag_set.add(t)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(sorted(tag_set), cls=DecimalEncoder)}

    # GET /api/budgets
    if path.endswith('/budgets'):
        items = dynamodb.Table(BUDGETS_TABLE).scan()['Items']
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(items, cls=DecimalEncoder)}

    # GET /api/exchange-rates
    if path.endswith('/exchange-rates'):
        table = dynamodb.Table(EXCHANGE_RATES_TABLE)
        result = table.get_item(Key={'base': 'USD'})
        item = result.get('Item')
        if not item:
            return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({})}
        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({
            'base':      item['base'],
            'rates':     {k: float(v) for k, v in item['rates'].items()},
            'updatedAt': item.get('updatedAt', ''),
        }, cls=DecimalEncoder)}

    # GET /api/alerts
    if path.endswith('/alerts'):
        budgets = dynamodb.Table(BUDGETS_TABLE).scan()['Items']
        if not budgets:
            return {'statusCode': 200, 'headers': CORS, 'body': json.dumps([])}

        now = datetime.utcnow()

        # Build set of budget account IDs
        budget_acct_ids = {b['accountId'] for b in budgets}

        # Collect all relevant entries for the last 7 months
        year_months = []
        for i in range(7):
            month_offset = now.month - i
            if month_offset <= 0:
                y = now.year - 1
                m = month_offset + 12
            else:
                y = now.year
                m = month_offset
            year_months.append(f'{y}-{m:02d}')

        # Fetch all confirmed entries for those months in one scan per month
        all_relevant_entries = []
        for ym in year_months:
            entries = dynamodb.Table(ENTRIES_TABLE).scan(
                FilterExpression='yearMonth = :ym AND #s = :confirmed',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':ym': ym, ':confirmed': 'CONFIRMED'},
            )['Items']
            for e in entries:
                e['_ym'] = ym
            all_relevant_entries.extend(entries)

        # Batch-fetch all lines at once
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in all_relevant_entries])

        # Build monthly spending per account
        # monthly_spend[accountId][yearMonth] = total debit
        monthly_spend = defaultdict(lambda: defaultdict(float))
        for entry in all_relevant_entries:
            ym = entry['_ym']
            for line in lines_by_entry.get(entry['entryId'], []):
                if line['accountId'] in budget_acct_ids and line['direction'] == 'DEBIT':
                    monthly_spend[line['accountId']][ym] += float(line['amount'])

        alerts_out = []
        for budget in budgets:
            acct_id = budget['accountId']
            limit   = float(budget['monthlyLimit'])
            current_ym = year_months[0]
            current_total = monthly_spend[acct_id][current_ym]
            past_6_totals = [monthly_spend[acct_id][ym] for ym in year_months[1:7]]
            # Include zero months in average (correct behavior)
            avg = sum(past_6_totals) / len(past_6_totals) if past_6_totals else 0

            over_limit = current_total > limit
            over_avg   = avg > 0 and current_total > avg * 1.10

            if over_limit or over_avg:
                alerts_out.append({
                    'accountId':          acct_id,
                    'accountName':        budget.get('name', acct_id),
                    'monthlyLimit':       limit,
                    'currentMonthTotal':  current_total,
                    'sixMonthAverage':    round(avg, 2),
                    'overLimit':          over_limit,
                    'overAverage':        over_avg,
                    'percentOverAverage': round((current_total / avg - 1) * 100, 1) if avg > 0 else 0,
                })

        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(alerts_out, cls=DecimalEncoder)}

    return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'Not found'})}
