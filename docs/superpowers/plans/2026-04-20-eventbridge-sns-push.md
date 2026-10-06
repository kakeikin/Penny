# EventBridge + SNS + Web Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated monthly report pre-computation, weekly exchange rate updates (CNY/EUR/USD/JPY/GBP), and browser Web Push notifications for budget alerts — using EventBridge, SNS, and the Web Push API.

**Architecture:** Three EventBridge rules trigger two new Lambdas on schedule. Budget alerts flow through SNS fan-out to a PushNotificationLambda that delivers Web Push to stored browser subscriptions. Exchange rates and monthly summaries are pre-computed and cached in DynamoDB for fast Dashboard reads.

**Tech Stack:** AWS CDK (TypeScript), Python 3.12 Lambda, DynamoDB, EventBridge, SNS, ExchangeRate-API (free tier), Web Push / VAPID (pywebpush library), Vanilla JS Push API

---

## File Map

**New files:**
- `lambda/monthly-report/index.py` — pre-compute last month P&L + check budgets + publish to SNS
- `lambda/exchange-rate/index.py` — fetch CNY/EUR/USD/JPY/GBP from ExchangeRate-API, write to DynamoDB
- `lambda/push-notification/index.py` — SNS subscriber, sends Web Push to all stored subscriptions
- `lambda/push-notification/requirements.txt` — `pywebpush`
- `scripts/generate-vapid-keys.py` — one-time VAPID key generation script
- `test/lambda/test_monthly_report.py`
- `test/lambda/test_exchange_rate.py`
- `test/lambda/test_push_notification.py`

**Modified files:**
- `lib/finance-stack.ts` — add 3 new DynamoDB tables, 3 new Lambdas, 2 EventBridge rules, 1 SNS topic, 3 new API endpoints
- `lambda/query/index.py` — add handlers for `/api/exchange-rates` and `/api/alerts`
- `lambda/manual-entry/index.py` — add POST `/api/push/subscribe` and DELETE `/api/push/unsubscribe`
- `frontend/pages/dashboard.js` — fetch `/api/alerts` and render budget banners
- `frontend/index.html` + `frontend/index-mobile.html` — register service worker, request Push permission
- `frontend/sw.js` (new) — service worker for Web Push

---

## Task 1: Generate VAPID Keys and Store in Secrets Manager

VAPID keys are required for Web Push. Generate once, store in Secrets Manager.

**Files:**
- Create: `scripts/generate-vapid-keys.py`

- [ ] **Step 1: Write key generation script**

```python
# scripts/generate-vapid-keys.py
from py_vapid import Vapid
import json

v = Vapid()
v.generate_keys()
print(json.dumps({
    "public_key":  v.public_key.public_bytes(
        __import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding','PublicFormat']).Encoding.X962,
        __import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding','PublicFormat']).PublicFormat.UncompressedPoint
    ).hex(),
    "private_key": v.private_key.private_bytes(
        __import__('cryptography.hazmat.primitives.serialization', fromlist=['Encoding','NoEncryption']).Encoding.PEM,
        __import__('cryptography.hazmat.primitives.serialization', fromlist=['PrivateFormat','NoEncryption']).PrivateFormat.TraditionalOpenSSL,
        __import__('cryptography.hazmat.primitives.serialization', fromlist=['NoEncryption']).NoEncryption()
    ).decode()
}, indent=2))
```

- [ ] **Step 2: Install py_vapid and run**

```bash
pip install py_vapid cryptography
python scripts/generate-vapid-keys.py
```

Save the output — you'll need both keys in Step 3.

- [ ] **Step 3: Store keys in Secrets Manager**

```bash
aws secretsmanager create-secret \
  --name finance/vapid-keys \
  --secret-string '{"public_key":"<YOUR_PUBLIC_KEY>","private_key":"<YOUR_PRIVATE_KEY>"}'
```

---

## Task 2: Add New DynamoDB Tables to CDK Stack

**Files:**
- Modify: `lib/finance-stack.ts`

- [ ] **Step 1: Add three new tables after the existing `budgetsTable` block**

