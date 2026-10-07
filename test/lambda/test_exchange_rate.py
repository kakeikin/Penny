# test/lambda/test_exchange_rate.py
import importlib
from unittest.mock import patch, MagicMock
import sys, os
import pytest

# Register the exchange-rate lambda directory on the path once.
_exchange_rate_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../lambda/exchange-rate')
)
if _exchange_rate_path not in sys.path:
    sys.path.insert(0, _exchange_rate_path)


@pytest.fixture(autouse=True)
def reload_exchange_rate_index(monkeypatch):
    """Evict any cached 'index' module and force a fresh import of the
    exchange-rate index before each test. Restore sys.path state after.
    Also sets EXCHANGERATE_API_KEY so Secrets Manager is never called.
    """
    monkeypatch.setenv('EXCHANGERATE_API_KEY', 'test-api-key')
    monkeypatch.setenv('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')

    saved_path = sys.path[:]
    saved_modules_index = sys.modules.get('index')

    sys.modules.pop('index', None)
    if _exchange_rate_path in sys.path:
        sys.path.remove(_exchange_rate_path)
    sys.path.insert(0, _exchange_rate_path)

    yield

    sys.modules.pop('index', None)
    sys.path[:] = saved_path
    if saved_modules_index is not None:
        sys.modules['index'] = saved_modules_index


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
def test_stores_rates(mock_dynamo, mock_get):
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
def test_stores_all_five_currencies(mock_dynamo, mock_get):
    mock_get.return_value.json.return_value = make_api_response()
    mock_get.return_value.raise_for_status = MagicMock()
    mock_table = MagicMock()
    mock_dynamo.Table.return_value = mock_table

    import index
    index.handler({}, {})

    item = mock_table.put_item.call_args[1]['Item']
    for currency in ['CNY', 'EUR', 'JPY', 'GBP', 'USD']:
        assert currency in item['rates']
