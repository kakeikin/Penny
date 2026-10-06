# test/lambda/test_monthly_report.py
import json
import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal
import sys, os

_monthly_report_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../lambda/monthly-report')
)
if _monthly_report_path not in sys.path:
    sys.path.insert(0, _monthly_report_path)


@pytest.fixture(autouse=True)
def reload_monthly_report_index(monkeypatch):
    """Isolate module state between tests."""
    monkeypatch.setenv('ENTRIES_TABLE',       'finance-journal-entries')
    monkeypatch.setenv('LINES_TABLE',         'finance-journal-lines')
    monkeypatch.setenv('MONTHLY_CACHE_TABLE', 'finance-monthly-cache')
    monkeypatch.setenv('BUDGETS_TABLE',       'finance-budgets')
    monkeypatch.setenv('SNS_TOPIC_ARN',       'arn:aws:sns:us-east-1:123456789012:finance-budget-alerts')

    saved_path = sys.path[:]
    saved_module = sys.modules.get('index')

    sys.modules.pop('index', None)
    if _monthly_report_path in sys.path:
        sys.path.remove(_monthly_report_path)
    sys.path.insert(0, _monthly_report_path)

    yield

    sys.modules.pop('index', None)
    sys.path[:] = saved_path
    if saved_module is not None:
        sys.modules['index'] = saved_module


def _make_table_router(entries_table, lines_table, cache_table, budgets_table):
    return {
        'finance-journal-entries': entries_table,
        'finance-journal-lines':   lines_table,
        'finance-monthly-cache':   cache_table,
        'finance-budgets':         budgets_table,
    }


def _over_budget_tables():
    """Return (entries_table, lines_table, cache_table, budgets_table) for over-budget scenario."""
    entries_table = MagicMock()
    lines_table   = MagicMock()
    cache_table   = MagicMock()
    budgets_table = MagicMock()

    entries_table.query.return_value = {'Items': [
        {'entryId': 'e1', 'date': '2026-03-15', 'yearMonth': '2026-03', 'status': 'CONFIRMED'},
    ]}
    lines_table.query.return_value = {'Items': [
        {'accountId': '5100', 'direction': 'DEBIT',  'amount': Decimal('150')},
        {'accountId': '1100', 'direction': 'CREDIT', 'amount': Decimal('150')},
    ]}
    budgets_table.scan.return_value = {'Items': [
        {'accountId': '5100', 'monthlyLimit': Decimal('100')}
    ]}
    return entries_table, lines_table, cache_table, budgets_table


@patch('index.dynamodb')
def test_detects_over_budget(mock_dynamo):
    entries_table, lines_table, cache_table, budgets_table = _over_budget_tables()
    router = _make_table_router(entries_table, lines_table, cache_table, budgets_table)
    mock_dynamo.Table.side_effect = lambda name: router[name]

    import index
    mock_sns = MagicMock()
    with patch.object(index, 'sns_client', mock_sns):
        index.handler({}, {})

    assert cache_table.put_item.called
    mock_sns.publish.assert_called_once()
    payload = json.loads(mock_sns.publish.call_args[1]['Message'])
    assert any(a['accountId'] == '5100' for a in payload['alerts'])


@patch('index.dynamodb')
def test_no_alert_when_under_budget(mock_dynamo):
    entries_table = MagicMock()
    lines_table   = MagicMock()
    cache_table   = MagicMock()
    budgets_table = MagicMock()

    router = _make_table_router(entries_table, lines_table, cache_table, budgets_table)
    mock_dynamo.Table.side_effect = lambda name: router[name]

    entries_table.query.return_value = {'Items': [
        {'entryId': 'e1', 'date': '2026-03-15', 'yearMonth': '2026-03', 'status': 'CONFIRMED'},
    ]}
    lines_table.query.return_value = {'Items': [
        {'accountId': '5100', 'direction': 'DEBIT',  'amount': Decimal('50')},
        {'accountId': '1100', 'direction': 'CREDIT', 'amount': Decimal('50')},
    ]}
    budgets_table.scan.return_value = {'Items': [
        {'accountId': '5100', 'monthlyLimit': Decimal('100')}
    ]}

    import index
    mock_sns = MagicMock()
    with patch.object(index, 'sns_client', mock_sns):
        index.handler({}, {})

    assert cache_table.put_item.called
    mock_sns.publish.assert_not_called()


@patch('index.dynamodb')
def test_no_sns_when_topic_arn_empty(mock_dynamo):
    """Verify SNS is suppressed when SNS_TOPIC_ARN is not configured."""
    entries_table, lines_table, cache_table, budgets_table = _over_budget_tables()
    router = _make_table_router(entries_table, lines_table, cache_table, budgets_table)
    mock_dynamo.Table.side_effect = lambda name: router[name]

    import index
    mock_sns = MagicMock()
    with patch.object(index, 'sns_client', mock_sns), \
         patch.object(index, 'SNS_TOPIC_ARN', ''):
        index.handler({}, {})

    assert cache_table.put_item.called
    mock_sns.publish.assert_not_called()