```typescript
// After budgetsTable definition:

const monthlyCache = new dynamodb.Table(this, 'MonthlyCacheTable', {
  tableName: 'finance-monthly-cache',
  partitionKey: { name: 'yearMonth', type: dynamodb.AttributeType.STRING },
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});

const exchangeRates = new dynamodb.Table(this, 'ExchangeRatesTable', {
  tableName: 'finance-exchange-rates',
  partitionKey: { name: 'base', type: dynamodb.AttributeType.STRING },
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});

const pushSubscriptions = new dynamodb.Table(this, 'PushSubscriptionsTable', {
  tableName: 'finance-push-subscriptions',
  partitionKey: { name: 'endpoint', type: dynamodb.AttributeType.STRING },
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});
```

- [ ] **Step 2: Add table names to `lambdaEnv`**

```typescript
// Add to the existing lambdaEnv object:
MONTHLY_CACHE_TABLE:     monthlyCache.tableName,
EXCHANGE_RATES_TABLE:    exchangeRates.tableName,
PUSH_SUBSCRIPTIONS_TABLE: pushSubscriptions.tableName,
```

- [ ] **Step 3: Add SNS Topic**

```typescript
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';

// After tables:
const alertTopic = new sns.Topic(this, 'AlertTopic', {
  topicName: 'finance-budget-alerts',
  displayName: 'Penny Budget Alerts',
});
```

- [ ] **Step 4: Commit**

```bash
git add lib/finance-stack.ts
git commit -m "feat: add monthly-cache, exchange-rates, push-subscriptions tables + SNS topic"
```

---

## Task 3: ExchangeRateLambda

**Files:**
- Create: `lambda/exchange-rate/index.py`
- Create: `lambda/exchange-rate/requirements.txt`
- Create: `test/lambda/test_exchange_rate.py`

- [ ] **Step 1: Write requirements**

```
# lambda/exchange-rate/requirements.txt
requests==2.31.0
```

- [ ] **Step 2: Write failing test**

```python
# test/lambda/test_exchange_rate.py
import json
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/exchange-rate'))

def make_api_response():
    return {
        "result": "success",
        "base_code": "USD",
        "conversion_rates": {
            "CNY": 7.24, "EUR": 0.92, "JPY": 154.2,
            "GBP": 0.79, "USD": 1.0
        }
    }

@patch('index.requests.get')
@patch('index.dynamodb')
@patch('index.os.environ.get', return_value='finance-exchange-rates')
def test_stores_rates(mock_env, mock_dynamo, mock_get):
    mock_get.return_value.json.return_value = make_api_response()
    mock_get.return_value.raise_for_status = MagicMock()
    mock_table = MagicMock()
    mock_dynamo.Table.return_value = mock_table

    import index
    index.handler({}, {})

    call_args = mock_table.put_item.call_args[1]['Item']
    assert call_args['base'] == 'USD'
    assert float(call_args['rates']['CNY']) == 7.24
    assert 'updatedAt' in call_args

@patch('index.requests.get')
@patch('index.dynamodb')
@patch('index.os.environ.get', return_value='finance-exchange-rates')
def test_stores_all_five_currencies(mock_env, mock_dynamo, mock_get):
    mock_get.return_value.json.return_value = make_api_response()
    mock_get.return_value.raise_for_status = MagicMock()
    mock_table = MagicMock()
    mock_dynamo.Table.return_value = mock_table

    import index
    index.handler({}, {})

    item = mock_table.put_item.call_args[1]['Item']
    for currency in ['CNY', 'EUR', 'JPY', 'GBP', 'USD']:
        assert currency in item['rates']
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp
pip install requests -q
pytest test/lambda/test_exchange_rate.py -v
```
Expected: `ModuleNotFoundError: No module named 'index'`

- [ ] **Step 4: Write implementation**

```python
# lambda/exchange-rate/index.py
import boto3
import json
import os
import requests
from datetime import datetime, timezone

dynamodb = boto3.resource('dynamodb')
secretsmanager = boto3.client('secretsmanager')

EXCHANGE_RATES_TABLE = os.environ.get('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')
CURRENCIES = ['CNY', 'EUR', 'JPY', 'GBP', 'USD']


def get_api_key() -> str:
    secret = secretsmanager.get_secret_value(SecretId='finance/exchangerate-api-key')
    return json.loads(secret['SecretString'])['api_key']


def handler(event, context):
    api_key = get_api_key()
    url = f'https://v6.exchangerate-api.com/v6/{api_key}/latest/USD'
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    rates = {c: data['conversion_rates'][c] for c in CURRENCIES if c in data['conversion_rates']}

    table = dynamodb.Table(EXCHANGE_RATES_TABLE)
    table.put_item(Item={
        'base': 'USD',
        'rates': {k: str(v) for k, v in rates.items()},
        'updatedAt': datetime.now(timezone.utc).isoformat(),
    })

    print(f"Exchange rates updated: {rates}")
    return {'statusCode': 200, 'body': json.dumps(rates)}
```

