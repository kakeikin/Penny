import io
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pdf_fixtures import make_pdf


@pytest.fixture
def parse(lambda_module, monkeypatch):
    index = lambda_module('parse')
    monkeypatch.setattr(index, 'APP_BUCKET', 'app')
    return index


def test_load_claude_json_uses_decimal(parse):
    fence = '`' * 3  # Claude sometimes wraps JSON in a markdown code fence
    out = parse.load_claude_json(f'{fence}json\n{{"entries": [{{"lines": [{{"amount": 0.1}}]}}]}}\n{fence}')
    assert out['entries'][0]['lines'][0]['amount'] == Decimal('0.1')


def test_validate_balance_exact_decimal(parse):
    lines = [{'direction': 'DEBIT', 'amount': Decimal('0.1')}, {'direction': 'DEBIT', 'amount': Decimal('0.2')},
             {'direction': 'CREDIT', 'amount': Decimal('0.3')}]
    assert parse.validate_balance(lines) is True
    assert parse.validate_balance([{'direction': 'DEBIT', 'amount': Decimal('100.00')},
                                   {'direction': 'CREDIT', 'amount': Decimal('99.99')}]) is False


def test_load_claude_json_tolerates_one_line_fence_and_prose(parse):
    fence = '`' * 3
    assert parse.load_claude_json(f'{fence}{{"entries": []}}{fence}') == {'entries': []}
    assert parse.load_claude_json(f'Here you go:\n{fence}json\n{{"entries": []}}\n{fence}\nDone.') == {'entries': []}
    assert parse.load_claude_json('Sure! {"entries": []} hope this helps') == {'entries': []}


def test_load_claude_json_rejects_non_finite(parse):
    with pytest.raises(ValueError):
        parse.load_claude_json('{"entries": [{"lines": [{"amount": Infinity}]}]}')


def test_validate_balance_accepts_int_amounts(parse):
    assert parse.validate_balance([{'direction': 'DEBIT', 'amount': Decimal('120.00')},
                                   {'direction': 'CREDIT', 'amount': 120}]) is True


def test_entry_hash_same_for_decimal_and_float(parse):
    base = {'date': '2026-03-14', 'description': 'ABC UTILITIES'}
    as_float = {**base, 'lines': [{'direction': 'DEBIT', 'amount': 120.1}, {'direction': 'CREDIT', 'amount': 120.1}]}
    as_dec = {**base, 'lines': [{'direction': 'DEBIT', 'amount': Decimal('120.10')}, {'direction': 'CREDIT', 'amount': Decimal('120.10')}]}
    assert parse.compute_entry_hash(as_float) == parse.compute_entry_hash(as_dec)


def test_unbalanced_entry_is_logged_not_silently_dropped(parse, monkeypatch, capsys):
    monkeypatch.setattr(parse, 'dynamodb', MagicMock())
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda h: False)
    entry = {'date': '2026-03-14', 'description': 'X',
             'lines': [{'accountId': 'a', 'direction': 'DEBIT', 'amount': Decimal('100.00')},
                       {'accountId': 'b', 'direction': 'CREDIT', 'amount': Decimal('99.99')}]}
    parse.save_pending_entries([entry], 'uploads/k.pdf', 'h', 'PDF')
    out = capsys.readouterr().out
    assert 'entry_unbalanced_skipped' in out and '"debit": "100.00"' in out
    assert 'description' not in out          # no document text in logs
    parse.dynamodb.Table.return_value.put_item.assert_not_called()
