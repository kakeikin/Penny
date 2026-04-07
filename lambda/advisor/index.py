import boto3
import json
import os
from datetime import datetime
from collections import defaultdict

dynamodb = boto3.resource('dynamodb')
bedrock  = boto3.client('bedrock-runtime', region_name='us-east-1')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
}


def get_financial_context() -> str:
    """Build a text summary of the last 3 months of transactions for Claude."""
    accounts = {a['accountId']: a for a in dynamodb.Table(ACCOUNTS_TABLE).scan()['Items']}

    now = datetime.utcnow()

    # Get all confirmed entries
    entries = dynamodb.Table(ENTRIES_TABLE).scan(
        FilterExpression='#s = :confirmed',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':confirmed': 'CONFIRMED'},
    )['Items']

    # Keep only last 3 months
    m3 = (now.month - 3 - 1) % 12 + 1
    y3 = now.year if now.month > 3 else now.year - 1
    cutoff = f"{y3}-{m3:02d}-01"
    recent = [e for e in entries if e.get('date', '') >= cutoff]
    recent.sort(key=lambda e: e.get('date', ''), reverse=True)

    # Fetch lines for recent entries (batch via scan)
    entry_ids = {e['entryId'] for e in recent}
    all_lines = dynamodb.Table(LINES_TABLE).scan()['Items'] if entry_ids else []
    lines_by_entry = defaultdict(list)
    for line in all_lines:
        if line['entryId'] in entry_ids:
            lines_by_entry[line['entryId']].append(line)

    # Build transaction text (limit to 100 entries)
    lines_text = []
    for entry in recent[:100]:
        eid = entry['entryId']
        for line in lines_by_entry[eid]:
            acct = accounts.get(line['accountId'], {})
            lines_text.append(
                f"{entry['date']} | {entry['description']} | "
                f"{acct.get('type','?')} {acct.get('name', line['accountId'])} | "
                f"{line['direction']} ¥{line['amount']}"
            )

    # Monthly totals
    monthly = defaultdict(lambda: {'income': 0.0, 'expense': 0.0})
    for entry in recent:
        ym = entry.get('yearMonth', '')
        for line in lines_by_entry[entry['entryId']]:
            acct = accounts.get(line['accountId'], {})
            amt = float(line['amount'])
            if acct.get('type') == 'INCOME' and line['direction'] == 'CREDIT':
                monthly[ym]['income'] += amt
            elif acct.get('type') == 'EXPENSE' and line['direction'] == 'DEBIT':
                monthly[ym]['expense'] += amt

    monthly_text = '\n'.join(
        f"{ym}: Income ¥{v['income']:.2f}, Expenses ¥{v['expense']:.2f}, Net ¥{v['income']-v['expense']:.2f}"
        for ym, v in sorted(monthly.items(), reverse=True)
    )

    return f"""## Monthly Summary (last 3 months)
{monthly_text}

## Recent Transactions (last 100)
{chr(10).join(lines_text)}"""


def handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 200, 'headers': CORS, 'body': ''}

    try:
        body = json.loads(event.get('body', '{}'))
        question = body.get('question', '').strip()
        if not question:
            return {'statusCode': 400, 'headers': CORS,
                    'body': json.dumps({'error': 'question is required'})}

        context_text = get_financial_context()

        prompt = f"""You are a helpful personal finance advisor. The user has provided their financial data below.
Answer the user's question concisely and helpfully. Use specific numbers from the data when relevant.
If you can't answer from the data provided, say so clearly.

{context_text}

User question: {question}"""

        response = bedrock.invoke_model(
            modelId='us.anthropic.claude-sonnet-4-6',
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            })
        )
        result = json.loads(response['body'].read())
        answer = result['content'][0]['text']

        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps({'answer': answer})}

    except Exception as e:
        print(f"Error: {e}")
        return {'statusCode': 500, 'headers': CORS,
                'body': json.dumps({'error': str(e)})}