- [ ] **Step 5: Run tests**

```bash
pytest test/lambda/test_exchange_rate.py -v
```
Expected: 2 PASSED

- [ ] **Step 6: Store API key in Secrets Manager**

Sign up at https://exchangerate-api.com (free tier), get API key, then:

```bash
aws secretsmanager create-secret \
  --name finance/exchangerate-api-key \
  --secret-string '{"api_key":"<YOUR_API_KEY>"}'
```

- [ ] **Step 7: Commit**

```bash
git add lambda/exchange-rate/ test/lambda/test_exchange_rate.py
git commit -m "feat: add ExchangeRateLambda for CNY/EUR/USD/JPY/GBP weekly updates"
```

---

## Task 4: MonthlyReportLambda

**Files:**
- Create: `lambda/monthly-report/index.py`
- Create: `test/lambda/test_monthly_report.py`

- [ ] **Step 1: Write failing test**

```python
# test/lambda/test_monthly_report.py
import json
from unittest.mock import patch, MagicMock
from decimal import Decimal
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/monthly-report'))

def mock_entries():
    return [
        {'entryId': 'e1', 'date': '2026-03-15', 'yearMonth': '2026-03', 'status': 'CONFIRMED'},
    ]

def mock_lines():
    return {
        'e1': [
            {'accountId': '5100', 'direction': 'DEBIT',  'amount': Decimal('150')},
            {'accountId': '1100', 'direction': 'CREDIT', 'amount': Decimal('150')},
        ]
    }

def mock_budgets():
    return [{'accountId': '5100', 'monthlyLimit': Decimal('100')}]

@patch('index.dynamodb')
@patch('index.boto3.client')
def test_detects_over_budget(mock_client, mock_dynamo):
    entries_table  = MagicMock()
    lines_table    = MagicMock()
    cache_table    = MagicMock()
    budgets_table  = MagicMock()

    def table_side_effect(name):
        return {
            'finance-journal-entries': entries_table,
            'finance-journal-lines':   lines_table,
            'finance-monthly-cache':   cache_table,
            'finance-budgets':         budgets_table,
        }[name]

    mock_dynamo.Table.side_effect = table_side_effect
    entries_table.query.return_value = {'Items': mock_entries()}
    lines_table.query.return_value   = {'Items': mock_lines()['e1']}
    budgets_table.scan.return_value  = {'Items': mock_budgets()}
    cache_table.put_item = MagicMock()

    sns_client = MagicMock()
    mock_client.return_value = sns_client

    import index
    with patch.object(index, 'ENTRIES_TABLE',  'finance-journal-entries'), \
         patch.object(index, 'LINES_TABLE',    'finance-journal-lines'), \
         patch.object(index, 'MONTHLY_CACHE_TABLE', 'finance-monthly-cache'), \
         patch.object(index, 'BUDGETS_TABLE',  'finance-budgets'):
        index.handler({}, {})

    assert cache_table.put_item.called
    sns_client.publish.assert_called_once()
    payload = json.loads(sns_client.publish.call_args[1]['Message'])
    assert any(a['accountId'] == '5100' for a in payload['alerts'])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest test/lambda/test_monthly_report.py -v
```
Expected: `ModuleNotFoundError: No module named 'index'`

- [ ] **Step 3: Write implementation**

