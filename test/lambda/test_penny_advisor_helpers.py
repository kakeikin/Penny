import io
import json
import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.conditions import Attr

from penny_common.citations import contains_money, validate_citations
from penny_common.embedding import embed_text
from boto3.dynamodb.conditions import Key

from penny_common.ledger import (confirmed_entries, entry_amount, flows, lines_for, money, months_between,
                                   session_condition)
from penny_common.pricing import estimate_cost_usd
from penny_common.session import session_from_headers, valid_session_id


# ── citations ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('text', ['$120.00', '¥8', '€ 5', '£1,234.56', 'USD 40', '40 CNY', '-120.00',
                                  '(120.00)', '($120.00)', 'total 1,234.56', '$5', '¥ 1,200', '-$40',
                                  'US$40', '￥40', '40元', '40 yuan', '40 dollars', 'usd 40'])
def test_money_detected(text):
    assert contains_money(text)


@pytest.mark.parametrize('text', ['3 transactions', 'March 2026', 'page 2', '12 times', '2026-03-14', '',
                                  '12.50%', '1.25x', 'version 3.10.12', '2026.03.14', '03/14', '14:32',
                                  '40.5', '1,234', '(2026)'])
def test_bare_integers_and_dates_are_not_money(text):
    assert not contains_money(text)


RESULTS = {'T1': {'ref': 'T1', 'type': 'transaction', 'amount': '120.00'},
           'S1': {'ref': 'S1', 'type': 'summary', 'expense': '400.00'}}


def test_valid_citations_kept_in_order_of_first_use():
    out = validate_citations('Rent was 1850.00 [T1]. Total spend 400.00 [S1], see [T1].', RESULTS)
    assert out['invalidCitations'] == 0 and out['evidenceStatus'] == 'supported'
    assert [c['ref'] for c in out['citations']] == ['T1', 'S1']


def test_invented_refs_are_removed_and_counted():
    out = validate_citations('Spent 99.00 [T9] at the cafe [D3].', RESULTS)
    assert out['answer'] == 'Spent 99.00 at the cafe.'
    assert out['invalidCitations'] == 2
    assert out['evidenceStatus'] == 'unsupported'      # money with no surviving citation


@pytest.mark.parametrize('answer,expected,invalid', [
    ('[T1, S1] spent $5', '[T1][S1] spent $5', 0),            # comma list, normalized
    ('Paid [t1] $5', 'Paid [T1] $5', 0),                      # lowercase
    ('Paid $5 [T1, S9].', 'Paid $5 [T1].', 1),                # partly invalid group
    ('word [T9]word', 'word word', 1),                        # no gluing words together
    ('Spent $5\n[T9] next', 'Spent $5\n next', 1),           # newline is not eaten
    ('Paid $5 ([T9]).', 'Paid $5.', 1),                       # no empty parentheses left
    ('Paid $5 [T9][S9].', 'Paid $5.', 2),
])
def test_ref_group_edge_cases(answer, expected, invalid):
    out = validate_citations(answer, RESULTS)
    assert out['answer'] == expected and out['invalidCitations'] == invalid


def test_partially_cited_answer_counts_as_supported():
    out = validate_citations('$5 [T1] and $9 [T9]', RESULTS)
    assert out['evidenceStatus'] == 'supported' and out['invalidCitations'] == 1


@pytest.mark.parametrize('text', ['1' * 30000 + 'x', '1,' * 15000 + 'x', '9' * 30000])
def test_money_regex_is_linear_on_long_digit_runs(text):
    started = time.monotonic()
    contains_money(text)
    assert time.monotonic() - started < 0.5


def test_no_money_no_citation_is_supported():
    out = validate_citations("I don't have any transactions for that period.", RESULTS)
    assert out['evidenceStatus'] == 'supported' and out['citations'] == []


# ── pricing ──────────────────────────────────────────────────────────────────

def test_cost_haiku_5_5():
    assert estimate_cost_usd('us.anthropic.claude-haiku-5-5', 2000, 500) == '0.000450'


def test_cost_unknown_model_is_none():
    assert estimate_cost_usd('some-other-model', 10, 10) is None
    assert estimate_cost_usd(None, 10, 10) is None


def test_cost_matches_whole_model_token_and_dated_bedrock_ids():
    assert estimate_cost_usd('us.anthropic.claude-haiku-4-5-20251001-v1:0', 1000, 1000) == '0.006000'
    assert estimate_cost_usd('us.anthropic.claude-sonnet-4-6', 1_000_000, 0) == '3.000000'
    assert estimate_cost_usd('us.anthropic.claude-haiku-5-5-mini', 1, 1) is None


# ── session ──────────────────────────────────────────────────────────────────

def test_session_from_headers_any_casing_and_owner():
    assert session_from_headers({'x-session-id': 'abc-123'}) == 'abc-123'
    assert session_from_headers({'X-Session-Id': 'abc-123'}) == 'abc-123'
    assert session_from_headers({}) is None and session_from_headers(None) is None
    assert session_from_headers({'X-Session-Id': ''}) is None


