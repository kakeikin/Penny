# lambda/push-notification/index.py
import boto3
import json
import os
from pywebpush import webpush, WebPushException

dynamodb      = boto3.resource('dynamodb')
secretsclient = boto3.client('secretsmanager')

PUSH_SUBSCRIPTIONS_TABLE = os.environ.get('PUSH_SUBSCRIPTIONS_TABLE', 'finance-push-subscriptions')
VAPID_CLAIMS_EMAIL       = os.environ.get('VAPID_CLAIMS_EMAIL', '')

if not VAPID_CLAIMS_EMAIL:
    # Fail fast at cold start rather than silently sending invalid VAPID claims
    raise ValueError("VAPID_CLAIMS_EMAIL env var is required (e.g. mailto:you@example.com)")


def get_vapid_keys():
    """Retrieve VAPID keys from Secrets Manager.

    Secret at 'finance/vapid-keys' must have format: {"public_key": "...", "private_key": "..."}
    """
    secret = secretsclient.get_secret_value(SecretId='finance/vapid-keys')
    data = json.loads(secret['SecretString'])
    return data['public_key'], data['private_key']


def handler(event, context):
    for record in event['Records']:
        message = json.loads(record['Sns']['Message'])
        alerts = message.get('alerts', [])
        year_month = message.get('yearMonth', '')

        if not alerts:
            print("No alerts in SNS message, skipping record")
            continue  # process remaining records, don't abort the whole batch

        try:
            _public_key, private_key = get_vapid_keys()
        except Exception as exc:
            print(f"ERROR retrieving VAPID keys: {exc}")
            raise

        table = dynamodb.Table(PUSH_SUBSCRIPTIONS_TABLE)
        # Paginate scan in case there are many subscriptions
        subscriptions = []
        kwargs: dict = {}
        while True:
            result = table.scan(**kwargs)
            subscriptions.extend(result.get('Items', []))
            last = result.get('LastEvaluatedKey')
            if not last:
                break
            kwargs['ExclusiveStartKey'] = last

        if not subscriptions:
            print("No push subscriptions found, skipping record")
            continue  # process remaining records

        payload = json.dumps({
            'title':  f'Penny Budget Alert — {year_month}',
            'body':   f'{len(alerts)} account(s) exceeded monthly limit',
            'alerts': alerts,
        })

        for item in subscriptions:
            sub_json = item.get('subscription')
            if not sub_json:
                print(f"Skipping item with no subscription field: {item.get('endpoint', '?')}")
                continue
            sub = json.loads(sub_json)
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=private_key,
                    vapid_claims={'sub': VAPID_CLAIMS_EMAIL},
                )
                print(f"Push sent to {sub['endpoint'][:50]}...")
            except WebPushException as exc:
                print(f"Push failed for {sub['endpoint'][:50]}: {exc}")
                if exc.response and exc.response.status_code in (404, 410):
                    # Subscription expired or invalid — remove it
                    table.delete_item(Key={'endpoint': item['endpoint']})