```python
# lambda/monthly-report/index.py
import boto3
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')

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
    table = dynamodb.Table(ENTRIES_TABLE)
    result = table.query(
        IndexName='date-index',
        KeyConditionExpression='yearMonth = :ym',
        FilterExpression='#s = :confirmed',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':ym': year_month, ':confirmed': 'CONFIRMED'},
    )
    return result.get('Items', [])


def get_lines(entry_ids: list) -> dict:
    lines_table = dynamodb.Table(LINES_TABLE)
    lines_by_entry = defaultdict(list)
    for eid in entry_ids:
        result = lines_table.query(
            KeyConditionExpression='entryId = :eid',
            ExpressionAttributeValues={':eid': eid},
        )
        lines_by_entry[eid] = result.get('Items', [])
    return lines_by_entry


def compute_expense_by_account(entries, lines_by_entry) -> dict:
    totals = defaultdict(Decimal)
    for entry in entries:
        for line in lines_by_entry.get(entry['entryId'], []):
            if line['direction'] == 'DEBIT':
                account_id = line['accountId']
                if account_id.startswith('5'):  # Expense accounts
                    totals[account_id] += Decimal(str(line['amount']))
    return dict(totals)


def check_budgets(expense_by_account: dict) -> list:
    budgets_table = dynamodb.Table(BUDGETS_TABLE)
    budgets = budgets_table.scan().get('Items', [])
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
    year_month = get_last_year_month()
    entries = get_entries_for_month(year_month)
    entry_ids = [e['entryId'] for e in entries]
    lines_by_entry = get_lines(entry_ids)

    expense_by_account = compute_expense_by_account(entries, lines_by_entry)
    total_expense = float(sum(expense_by_account.values()))

    # Write to cache
    cache_table = dynamodb.Table(MONTHLY_CACHE_TABLE)
    cache_table.put_item(Item={
        'yearMonth':        year_month,
        'expenseByAccount': {k: str(v) for k, v in expense_by_account.items()},
        'totalExpense':     str(total_expense),
        'entryCount':       len(entries),
        'computedAt':       datetime.now(timezone.utc).isoformat(),
    })
    print(f"Cached monthly report for {year_month}: {total_expense} total expense")

    # Check budgets and publish alerts
    alerts = check_budgets(expense_by_account)
    if alerts and SNS_TOPIC_ARN:
        sns = boto3.client('sns')
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Penny Budget Alert — {year_month}",
            Message=json.dumps({'yearMonth': year_month, 'alerts': alerts}, cls=DecimalEncoder),
        )
        print(f"Published {len(alerts)} budget alerts to SNS")

    return {'statusCode': 200, 'body': json.dumps({'yearMonth': year_month, 'alerts': alerts})}
```

- [ ] **Step 4: Run tests**

```bash
pytest test/lambda/test_monthly_report.py -v
```
Expected: 1 PASSED

- [ ] **Step 5: Commit**

```bash
git add lambda/monthly-report/ test/lambda/test_monthly_report.py
git commit -m "feat: add MonthlyReportLambda with P&L caching and budget alert publishing"
```

---

## Task 5: PushNotificationLambda

**Files:**
- Create: `lambda/push-notification/index.py`
- Create: `lambda/push-notification/requirements.txt`
- Create: `test/lambda/test_push_notification.py`

- [ ] **Step 1: Write requirements**

```
# lambda/push-notification/requirements.txt
pywebpush==2.0.0
```

- [ ] **Step 2: Write failing test**

```python
# test/lambda/test_push_notification.py
import json
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/push-notification'))

def make_sns_event(alerts):
    return {
        'Records': [{
            'Sns': {
                'Message': json.dumps({
                    'yearMonth': '2026-03',
                    'alerts': alerts,
                })
            }
        }]
    }

@patch('index.webpush')
@patch('index.dynamodb')
@patch('index.get_vapid_keys')
def test_sends_push_to_all_subscriptions(mock_vapid, mock_dynamo, mock_webpush):
    mock_vapid.return_value = ('pub_key', 'priv_key')
    sub_table = MagicMock()
    mock_dynamo.Table.return_value = sub_table
    sub_table.scan.return_value = {'Items': [
        {'endpoint': 'https://push.example.com/abc', 'subscription': json.dumps({
            'endpoint': 'https://push.example.com/abc',
            'keys': {'p256dh': 'key1', 'auth': 'auth1'}
        })},
    ]}

    import index
    index.handler(make_sns_event([{'accountId': '5100', 'spent': 150, 'limit': 100, 'overage': 50}]), {})

    assert mock_webpush.called

@patch('index.webpush')
@patch('index.dynamodb')
@patch('index.get_vapid_keys')
def test_skips_on_empty_subscriptions(mock_vapid, mock_dynamo, mock_webpush):
    mock_vapid.return_value = ('pub_key', 'priv_key')
    sub_table = MagicMock()
    mock_dynamo.Table.return_value = sub_table
    sub_table.scan.return_value = {'Items': []}

    import index
    index.handler(make_sns_event([{'accountId': '5100', 'spent': 150, 'limit': 100, 'overage': 50}]), {})

    mock_webpush.assert_not_called()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest test/lambda/test_push_notification.py -v
```
Expected: `ModuleNotFoundError: No module named 'index'`

