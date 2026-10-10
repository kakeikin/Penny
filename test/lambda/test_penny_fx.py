import random
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from penny_common.fx import CENT, FxError, normalize_currency, parse_rates, to_base

D = Decimal
RATES = {'CNY': D('7.25'), 'JPY': D('150.3'), 'EUR': D('0.92')}
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _line(direction, amount, account='a'):
    return {'accountId': account, 'direction': direction, 'amount': D(amount), 'note': ''}


def _sides(lines):
    debit = sum(l['amount'] for l in lines if l['direction'] == 'DEBIT')
    credit = sum(l['amount'] for l in lines if l['direction'] == 'CREDIT')
    return debit, credit


def _item(rates, age=timedelta(days=1)):
    return {'base': 'USD', 'rates': rates, 'updatedAt': (NOW - age).isoformat()}


@pytest.mark.parametrize('raw,code', [(None, None), ('', None), ('  ', None), ('usd', 'USD'),
                                      (' CNY ', 'CNY'), ('rmb', 'CNY'), ('JPY', 'JPY')])
def test_normalize_currency(raw, code):
    assert normalize_currency(raw) == code


@pytest.mark.parametrize('raw', ['HKD', 'dollars', 12, ['USD']])
def test_normalize_currency_rejects_unsupported(raw):
    with pytest.raises(FxError):
        normalize_currency(raw)


def test_parse_rates_drops_bad_values():
    item = _item({'CNY': '7.25', 'JPY': 'abc', 'EUR': '0', 'GBP': '-1', 'USD': '1'})
    assert parse_rates(item, NOW) == {'CNY': D('7.25'), 'USD': D('1')}


@pytest.mark.parametrize('item', [None, [], {'rates': None}, {'rates': ['CNY']},
                                  {'rates': {'CNY': '7.25'}},                       # no updatedAt
                                  {'rates': {'CNY': '7.25'}, 'updatedAt': 'yesterday'}])
def test_parse_rates_rejects_malformed_items(item):
    assert parse_rates(item, NOW) == {}


def test_parse_rates_rejects_stale_rates():
    assert parse_rates(_item({'CNY': '7.25'}, age=timedelta(days=13)), NOW) == {'CNY': D('7.25')}
    assert parse_rates(_item({'CNY': '7.25'}, age=timedelta(days=15)), NOW) == {}


def test_usd_lines_are_copied_unchanged():
    lines = [_line('DEBIT', '120.00'), _line('CREDIT', '120.00')]
    out = to_base(lines, 'USD', {})
    assert out == lines and out[0] is not lines[0]
    assert 'originalCurrency' not in out[0]


def test_cny_converts_and_keeps_original():
    out = to_base([_line('DEBIT', '72.50'), _line('CREDIT', '72.50')], 'CNY', RATES)
    assert [l['amount'] for l in out] == [D('10.00'), D('10.00')]
    assert out[0]['originalCurrency'] == 'CNY'
    assert out[0]['originalAmount'] == D('72.50')
    assert out[0]['exchangeRate'] == D('7.25')


def test_eur_rate_below_one_converts_upward():
    out = to_base([_line('DEBIT', '9.20'), _line('CREDIT', '9.20')], 'EUR', RATES)
    assert [l['amount'] for l in out] == [D('10.00'), D('10.00')]


def test_total_is_the_converted_total_not_the_larger_rounded_side():
    # 1/150.3 rounds to 0.01 per line, but the true total 3/150.3 = 0.02.
    lines = [_line('DEBIT', '3', 'x'), _line('CREDIT', '1', 'a'), _line('CREDIT', '1', 'b'), _line('CREDIT', '1', 'c')]
    out = to_base(lines, 'JPY', RATES)
    assert _sides(out) == (D('0.02'), D('0.02'))
    assert all(l['amount'] >= 0 for l in out)


def test_random_entries_balance_at_the_exact_converted_total():
    rng = random.Random(7)
    for _ in range(2000):
        currency = rng.choice(['CNY', 'JPY', 'EUR'])
        debits = [D(rng.randint(1, 50000)) / 100 for _ in range(rng.randint(1, 8))]
        lines = [_line('DEBIT', str(a), f'd{i}') for i, a in enumerate(debits)]
        lines.append(_line('CREDIT', str(sum(debits)), 'card'))
        out = to_base(lines, currency, RATES)
        target = (sum(debits) / RATES[currency]).quantize(CENT, rounding=ROUND_HALF_UP)
        assert _sides(out) == (target, target)
        assert all(l['amount'] >= 0 for l in out)


def test_more_than_two_decimals_is_rounded_half_up():
    out = to_base([_line('DEBIT', '0.3625'), _line('CREDIT', '0.3625')], 'CNY', RATES)   # 0.05
    assert [l['amount'] for l in out] == [D('0.05'), D('0.05')]


def test_zero_line_stays_zero_without_breaking_balance():
    out = to_base([_line('DEBIT', '72.50'), _line('DEBIT', '0', 'tax'), _line('CREDIT', '72.50')], 'CNY', RATES)
    assert [l['amount'] for l in out] == [D('10.00'), D('0.00'), D('10.00')]


def test_unbalanced_printed_amounts_are_rejected():
    with pytest.raises(FxError):
        to_base([_line('DEBIT', '72.50'), _line('CREDIT', '72.49')], 'CNY', RATES)


def test_huge_amount_raises_fx_error_not_invalid_operation():
    with pytest.raises(FxError):
        to_base([_line('DEBIT', '1e30'), _line('CREDIT', '1e30')], 'CNY', RATES)


def test_missing_rate_raises():
    with pytest.raises(FxError):
        to_base([_line('DEBIT', '1.00'), _line('CREDIT', '1.00')], 'GBP', RATES)
