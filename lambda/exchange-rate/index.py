# lambda/exchange-rate/index.py
import boto3
import json
import os
import requests
from datetime import datetime, timezone

dynamodb = boto3.resource('dynamodb')
secretsmanager = boto3.client('secretsmanager')

# Table name resolved once at cold-start; falls back to default for local testing.
EXCHANGE_RATES_TABLE = os.environ.get('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')
CURRENCIES = ['CNY', 'EUR', 'JPY', 'GBP', 'USD']


def get_api_key() -> str:
    """Return the ExchangeRate-API key.

    In production the key is stored in Secrets Manager as:
      { "api_key": "<key>" }
    at path  finance/exchangerate-api-key.

    Set the EXCHANGERATE_API_KEY env var to skip Secrets Manager (tests / local).
    """
    key = os.environ.get('EXCHANGERATE_API_KEY')
    if key:
        return key
    try:
        secret = secretsmanager.get_secret_value(SecretId='finance/exchangerate-api-key')
        return json.loads(secret['SecretString'])['api_key']
    except Exception as exc:
        raise RuntimeError(f"Failed to retrieve ExchangeRate-API key from Secrets Manager: {exc}") from exc


def handler(event, context):
    try:
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
    except Exception as exc:
        print(f"ERROR updating exchange rates: {exc}")
        raise