- [ ] **Step 4: Write implementation**

```python
# lambda/push-notification/index.py
import boto3
import json
import os
from pywebpush import webpush, WebPushException

dynamodb      = boto3.resource('dynamodb')
secretsclient = boto3.client('secretsmanager')

PUSH_SUBSCRIPTIONS_TABLE = os.environ.get('PUSH_SUBSCRIPTIONS_TABLE', 'finance-push-subscriptions')
VAPID_CLAIMS_EMAIL       = os.environ.get('VAPID_CLAIMS_EMAIL', 'mailto:penny@example.com')


def get_vapid_keys():
    secret = secretsclient.get_secret_value(SecretId='finance/vapid-keys')
    data = json.loads(secret['SecretString'])
    return data['public_key'], data['private_key']


def handler(event, context):
    for record in event['Records']:
        message = json.loads(record['Sns']['Message'])
        alerts = message.get('alerts', [])
        year_month = message.get('yearMonth', '')

        if not alerts:
            return

        public_key, private_key = get_vapid_keys()

        table = dynamodb.Table(PUSH_SUBSCRIPTIONS_TABLE)
        subscriptions = table.scan().get('Items', [])

        if not subscriptions:
            print("No push subscriptions found")
            return

        payload = json.dumps({
            'title': f'Penny Budget Alert — {year_month}',
            'body': f'{len(alerts)} account(s) exceeded monthly limit',
            'alerts': alerts,
        })

        for item in subscriptions:
            sub = json.loads(item['subscription'])
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=private_key,
                    vapid_claims={'sub': VAPID_CLAIMS_EMAIL},
                )
                print(f"Push sent to {sub['endpoint'][:50]}...")
            except WebPushException as e:
                print(f"Push failed for {sub['endpoint'][:50]}: {e}")
                if e.response and e.response.status_code in (404, 410):
                    # Subscription expired — remove it
                    table.delete_item(Key={'endpoint': item['endpoint']})
```

- [ ] **Step 5: Run tests**

```bash
pip install pywebpush -q
pytest test/lambda/test_push_notification.py -v
```
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add lambda/push-notification/ test/lambda/test_push_notification.py
git commit -m "feat: add PushNotificationLambda (SNS → Web Push via pywebpush)"
```

---

## Task 6: Wire Everything in CDK Stack

**Files:**
- Modify: `lib/finance-stack.ts`

- [ ] **Step 1: Add Lambda imports at top of file**

```typescript
// Add to existing imports:
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
```

- [ ] **Step 2: Add three new Lambda functions after `advisorFn`**

```typescript
// ExchangeRateLambda
const exchangeRateFn = new lambda.Function(this, 'ExchangeRateLambda', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'index.handler',
  code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/exchange-rate')),
  layers: [pyLayer],
  environment: { ...lambdaEnv },
  timeout: cdk.Duration.seconds(30),
  memorySize: 256,
});
exchangeRateFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ['secretsmanager:GetSecretValue'],
  resources: ['*'],
}));
exchangeRates.grantReadWriteData(exchangeRateFn);

// MonthlyReportLambda
const monthlyReportFn = new lambda.Function(this, 'MonthlyReportLambda', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'index.handler',
  code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/monthly-report')),
  layers: [pyLayer],
  environment: { ...lambdaEnv, SNS_TOPIC_ARN: alertTopic.topicArn },
  timeout: cdk.Duration.seconds(60),
  memorySize: 512,
});
entriesTable.grantReadData(monthlyReportFn);
linesTable.grantReadData(monthlyReportFn);
budgetsTable.grantReadData(monthlyReportFn);
monthlyCache.grantReadWriteData(monthlyReportFn);
alertTopic.grantPublish(monthlyReportFn);

