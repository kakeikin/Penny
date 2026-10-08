import sys, os, json
import pytest
from unittest.mock import patch, MagicMock

_confirm_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../lambda/confirm')
)
if _confirm_path not in sys.path:
    sys.path.insert(0, _confirm_path)


@pytest.fixture(autouse=True)
def isolate_confirm_index():
    saved_path = sys.path[:]
    saved_module = sys.modules.get('index')

    sys.modules.pop('index', None)
    if _confirm_path in sys.path:
        sys.path.remove(_confirm_path)
    sys.path.insert(0, _confirm_path)

    yield

    sys.modules.pop('index', None)
    sys.path[:] = saved_path
    if saved_module is not None:
        sys.modules['index'] = saved_module


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


BALANCED = [{'lineId': '000', 'accountId': 'dining', 'direction': 'DEBIT', 'amount': '10.00'},
            {'lineId': '001', 'accountId': 'card', 'direction': 'CREDIT', 'amount': '10.00'}]


def _confirm(entry, body=None, headers=None, stored=BALANCED):
    from index import handler
    entries, lines = MagicMock(), MagicMock()
    entries.get_item.return_value = {'Item': entry} if entry else {}
    lines.query.return_value = {'Items': stored}
    with patch('index.dynamodb') as mock_db:
        mock_db.Table.side_effect = lambda name: {'finance-journal-entries': entries,
                                                  'finance-journal-lines': lines}[name]
        event = {'pathParameters': {'id': 'e1'}, 'httpMethod': 'PUT', 'headers': headers,
                 'body': json.dumps(body) if body is not None else None}
        resp = handler(event, {})
    return resp, entries, lines


def test_lines_balance_is_exact_decimal():
    from index import lines_balance
    assert lines_balance([{'direction': 'DEBIT', 'amount': '100.00'},
                          {'direction': 'CREDIT', 'amount': '99.995'}]) is False


def test_confirm_marks_entry_confirmed_and_clears_fx_status():
    resp, entries, _ = _confirm({'entryId': 'e1', 'status': 'PENDING'})
    assert resp['statusCode'] == 200
    update = entries.update_item.call_args.kwargs
    assert update['UpdateExpression'] == 'SET #s = :s REMOVE fxStatus'
    assert update['ExpressionAttributeValues'] == {':s': 'CONFIRMED'}


def test_client_lines_must_balance_before_they_replace_stored_lines():
    body = {'lines': [{'accountId': 'dining', 'direction': 'DEBIT', 'amount': '100.00'},
                      {'accountId': 'card', 'direction': 'CREDIT', 'amount': '50.00'}]}
    resp, entries, lines = _confirm({'entryId': 'e1', 'status': 'PENDING'}, body)
    assert resp['statusCode'] == 400
    lines.put_item.assert_not_called()
    lines.delete_item.assert_not_called()
    entries.update_item.assert_not_called()


@pytest.mark.parametrize('bad', [
    'not a list',
    [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '1.00'}],                       # one line
    [{'accountId': '', 'direction': 'DEBIT', 'amount': '1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}],
    [{'accountId': 'a', 'direction': 'UP', 'amount': '1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}],
    [{'accountId': 'a', 'direction': 'DEBIT', 'amount': 'abc'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}],
    [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '-1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '-1.00'}],
])
def test_malformed_client_lines_are_rejected(bad):
    resp, _, lines = _confirm({'entryId': 'e1', 'status': 'PENDING'}, {'lines': bad})
    assert resp['statusCode'] == 400
    lines.put_item.assert_not_called()


def test_client_lines_keep_original_currency_fields():
    body = {'lines': [{'accountId': 'dining', 'direction': 'DEBIT', 'amount': '10.00', 'originalCurrency': 'CNY',
                       'originalAmount': '72.50', 'exchangeRate': '7.25', 'entryId': 'x', 'lineId': '009'},
                      {'accountId': 'card', 'direction': 'CREDIT', 'amount': '10.00'}]}
    resp, _, lines = _confirm({'entryId': 'e1', 'status': 'PENDING'}, body)
    assert resp['statusCode'] == 200
    written = [c.kwargs['Item'] for c in lines.put_item.call_args_list]
    assert written[0] == {'entryId': 'e1', 'lineId': '000', 'accountId': 'dining', 'direction': 'DEBIT',
                          'amount': '10.00', 'note': '', 'originalCurrency': 'CNY', 'originalAmount': '72.50',
                          'exchangeRate': '7.25'}
    assert 'originalCurrency' not in written[1]


def test_unconverted_entry_needs_explicit_acknowledgement():
    entry = {'entryId': 'e1', 'status': 'PENDING', 'fxStatus': 'unconverted'}
    resp, entries, _ = _confirm(entry)
    assert resp['statusCode'] == 409
    entries.update_item.assert_not_called()
    resp, _, _ = _confirm(entry, {'acknowledgeUnconverted': True})
    assert resp['statusCode'] == 200


def test_sessions_can_only_confirm_their_own_entries():
    assert _confirm({'entryId': 'e1', 'status': 'PENDING'}, headers={'X-Session-Id': 'demo1'})[0]['statusCode'] == 404
    assert _confirm({'entryId': 'e1', 'status': 'PENDING', 'sessionId': 'demo1'})[0]['statusCode'] == 404
    assert _confirm({'entryId': 'e1', 'status': 'PENDING', 'sessionId': 'demo1'},
                    headers={'x-session-id': 'demo1'})[0]['statusCode'] == 200


def test_missing_entry_and_bad_requests():
    assert _confirm(None)[0]['statusCode'] == 404
    assert _confirm({'entryId': 'e1'}, headers={'X-Session-Id': 'bad id!'})[0]['statusCode'] == 400
    from index import handler
    resp = handler({'pathParameters': {'id': 'e1'}, 'body': '[1, 2]'}, {})
    assert resp['statusCode'] == 400
