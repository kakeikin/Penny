"""Convert parsed journal lines into the ledger's base currency (USD), keeping them balanced."""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

BASE = 'USD'
SUPPORTED = ('USD', 'CNY', 'EUR', 'JPY', 'GBP')    # the currencies ExchangeRateLambda stores
_ALIASES = {'RMB': 'CNY'}
CENT = Decimal('0.01')
MAX_RATE_AGE = timedelta(days=14)    # ExchangeRateLambda refreshes weekly; older means it is failing


class FxError(ValueError):
    """The amounts cannot be converted (unsupported currency or no usable rate)."""


def normalize_currency(value):
    """ISO code for a model-reported currency; None means "not reported" (treated as BASE)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise FxError('currency must be a string')
    code = _ALIASES.get(value.strip().upper(), value.strip().upper())
    if code not in SUPPORTED:
        raise FxError('unsupported currency')
    return code


def parse_rates(item, now=None) -> dict:
    """{code: Decimal units per 1 USD} from the exchange-rates table item.

    Bad values are dropped. A malformed or stale item (older than MAX_RATE_AGE, or with no
    readable updatedAt) yields {}, so callers book the amounts unconverted instead of guessing.
    """
    if not isinstance(item, dict) or not isinstance(item.get('rates'), dict):
        return {}
    try:
        updated = datetime.fromisoformat(str(item.get('updatedAt')))
    except ValueError:
        return {}
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if (now or datetime.now(timezone.utc)) - updated > MAX_RATE_AGE:
        return {}
    out = {}
    for code, raw in item['rates'].items():
        try:
            rate = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        if rate.is_finite() and rate > 0:
            out[code] = rate
    return out


def to_base(lines: list, currency: str, rates: dict) -> list:
    """Return copies of `lines` with amounts in USD.

    `rates[currency]` is units of `currency` per 1 USD (the ExchangeRateLambda format, e.g.
    CNY 7.25), so usd = original / rate, rounded half-up to cents. The entry total is converted
    once; each side's rounding drift goes onto that side's largest line, so both sides equal the
    converted total exactly (never biased up or down). Each line keeps originalCurrency,
    originalAmount and exchangeRate.
    """
    if currency == BASE:
        return [dict(line) for line in lines]
    rate = rates.get(currency)
    if rate is None:
        raise FxError('no rate')
    printed = {side: sum((Decimal(str(l['amount'])) for l in lines if l['direction'] == side), Decimal('0'))
               for side in ('DEBIT', 'CREDIT')}
    if printed['DEBIT'] != printed['CREDIT']:
        raise FxError('printed amounts do not balance')
    try:
        target = (printed['DEBIT'] / rate).quantize(CENT, rounding=ROUND_HALF_UP)
        out = []
        for line in lines:
            original = Decimal(str(line['amount']))
            out.append({**line, 'amount': (original / rate).quantize(CENT, rounding=ROUND_HALF_UP),
                        'originalCurrency': currency, 'originalAmount': original, 'exchangeRate': rate})
    except ArithmeticError as e:            # e.g. InvalidOperation: too large to quantize
        raise FxError('amount cannot be converted') from e
    for side in ('DEBIT', 'CREDIT'):
        side_lines = [l for l in out if l['direction'] == side]
        drift = target - sum((l['amount'] for l in side_lines), Decimal('0'))
        if drift:
            largest = max(side_lines, key=lambda l: l['amount'])
            largest['amount'] += drift
            if largest['amount'] < 0:
                raise FxError('rounding would make a line negative')
    return out