// PushNotificationLambda
const pushNotificationFn = new lambda.Function(this, 'PushNotificationLambda', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'index.handler',
  code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/push-notification')),
  layers: [pyLayer],
  environment: { ...lambdaEnv, VAPID_CLAIMS_EMAIL: 'mailto:jia.jiax@northeastern.edu' },
  timeout: cdk.Duration.seconds(60),
  memorySize: 256,
});
pushSubscriptions.grantReadWriteData(pushNotificationFn);
pushNotificationFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ['secretsmanager:GetSecretValue'],
  resources: ['*'],
}));
alertTopic.addSubscription(new snsSubscriptions.LambdaSubscription(pushNotificationFn));
```

- [ ] **Step 3: Add EventBridge rules**

```typescript
// Weekly Monday 00:00 UTC — exchange rate update
new events.Rule(this, 'WeeklyExchangeRate', {
  schedule: events.Schedule.cron({ minute: '0', hour: '0', weekDay: 'MON' }),
  targets: [new targets.LambdaFunction(exchangeRateFn)],
});

// Monthly 1st at 01:00 UTC — monthly report + budget check
new events.Rule(this, 'MonthlyReport', {
  schedule: events.Schedule.cron({ minute: '0', hour: '1', day: '1' }),
  targets: [new targets.LambdaFunction(monthlyReportFn)],
});
```

- [ ] **Step 4: Add API endpoints for exchange rates + push subscription**

```typescript
// /api/exchange-rates  (handled by queryFn)
apiRoot.addResource('exchange-rates').addMethod('GET', new apigw.LambdaIntegration(queryFn));
exchangeRates.grantReadData(queryFn);
monthlyCache.grantReadData(queryFn);

// /api/push/subscribe + unsubscribe  (handled by manualEntryFn)
const push = apiRoot.addResource('push');
const subscribe = push.addResource('subscribe');
subscribe.addMethod('POST',   new apigw.LambdaIntegration(manualEntryFn));
subscribe.addMethod('DELETE', new apigw.LambdaIntegration(manualEntryFn));
pushSubscriptions.grantReadWriteData(manualEntryFn);

// /api/alerts (handled by queryFn)
apiRoot.addResource('alerts').addMethod('GET', new apigw.LambdaIntegration(queryFn));
budgetsTable.grantReadData(queryFn);
```

- [ ] **Step 5: Commit**

```bash
git add lib/finance-stack.ts
git commit -m "feat: wire EventBridge rules, SNS topic, and new Lambda functions in CDK stack"
```

---

## Task 7: Add /api/alerts and /api/exchange-rates to QueryLambda

**Files:**
- Modify: `lambda/query/index.py`

- [ ] **Step 1: Add exchange rates handler function**

Add after existing helper functions:

```python
EXCHANGE_RATES_TABLE = os.environ.get('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')
MONTHLY_CACHE_TABLE  = os.environ.get('MONTHLY_CACHE_TABLE',  'finance-monthly-cache')

def get_exchange_rates() -> dict:
    table = dynamodb.Table(EXCHANGE_RATES_TABLE)
    result = table.get_item(Key={'base': 'USD'})
    item = result.get('Item')
    if not item:
        return {}
    return {
        'base':      item['base'],
        'rates':     {k: float(v) for k, v in item['rates'].items()},
        'updatedAt': item.get('updatedAt', ''),
    }


def get_current_month_expense_by_account() -> dict:
    """Compute current month expense per account (for live alert check)."""
    now = datetime.now()
    year_month = f"{now.year}-{now.month:02d}"
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    result = entries_table.query(
        IndexName='date-index',
        KeyConditionExpression='yearMonth = :ym',
        FilterExpression='#s = :confirmed',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':ym': year_month, ':confirmed': 'CONFIRMED'},
    )
    entries = result.get('Items', [])

    expense_by_account = defaultdict(float)
    for entry in entries:
        lines_result = lines_table.query(
            KeyConditionExpression='entryId = :eid',
            ExpressionAttributeValues={':eid': entry['entryId']},
        )
        for line in lines_result.get('Items', []):
            if line['direction'] == 'DEBIT' and line['accountId'].startswith('5'):
                expense_by_account[line['accountId']] += float(line['amount'])

    return dict(expense_by_account)


