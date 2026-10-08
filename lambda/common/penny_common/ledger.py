"""Read-only journal queries scoped to one session, with Decimal money.

Entries are read through the date-index GSI (PK yearMonth, SK date), one Query per month,
and lines by their primary key - no full-table scans on the request path.
"""
from collections import defaultdict
from decimal import Decimal

from boto3.dynamodb.conditions import Attr, Key

DATE_INDEX = 'date-index'
MAX_MONTHS = 36              # callers validate ranges; this is a hard backstop
LINE_QUERY_LIMIT = 50        # above this many entries one paginated scan is cheaper than N queries


def session_condition(session_id):
    """Owner entries have no sessionId; demo entries carry theirs."""
    return Attr('sessionId').eq(session_id) if session_id else Attr('sessionId').not_exists()


def months_between(start_date: str, end_date: str) -> list:
    """['YYYY-MM', ...] covering both dates (inclusive)."""
    y, m = int(start_date[:4]), int(start_date[5:7])
    end_y, end_m = int(end_date[:4]), int(end_date[5:7])
    months = []
    while (y, m) <= (end_y, end_m):
        months.append(f'{y:04d}-{m:02d}')
        if len(months) > MAX_MONTHS:
            raise ValueError(f'date range longer than {MAX_MONTHS} months')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def _paginate(call, **kwargs) -> list:
    items = []
    while True:
        resp = call(**kwargs)
        items += resp.get('Items', [])
        if 'LastEvaluatedKey' not in resp:
            return items
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']


def confirmed_entries(entries_table, session_id, start_date: str, end_date: str) -> list:
    """CONFIRMED entries of one session with start_date <= date <= end_date (ISO strings)."""
    items = []
    for ym in months_between(start_date, end_date):
        items += _paginate(
            entries_table.query, IndexName=DATE_INDEX,
            KeyConditionExpression=Key('yearMonth').eq(ym) & Key('date').between(start_date, end_date),
            FilterExpression=Attr('status').eq('CONFIRMED') & session_condition(session_id),
        )
    return items


def lines_for(lines_table, entry_ids) -> dict:
    """{entryId: [lines]}: Query by key for a few entries, one scan for many."""
    wanted, result = set(entry_ids), defaultdict(list)
    if len(wanted) <= LINE_QUERY_LIMIT:
        for entry_id in sorted(wanted):
            result[entry_id] = _paginate(lines_table.query, KeyConditionExpression=Key('entryId').eq(entry_id))
        return result
    for item in _paginate(lines_table.scan):
        if item['entryId'] in wanted:
            result[item['entryId']].append(item)
    return result


def money(value) -> Decimal:
    """Exact Decimal from a stored amount (string/Decimal/int; floats via str)."""
    return Decimal(str(value))


def entry_amount(lines) -> Decimal:
    """Gross amount of an entry = its debit side."""
    return sum((money(l['amount']) for l in lines if l['direction'] == 'DEBIT'), Decimal('0'))


def account_net(line, account_type: str) -> Decimal:
    """Signed effect of one line on an INCOME (credit - debit) or EXPENSE (debit - credit) account."""
    amount = money(line['amount'])
    if account_type == 'EXPENSE':
        return amount if line['direction'] == 'DEBIT' else -amount
    if account_type == 'INCOME':
        return amount if line['direction'] == 'CREDIT' else -amount
    return Decimal('0')


def flows(lines, accounts) -> tuple:
    """(income, expense) contributed by one entry's lines, net of refunds and reversals."""
    income = expense = Decimal('0')
    for line in lines:
        kind = accounts.get(line['accountId'], {}).get('type')
        if kind == 'INCOME':
            income += account_net(line, kind)
        elif kind == 'EXPENSE':
            expense += account_net(line, kind)
    return income, expense