@pytest.mark.parametrize('sid', ['owner', 'OWNER', 'a#b', 'a/b', 'x' * 65])
def test_session_from_headers_rejects_bad_ids(sid):
    with pytest.raises(ValueError):
        session_from_headers({'X-Session-Id': sid})
    assert valid_session_id(sid) is False


def test_valid_session_id_accepts_uuid():
    assert valid_session_id('123e4567-e89b-12d3-a456-426614174000') and not valid_session_id(None)


# ── embedding ────────────────────────────────────────────────────────────────

def test_embed_text_request_and_dimension_check():
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = {'body': io.BytesIO(json.dumps({'embedding': [0.0] * 512,
                                                                        'inputTextTokenCount': 4}).encode())}
    vec, n = embed_text(bedrock, 'hello', 512)
    assert len(vec) == 512 and n == 4
    assert json.loads(bedrock.invoke_model.call_args.kwargs['body']) == {'inputText': 'hello', 'dimensions': 512,
                                                                         'normalize': True}
    bedrock.invoke_model.return_value = {'body': io.BytesIO(json.dumps({'embedding': [0.0] * 3}).encode())}
    with pytest.raises(ValueError):
        embed_text(bedrock, 'hello', 512)


# ── ledger ───────────────────────────────────────────────────────────────────

def test_session_condition():
    assert session_condition(None) == Attr('sessionId').not_exists()
    assert session_condition('abc') == Attr('sessionId').eq('abc')


def test_months_between_inclusive_and_capped():
    assert months_between('2025-11-15', '2026-02-01') == ['2025-11', '2025-12', '2026-01', '2026-02']
    with pytest.raises(ValueError):
        months_between('2020-01-01', '2026-01-01')


def test_confirmed_entries_queries_date_index_per_month():
    table = MagicMock()
    table.query.side_effect = [{'Items': [{'entryId': 'a'}], 'LastEvaluatedKey': {'k': 1}},
                               {'Items': [{'entryId': 'b'}]}, {'Items': [{'entryId': 'c'}]}]
    out = confirmed_entries(table, 'abc', '2026-03-05', '2026-04-10')
    assert [e['entryId'] for e in out] == ['a', 'b', 'c']
    first = table.query.call_args_list[0].kwargs
    assert first['IndexName'] == 'date-index'
    assert first['KeyConditionExpression'] == Key('yearMonth').eq('2026-03') & Key('date').between('2026-03-05', '2026-04-10')
    assert first['FilterExpression'] == Attr('status').eq('CONFIRMED') & Attr('sessionId').eq('abc')
    assert table.query.call_args_list[1].kwargs['ExclusiveStartKey'] == {'k': 1}
    assert table.query.call_args_list[2].kwargs['KeyConditionExpression'].get_expression()['values'][0] == \
        Key('yearMonth').eq('2026-04')
    table.scan.assert_not_called()


def test_lines_for_queries_by_key_for_few_entries():
    table = MagicMock()
    table.query.side_effect = lambda **kw: {'Items': [{'entryId': kw['KeyConditionExpression'].get_expression()['values'][1]}]}
    out = lines_for(table, ['b', 'a'])
    assert list(out) == ['a', 'b'] and table.query.call_count == 2
    table.scan.assert_not_called()
    assert lines_for(MagicMock(), []) == {}


def test_lines_for_scans_once_for_many_entries():
    table = MagicMock()
    ids = [f'e{i}' for i in range(51)]
    table.scan.side_effect = [{'Items': [{'entryId': 'e1'}, {'entryId': 'zz'}], 'LastEvaluatedKey': {'k': 1}},
                              {'Items': [{'entryId': 'e2'}]}]
    out = lines_for(table, ids)
    assert set(out) == {'e1', 'e2'} and table.query.call_count == 0


def test_money_accepts_str_int_and_float():
    assert money('1.10') == Decimal('1.10') and money(3) == Decimal('3') and money(0.1) == Decimal('0.1')


def test_amounts_are_exact_decimals():
    lines = [{'accountId': 'food', 'direction': 'DEBIT', 'amount': '0.10'},
             {'accountId': 'food', 'direction': 'DEBIT', 'amount': '0.20'},
             {'accountId': 'bank', 'direction': 'CREDIT', 'amount': '0.30'}]
    assert entry_amount(lines) == Decimal('0.30')
    accounts = {'food': {'type': 'EXPENSE'}, 'bank': {'type': 'ASSET'}, 'pay': {'type': 'INCOME'}}
    assert flows(lines, accounts) == (Decimal('0'), Decimal('0.30'))
    assert flows([{'accountId': 'pay', 'direction': 'CREDIT', 'amount': '3200.00'}], accounts) == (Decimal('3200.00'), Decimal('0'))