def get_alerts() -> list:
    """Return active budget alerts: over-limit or >10% above 6-month average."""
    budgets_table = dynamodb.Table(BUDGETS_TABLE)
    budgets = budgets_table.scan().get('Items', [])
    expense = get_current_month_expense_by_account()
    alerts = []

    # Check monthly limits
    for budget in budgets:
        account_id = budget['accountId']
        limit = float(budget.get('monthlyLimit', 0))
        spent = expense.get(account_id, 0.0)
        if limit > 0 and spent > limit:
            alerts.append({
                'type':      'over_limit',
                'accountId': account_id,
                'spent':     spent,
                'limit':     limit,
                'overage':   round(spent - limit, 2),
            })

    # Check 6-month average anomaly
    now = datetime.now()
    monthly_totals = []
    cache_table = dynamodb.Table(MONTHLY_CACHE_TABLE)
    for i in range(1, 7):
        month = now.month - i
        year  = now.year
        while month <= 0:
            month += 12
            year  -= 1
        ym = f"{year}-{month:02d}"
        item = cache_table.get_item(Key={'yearMonth': ym}).get('Item')
        if item:
            by_account = {k: float(v) for k, v in item.get('expenseByAccount', {}).items()}
            monthly_totals.append(by_account)

    if len(monthly_totals) >= 3:
        all_accounts = set()
        for m in monthly_totals:
            all_accounts.update(m.keys())
        for account_id in all_accounts:
            values = [m.get(account_id, 0.0) for m in monthly_totals]
            avg = sum(values) / len(values)
            current = expense.get(account_id, 0.0)
            if avg > 0 and current > avg * 1.10:
                # Only add if not already flagged as over_limit
                if not any(a['accountId'] == account_id and a['type'] == 'over_limit' for a in alerts):
                    alerts.append({
                        'type':      'anomaly',
                        'accountId': account_id,
                        'spent':     current,
                        'average':   round(avg, 2),
                        'pctAbove':  round((current - avg) / avg * 100, 1),
                    })

    return alerts
```

- [ ] **Step 2: Add routes in handler function**

Find the `handler` function's routing section and add:

```python
# In handler, add these routes alongside existing ones:
if path == '/api/exchange-rates' and method == 'GET':
    return {
        'statusCode': 200,
        'headers': CORS,
        'body': json.dumps(get_exchange_rates(), cls=DecimalEncoder),
    }

if path == '/api/alerts' and method == 'GET':
    return {
        'statusCode': 200,
        'headers': CORS,
        'body': json.dumps(get_alerts(), cls=DecimalEncoder),
    }
```

- [ ] **Step 3: Commit**

```bash
git add lambda/query/index.py
git commit -m "feat: add /api/alerts and /api/exchange-rates to QueryLambda"
```

---

## Task 8: Add Push Subscribe/Unsubscribe to ManualEntryLambda

**Files:**
- Modify: `lambda/manual-entry/index.py`

- [ ] **Step 1: Add push subscription routes in handler**

```python
PUSH_SUBSCRIPTIONS_TABLE = os.environ.get('PUSH_SUBSCRIPTIONS_TABLE', 'finance-push-subscriptions')

# In handler, add alongside existing routes:
if path == '/api/push/subscribe' and method == 'POST':
    sub_json = body.get('subscription')
    if not sub_json:
        return {'statusCode': 400, 'headers': CORS, 'body': json.dumps({'error': 'subscription required'})}
    endpoint = body.get('endpoint') or json.loads(sub_json).get('endpoint', '')
    table = dynamodb.Table(PUSH_SUBSCRIPTIONS_TABLE)
    table.put_item(Item={
        'endpoint':     endpoint,
        'subscription': sub_json,
        'createdAt':    datetime.now(timezone.utc).isoformat(),
    })
    return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'ok': True})}

if path == '/api/push/subscribe' and method == 'DELETE':
    endpoint = body.get('endpoint', '')
    if endpoint:
        dynamodb.Table(PUSH_SUBSCRIPTIONS_TABLE).delete_item(Key={'endpoint': endpoint})
    return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'ok': True})}
```

- [ ] **Step 2: Commit**

```bash
git add lambda/manual-entry/index.py
git commit -m "feat: add push subscribe/unsubscribe endpoints to ManualEntryLambda"
```

---

## Task 9: Service Worker + Frontend Push Registration

**Files:**
- Create: `frontend/sw.js`
- Modify: `frontend/index.html`
- Modify: `frontend/index-mobile.html`

- [ ] **Step 1: Create service worker**

```javascript
// frontend/sw.js
self.addEventListener('push', function(event) {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'Penny Alert';
  const body  = data.body  || 'You have a budget notification.';
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon:  '/icon-192.png',
      badge: '/icon-192.png',
      data:  { alerts: data.alerts || [] },
    })
  );
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
```

- [ ] **Step 2: Add push registration script to both index.html and index-mobile.html**

Add before closing `</body>` in both files:

```html
<script>
  // VAPID public key — replace with your actual key from Secrets Manager
  const VAPID_PUBLIC_KEY = '<YOUR_VAPID_PUBLIC_KEY>';

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
  }

  async function registerPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    const reg = await navigator.serviceWorker.register('/sw.js');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return;

    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
    });

    await fetch('/api/push/subscribe', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: JSON.stringify(sub), endpoint: sub.endpoint }),
    });
    console.log('Push notifications registered');
  }

  // Register after page loads
  window.addEventListener('load', () => registerPush().catch(console.warn));
