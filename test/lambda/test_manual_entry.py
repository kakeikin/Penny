import sys, os, json
import pytest
from unittest.mock import patch, MagicMock

_manual_entry_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../lambda/manual-entry')
)
if _manual_entry_path not in sys.path:
    sys.path.insert(0, _manual_entry_path)


@pytest.fixture(autouse=True)
def isolate_manual_entry_index(monkeypatch):
    monkeypatch.setenv('ACCOUNTS_TABLE',           'finance-accounts')
    monkeypatch.setenv('ENTRIES_TABLE',            'finance-journal-entries')
    monkeypatch.setenv('LINES_TABLE',              'finance-journal-lines')
    monkeypatch.setenv('BUDGETS_TABLE',            'finance-budgets')
    monkeypatch.setenv('PUSH_SUBSCRIPTIONS_TABLE', 'finance-push-subscriptions')

    saved_path = sys.path[:]
    saved_module = sys.modules.get('index')

    sys.modules.pop('index', None)
    if _manual_entry_path in sys.path:
        sys.path.remove(_manual_entry_path)
    sys.path.insert(0, _manual_entry_path)

    yield

    sys.modules.pop('index', None)
    sys.path[:] = saved_path
    if saved_module is not None:
        sys.modules['index'] = saved_module


def _event(method, path, body=None, path_params=None):
    return {
        'httpMethod': method,
        'path': path,
        'pathParameters': path_params or {},
        'body': json.dumps(body) if body else None,
    }


def test_post_entry_returns_201():
    from index import handler
    body = {
        'date': '2026-04-01',
        'description': 'Coffee',
        'lines': [
            {'accountId': '5100', 'direction': 'DEBIT',  'amount': 38.00, 'note': ''},
            {'accountId': '1100', 'direction': 'CREDIT', 'amount': 38.00, 'note': ''},
        ],
    }
    with patch('index.dynamodb') as mock_db:
        mock_db.Table.return_value.put_item.return_value = {}
        resp = handler(_event('POST', '/api/entries', body), {})
    assert resp['statusCode'] == 201
    data = json.loads(resp['body'])
    assert 'entryId' in data


def test_post_entry_rejects_unbalanced():
    from index import handler
    body = {
        'date': '2026-04-01',
        'description': 'Bad entry',
        'lines': [
            {'accountId': '5100', 'direction': 'DEBIT', 'amount': 100.00, 'note': ''},
        ],
    }
    with patch('index.dynamodb'):
        resp = handler(_event('POST', '/api/entries', body), {})
    assert resp['statusCode'] == 400
