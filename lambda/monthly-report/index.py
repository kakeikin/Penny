# lambda/monthly-report/index.py
import boto3
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
sns_client = boto3.client('sns')

ENTRIES_TABLE       = os.environ.get('ENTRIES_TABLE',       'finance-journal-entries')
LINES_TABLE         = os.environ.get('LINES_TABLE',         'finance-journal-lines')
MONTHLY_CACHE_TABLE = os.environ.get('MONTHLY_CACHE_TABLE', 'finance-monthly-cache')
BUDGETS_TABLE       = os.environ.get('BUDGETS_TABLE',       'finance-budgets')
SNS_TOPIC_ARN       = os.environ.get('SNS_TOPIC_ARN',       '')


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        return float(o) if isinstance(o, Decimal) else super().default(o)


def get_last_year_month() -> str:
    now = datetime.now(timezone.utc)
    if now.month == 1:
        return f"{now.year - 1}-12"
    return f"{now.year}-{now.month - 1:02d}"


def get_entries_for_month(year_month: str) -> list:
    """Query all CONFIRMED entries for year_month, handling DynamoDB pagination."""
    table = dynamodb.Table(ENTRIES_TABLE)
    items = []
    kwargs = {
        'IndexName': 'date-index',
        'KeyConditionExpression': 'yearMonth = :ym',
        'FilterExpression': '#s = :confirmed',
        'ExpressionAttributeNames': {'#s': 'status'},
        'ExpressionAttributeValues': {':ym': year_month, ':confirmed': 'CONFIRMED'},
    }
    while True:
        result = table.query(**kwargs)
        items.extend(result.get('Items', []))
        last = result.get('LastEvaluatedKey')
        if not last:
            break
        kwargs['ExclusiveStartKey'] = last
    return items


def get_lines(entry_ids: list) -> dict:
    """Fetch all journal lines for each entry, handling DynamoDB pagination."""
    lines_table = dynamodb.Table(LINES_TABLE)
    lines_by_entry = defaultdict(list)
    for eid in entry_ids:
        kwargs = {
            'KeyConditionExpression': 'entryId = :eid',
            'ExpressionAttributeValues': {':eid': eid},
        }
        while True:
            result = lines_table.query(**kwargs)
            lines_by_entry[eid].extend(result.get('Items', []))
            last = result.get('LastEvaluatedKey')
            if not last:
                break
            kwargs['ExclusiveStartKey'] = last
    return lines_by_entry


def compute_expense_by_account(entries: list, lines_by_entry: dict) -> dict:
    """Sum DEBIT amounts for expense accounts (accountId starts with '5')."""
    totals = defaultdict(Decimal)
    for entry in entries:
        for line in lines_by_entry.get(entry['entryId'], []):
            if line['direction'] == 'DEBIT' and str(line['accountId']).startswith('5'):
                totals[line['accountId']] += Decimal(str(line['amount']))
    return dict(totals)


def check_budgets(expense_by_account: dict) -> list:
    """Scan budgets and return alerts for accounts that exceeded their monthly limit."""
    budgets_table = dynamodb.Table(BUDGETS_TABLE)
    budgets: list = []
    kwargs: dict = {}
    while True:
        result = budgets_table.scan(**kwargs)
        budgets.extend(result.get('Items', []))
        last = result.get('LastEvaluatedKey')
        if not last:
            break
        kwargs['ExclusiveStartKey'] = last

    alerts = []
    for budget in budgets:
        account_id = budget['accountId']
        limit = Decimal(str(budget.get('monthlyLimit', 0)))
        spent = expense_by_account.get(account_id, Decimal('0'))
        if limit > 0 and spent > limit:
            alerts.append({
                'accountId': account_id,
                'spent': float(spent),
                'limit': float(limit),
                'overage': float(spent - limit),
            })
    return alerts


def handler(event, context):
    try:
        year_month = get_last_year_month()
        entries = get_entries_for_month(year_month)
        entry_ids = [e['entryId'] for e in entries]
        lines_by_entry = get_lines(entry_ids)

        expense_by_account = compute_expense_by_account(entries, lines_by_entry)
        # Keep as Decimal to preserve precision; convert to str for DynamoDB storage
        total_expense = sum(expense_by_account.values()) if expense_by_account else Decimal('0')

        # Cache monthly summary
        cache_table = dynamodb.Table(MONTHLY_CACHE_TABLE)
        cache_table.put_item(Item={
            'yearMonth':        year_month,
            'expenseByAccount': {k: str(v) for k, v in expense_by_account.items()},
            'totalExpense':     str(total_expense),
            'entryCount':       len(entries),
            'computedAt':       datetime.now(timezone.utc).isoformat(),
        })
        print(f"Cached monthly report for {year_month}: ${total_expense} total expense")

        # Check budgets and publish alerts to SNS
        alerts = check_budgets(expense_by_account)
        if alerts and SNS_TOPIC_ARN:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"Penny Budget Alert — {year_month}",
                Message=json.dumps({'yearMonth': year_month, 'alerts': alerts}, cls=DecimalEncoder),
            )
            print(f"Published {len(alerts)} budget alert(s) to SNS")

        return {'statusCode': 200, 'body': json.dumps({'yearMonth': year_month, 'alerts': alerts})}

    except Exception as exc:
        print(f"ERROR in MonthlyReportLambda: {exc}")
        raise