</script>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/sw.js frontend/index.html frontend/index-mobile.html
git commit -m "feat: add service worker and push notification registration to frontend"
```

---

## Task 10: Dashboard Budget Alert Banners

**Files:**
- Modify: `frontend/pages/dashboard.js`

- [ ] **Step 1: Add alert banner rendering at top of dashboard**

Find the dashboard render function and add at the start before existing content:

```javascript
// At the start of the dashboard render function, before other content:
async function renderAlerts(container) {
  try {
    const res = await apiFetch('/api/alerts');
    const alerts = await res.json();
    if (!alerts || alerts.length === 0) return;

    const banner = document.createElement('div');
    banner.style.cssText = 'margin-bottom:16px;';
    banner.innerHTML = alerts.map(a => {
      if (a.type === 'over_limit') {
        return `<div style="background:#FEF2F2;border-left:4px solid #EF4444;border-radius:8px;padding:12px 16px;margin-bottom:8px;font-size:13px;color:#991B1B;">
          ⚠️ <strong>Account ${a.accountId}</strong> exceeded monthly limit —
          spent <strong>$${a.spent.toFixed(2)}</strong> vs limit <strong>$${a.limit.toFixed(2)}</strong>
          (over by $${a.overage.toFixed(2)})
        </div>`;
      }
      if (a.type === 'anomaly') {
        return `<div style="background:#FFFBEB;border-left:4px solid #F59E0B;border-radius:8px;padding:12px 16px;margin-bottom:8px;font-size:13px;color:#92400E;">
          📈 <strong>Account ${a.accountId}</strong> is ${a.pctAbove}% above 6-month average —
          spent <strong>$${a.spent.toFixed(2)}</strong> vs avg <strong>$${a.average.toFixed(2)}</strong>
        </div>`;
      }
      return '';
    }).join('');
    container.prepend(banner);
  } catch (e) {
    console.warn('Could not load alerts:', e);
  }
}
```

Call `renderAlerts(app)` at the start of the dashboard render function.

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/dashboard.js
git commit -m "feat: add budget alert banners to Dashboard (over-limit + anomaly detection)"
```

---

## Task 11: Deploy

- [ ] **Step 1: Build Lambda layer with new dependencies**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp
mkdir -p lambda/layer/python
pip install pywebpush requests -t lambda/layer/python/
```

- [ ] **Step 2: Deploy CDK stack**

```bash
npx cdk deploy --require-approval never
```

- [ ] **Step 3: Deploy frontend files to S3**

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name FinanceStack --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 cp frontend/sw.js         s3://$BUCKET/sw.js
aws s3 cp frontend/index.html    s3://$BUCKET/index.html
aws s3 cp frontend/index-mobile.html s3://$BUCKET/index-mobile.html
aws s3 cp frontend/pages/dashboard.js s3://$BUCKET/pages/dashboard.js
```

- [ ] **Step 4: Invalidate CloudFront**

```bash
DIST_ID=$(aws cloudfront list-distributions --query "DistributionList.Items[0].Id" --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

- [ ] **Step 5: Trigger exchange rate Lambda manually to populate initial data**

```bash
aws lambda invoke --function-name $(aws lambda list-functions --query "Functions[?contains(FunctionName,'ExchangeRate')].FunctionName" --output text) /tmp/out.json
cat /tmp/out.json
```

- [ ] **Step 6: Final smoke test**
  - Open Dashboard — should show no alerts (or real ones if over budget)
  - Accept push notification permission in browser
  - Check `finance-push-subscriptions` DynamoDB table has an entry
  - Check `finance-exchange-rates` DynamoDB table has USD rates

- [ ] **Step 7: Commit**

```bash
git add .
git commit -m "chore: deploy EventBridge + SNS + Web Push feature"
```
