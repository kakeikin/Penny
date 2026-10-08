from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

D = Decimal


def _rates_item(age=timedelta(days=1)):
    return {'Item': {'base': 'USD', 'rates': {'CNY': '7.25', 'USD': '1'},
                     'updatedAt': (datetime.now(timezone.utc) - age).isoformat()}}


@pytest.fixture
def parse(lambda_module, monkeypatch):
    index = lambda_module('parse')
    tables = {name: MagicMock(name=name) for name in
              (index.ENTRIES_TABLE, index.LINES_TABLE, index.EXCHANGE_RATES_TABLE)}
    tables[index.EXCHANGE_RATES_TABLE].get_item.return_value = _rates_item()
    db = MagicMock()
    db.Table.side_effect = lambda name: tables[name]
    monkeypatch.setattr(index, 'dynamodb', db)
    monkeypatch.setattr(index, 'is_duplicate_entry', lambda *a: False)
    index.tables = tables
    return index


def _entry(currency, amount='72.50'):
    e = {'date': '2026-03-18', 'description': 'Cafe',
         'lines': [{'accountId': 'dining', 'direction': 'DEBIT', 'amount': D(amount)},
                   {'accountId': 'card', 'direction': 'CREDIT', 'amount': D(amount)}]}
    if currency is not None:
        e['currency'] = currency
    return e


def _written(parse):
    entries = [c.kwargs['Item'] for c in parse.tables[parse.ENTRIES_TABLE].put_item.call_args_list]
    lines = [c.kwargs['Item'] for c in parse.tables[parse.LINES_TABLE].put_item.call_args_list]
    return entries, lines


def test_prompt_asks_for_currency_without_conversion(parse, monkeypatch):
    captured = {}

    def fake_invoke(modelId, body):
        captured['body'] = body
        return {'body': MagicMock(read=lambda: b'{"content": [{"text": "{\\"entries\\": []}"}]}')}

    monkeypatch.setattr(parse, 'bedrock', MagicMock(invoke_model=fake_invoke))
    parse.parse_with_claude(b'%PDF', 'application/pdf', [], [])
    assert '\\"currency\\": \\"USD\\"' in captured['body']
    assert 'never convert them' in captured['body']


@pytest.mark.parametrize('currency', [None, 'USD', 'usd', ''])
def test_usd_or_unreported_currency_is_stored_as_is(parse, currency):
    parse.save_pending_entries([_entry(currency, '120.00')], 'uploads/k.pdf', 'h', 'PDF')
    entries, lines = _written(parse)
    assert [l['amount'] for l in lines] == ['120.00', '120.00']
    assert 'originalCurrency' not in lines[0] and 'fxStatus' not in entries[0]
    parse.tables[parse.EXCHANGE_RATES_TABLE].get_item.assert_not_called()    # USD needs no rates


def test_missing_currency_is_logged_without_data(parse, capsys):
    parse.save_pending_entries([_entry(None, '120.00')], 'uploads/k.pdf', 'h', 'PDF')
    out = capsys.readouterr().out
    assert 'currency_missing_assumed_usd' in out and '120.00' not in out


def test_cny_entry_is_converted_and_keeps_original(parse):
    parse.save_pending_entries([_entry('CNY')], 'uploads/k.pdf', 'h', 'PDF')
    entries, lines = _written(parse)
    assert [l['amount'] for l in lines] == ['10.00', '10.00']
    assert lines[0]['originalCurrency'] == 'CNY'
    assert lines[0]['originalAmount'] == '72.50'
    assert lines[0]['exchangeRate'] == '7.25'
    assert 'fxStatus' not in entries[0] and 'printedCurrency' not in entries[0]


def test_usd_entry_hash_format_is_unchanged(parse):
    parse.save_pending_entries([_entry('USD', '10.00')], 'uploads/k.pdf', 'h', 'PDF')
    entries, _ = _written(parse)
    assert entries[0]['entryHash'] == parse.compute_entry_hash(_entry(None, '10.00'))


def test_foreign_entry_hash_uses_printed_amount_and_currency(parse):
    parse.save_pending_entries([_entry('CNY')], 'uploads/k.pdf', 'h', 'PDF')
    entries, _ = _written(parse)
    assert entries[0]['entryHash'] == parse.compute_entry_hash(_entry(None), 'CNY')
    assert entries[0]['entryHash'] != parse.compute_entry_hash(_entry(None, '10.00'))   # rate-independent
    assert entries[0]['entryHash'] != parse.compute_entry_hash(_entry(None))             # not a USD 72.50


def test_rates_are_read_once_per_document(parse):
    parse.save_pending_entries([_entry('CNY'), _entry('CNY', '14.50')], 'uploads/k.pdf', 'h', 'PDF')
    assert parse.tables[parse.EXCHANGE_RATES_TABLE].get_item.call_count == 1


@pytest.mark.parametrize('currency,printed', [('HKD', 'HKD'), ('JPY', 'JPY'), ('dollars', 'XXX'), (12, 'XXX')])
def test_unconvertible_entry_is_booked_as_printed_and_flagged(parse, currency, printed, capsys):
    parse.save_pending_entries([_entry(currency)], 'uploads/k.pdf', 'h', 'PDF')
    entries, lines = _written(parse)
    assert entries[0]['fxStatus'] == 'unconverted'
    assert entries[0]['printedCurrency'] == printed
    assert [l['amount'] for l in lines] == ['72.50', '72.50']
    assert all('originalCurrency' not in l for l in lines)
    out = capsys.readouterr().out
    assert 'fx_unconverted' in out and '72.50' not in out and 'HKD' not in out


def test_rates_table_failure_leaves_entries_unconverted_and_is_read_once(parse, capsys):
    parse.tables[parse.EXCHANGE_RATES_TABLE].get_item.side_effect = ClientError(
        {'Error': {'Code': 'ProvisionedThroughputExceededException', 'Message': 'x'}}, 'GetItem')
    parse.save_pending_entries([_entry('CNY'), _entry('CNY', '14.50')], 'uploads/k.pdf', 'h', 'PDF')
    entries, _ = _written(parse)
    assert [e['fxStatus'] for e in entries] == ['unconverted', 'unconverted']
    assert parse.tables[parse.EXCHANGE_RATES_TABLE].get_item.call_count == 1
    assert 'fx_rates_unavailable' in capsys.readouterr().out


def test_stale_rates_leave_entry_unconverted(parse):
    parse.tables[parse.EXCHANGE_RATES_TABLE].get_item.return_value = _rates_item(age=timedelta(days=30))
    parse.save_pending_entries([_entry('CNY')], 'uploads/k.pdf', 'h', 'PDF')
    entries, _ = _written(parse)
    assert entries[0]['fxStatus'] == 'unconverted'


@pytest.mark.parametrize('amount', ['-5.00', '1000000000000', '1e30'])
def test_negative_or_absurd_amounts_skip_only_that_entry(parse, amount, capsys):
    parse.save_pending_entries([_entry('CNY', amount), _entry('CNY')], 'uploads/k.pdf', 'h', 'PDF')
    entries, lines = _written(parse)
    assert len(entries) == 1 and [l['amount'] for l in lines] == ['10.00', '10.00']
    assert 'entry_invalid_skipped' in capsys.readouterr().out
