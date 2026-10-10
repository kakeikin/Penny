"""Deterministic eval metrics (no LLM judge). Pure functions: no AWS, no network.

Every rate is reported with its denominator. A check that does not apply to a question is None
and is left out of that rate (e.g. tool selection for questions that expect no tool), so no
question passes a check for free. An answer that errored or was truncated fails every quality
check that applies to it.
"""
import re
from decimal import Decimal

# 1,234.56 / 1234.56 / 15.49: two-decimal amounts only, so years and day numbers never count.
_AMOUNT = re.compile(r'(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d{2}(?![\d])|(?<![\d.,])\d+\.\d{2}(?![\d])')
_DOLLARS = re.compile(r'\$\s?(\d[\d,]*(?:\.\d+)?)')        # "$2,100" or "$0" in no-data answers
_WS = re.compile(r'\s+')
MAX_EXTRA_FACTOR = 2      # more than 2x as many other amounts as expected ones = a "verbose" answer
DUMP_FACTOR = 10          # more than 10x = listing everything to hit the right number: fails
# Bump when a scoring rule changes, and say what changed in RULE_CHANGES (rendered into the report).
METRICS_VERSION = '2'
RULE_CHANGES = {
    '2': 'Answers listing extra amounts (e.g. a correct total plus its category breakdown) no longer fail '
         f'numeric exactness up to {DUMP_FACTOR}x the expected count; between {MAX_EXTRA_FACTOR}x and '
         f'{DUMP_FACTOR}x they are counted separately as "verbose".',
}


def amounts_in(text: str) -> set:
    """Every two-decimal amount in the text, as Decimal (sign and currency symbols ignored)."""
    return {Decimal(m.group(0).replace(',', '')) for m in _AMOUNT.finditer(text or '')}


def dollar_figures_in(text: str) -> set:
    """Every $-prefixed figure, with or without cents."""
    return {Decimal(m.group(1).replace(',', '')) for m in _DOLLARS.finditer(text or '')}


def score_answer(q: dict, resp: dict, ok: bool = True) -> dict:
    """Per-question checks for one advisor response; None means "does not apply"."""
    answer = resp.get('answer') or ''
    found = amounts_in(answer)
    expected = {Decimal(a) for a in q.get('expectAmounts') or []}
    tools = set(resp.get('toolsUsed') or [])
    citations = resp.get('citations') or []
    truncated = bool(resp.get('truncated'))
    usable = ok and not truncated

    numeric = verbose = None
    if q.get('expectNoData'):
        numeric = usable and not any(a != 0 for a in found | dollar_figures_in(answer))
    elif expected:
        extra = len(found - expected)
        numeric = usable and expected <= found and extra <= DUMP_FACTOR * len(expected)
        verbose = (extra > MAX_EXTRA_FACTOR * len(expected)) if usable else None
    text = None
    if q.get('expectText'):
        text = usable and q['expectText'].lower() in answer.lower()
    has_money = any(a != 0 for a in found | dollar_figures_in(answer))   # "$0" is not a figure to cite
    return {
        'id': q['id'],
        'category': q['category'],
        'ok': ok,
        'truncated': truncated,
        'toolOk': (ok and set(q['expectTools']) <= tools) if q.get('expectTools') else None,
        'numericOk': numeric,
        'textOk': text,
        # Of the answers that carry citations, how many had none removed by the validator.
        'citationsValid': (resp.get('invalidCitations', 0) == 0) if (usable and citations) else None,
        # Of the answers that state money, how many cite at least one source.
        'moneyCited': bool(citations) if (usable and has_money) else None,
        'verbose': verbose,
    }


def passed(score: dict) -> bool:
    """A question passes when every check that applies to it passes."""
    checks = [score[k] for k in ('toolOk', 'numericOk', 'textOk') if score[k] is not None]
    return score['ok'] and not score['truncated'] and all(checks)


def rank_of(results: list, gold_docs: list):
    """1-based rank of the first retrieved chunk from any gold (file, page), else None."""
    wanted = {(g['file'], g['page']) for g in gold_docs}
    for i, r in enumerate(results, start=1):
        if (r.get('fileName'), int(r.get('page') or 0)) in wanted:
            return i
    return None


