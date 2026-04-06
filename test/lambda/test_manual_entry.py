import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/manual-entry'))

from unittest.mock import patch, MagicMock

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
            {'accountId': '5100', 'direction': 'DEBIT', 'amount': 38.00, 'note': ''},
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
