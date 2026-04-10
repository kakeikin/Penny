import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/confirm'))

from unittest.mock import patch, MagicMock

def test_lines_balance():
    from index import lines_balance
    assert lines_balance([
        {'direction': 'DEBIT', 'amount': '100.00'},
        {'direction': 'CREDIT', 'amount': '100.00'},
    ]) is True

def test_lines_unbalanced():
    from index import lines_balance
    assert lines_balance([
        {'direction': 'DEBIT', 'amount': '100.00'},
        {'direction': 'CREDIT', 'amount': '50.00'},
    ]) is False

def test_handler_returns_400_if_unbalanced():
    from index import handler
    mock_lines_table = MagicMock()
    mock_lines_table.query.return_value = {'Items': [
        {'lineId': '000', 'direction': 'DEBIT', 'amount': '100.00'},
        {'lineId': '001', 'direction': 'CREDIT', 'amount': '50.00'},
    ]}
    mock_entries_table = MagicMock()
    mock_entries_table.get_item.return_value = {'Item': {'entryId': 'e1', 'status': 'PENDING'}}

    with patch('index.dynamodb') as mock_db:
        mock_db.Table.side_effect = lambda name: {
            'finance-journal-entries': mock_entries_table,
            'finance-journal-lines': mock_lines_table,
        }[name]
        event = {'pathParameters': {'id': 'e1'}, 'httpMethod': 'PUT', 'body': None}
        resp = handler(event, {})
    assert resp['statusCode'] == 400