def retrieval_metrics(ranks: list) -> dict:
    n = len(ranks)
    if not n:
        return {'n': 0, 'hit@3': None, 'hit@5': None, 'mrr': None}
    hit = lambda k: sum(1 for r in ranks if r is not None and r <= k) / n
    return {'n': n, 'hit@3': round(hit(3), 3), 'hit@5': round(hit(5), 3),
            'mrr': round(sum(1 / r for r in ranks if r) / n, 3)}


def _norm(text: str) -> str:
    return _WS.sub(' ', (text or '').strip()).lower()


def _entry_amount(entry: dict) -> Decimal:
    return sum((Decimal(str(l['amount'])) for l in entry.get('lines', []) if l['direction'] == 'DEBIT'),
               Decimal('0'))


def ledger_matches_gold(gold: list, entries: list) -> dict:
    """Do the booked entries equal the gold transactions exactly (as a multiset of date, amount)?"""
    want = sorted((g['date'], abs(Decimal(g['amount']))) for g in gold)
    have = sorted((e.get('date'), _entry_amount(e)) for e in entries)
    missing, extra = list(want), []
    for item in have:
        if item in missing:
            missing.remove(item)
        else:
            extra.append(item)
    return {'ok': not missing and not extra, 'expected': len(want), 'booked': len(have),
            'missing': len(missing), 'extra': len(extra)}


def evidence_metrics(gold: list, entries: list) -> dict:
    """Match each gold statement line to the booked entry with the same date and amount, then
    check that the entry's evidence quotes that whole line on the right page. Receipts (line None)
    are skipped: image evidence is only checked against Claude's own transcript. Requiring the
    whole printed line is conservative: a correct but partial quote counts as a miss."""
    pool = list(entries)
    matched = correct = 0
    checked = [g for g in gold if g.get('line')]
    for g in checked:
        target = abs(Decimal(g['amount']))
        hit = next((e for e in pool if e.get('date') == g['date'] and _entry_amount(e) == target), None)
        if hit is None:
            continue
        pool.remove(hit)
        matched += 1
        ev = (hit.get('evidence') or [None])[0]
        if ev and int(ev.get('page') or 0) == g['page'] and _norm(g['line']) in _norm(ev.get('text')):
            correct += 1
    n = len(checked)
    return {'n': n, 'booked': matched, 'bookedRate': round(matched / n, 3) if n else None,
            'evidenceCorrect': correct, 'evidenceAccuracy': round(correct / matched, 3) if matched else None}


def percentile(values: list, p: float):
    """Nearest-rank percentile (p in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, -(-len(ordered) * p // 100))           # ceil(n * p / 100)
    return ordered[int(k) - 1]


def _rate(scores: list, key: str) -> dict:
    applicable = [s[key] for s in scores if s[key] is not None]
    return {'rate': round(sum(applicable) / len(applicable), 3) if applicable else None,
            'passed': sum(applicable), 'n': len(applicable)}


def summarize(scores: list, costs: list, latencies_ms: list) -> dict:
    """costs: estCostUsd strings, or None when the model is unpriced; latencies of ok answers only."""
    n = len(scores)
    priced = [Decimal(c) for c in costs if c is not None]
    total = sum(priced, Decimal('0'))
    return {
        'n': n,
        'errors': sum(1 for s in scores if not s['ok']),
        'truncated': sum(1 for s in scores if s['truncated']),
        'passRate': _rate([{**s, 'pass': passed(s)} for s in scores], 'pass'),
        'toolSelection': _rate(scores, 'toolOk'),
        'numericExactness': _rate(scores, 'numericOk'),
        'textMatch': _rate(scores, 'textOk'),
        'citationValidity': _rate(scores, 'citationsValid'),
        'moneyCited': _rate(scores, 'moneyCited'),
        'verbose': _rate(scores, 'verbose'),
        'meanCostUsd': f'{total / len(priced):.6f}' if priced else None,
        'totalCostUsd': f'{total:.6f}' if priced else None,
        'unpricedAnswers': sum(1 for c in costs if c is None),
        'latencyP50Ms': percentile(latencies_ms, 50),
        'latencyP95Ms': percentile(latencies_ms, 95),
    }
