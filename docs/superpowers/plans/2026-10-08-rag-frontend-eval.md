# Penny RAG — Plan 3: Frontend, Base Currency, and Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the RAG work visible and measurable. Advisor citations become clickable chips that open the source evidence. The ledger gets a defined base currency (USD). The Upload page waits for the real parse result. A deterministic eval produces the numbers the README and resume quote.

**Architecture:**
- **Frontend.** It stays plain browser scripts with no build step. The new pure helpers (`citations.js`, `upload-poll.js`) also export themselves for jest. One shared modal (`evidence-viewer.js`) serves both transaction evidence and advisor chips.
- **Currency.** ParseLambda asks Claude for each entry's printed currency. Non-USD amounts are converted at the stored ExchangeRateLambda rate, and debits stay exactly equal to credits. If conversion isn't possible, the entry is flagged for review instead of being booked wrong.
- **Eval.** It runs the real AdvisorLambda code locally against the deployed resources, in an isolated demo session `eval-v1`. Only the model ID changes between runs.

**Tech Stack:** vanilla JS + Tailwind CDN; Python 3.12; boto3; jest + ts-jest; pytest; Pillow (dataset regeneration only).

**Spec:** `docs/superpowers/specs/2026-10-01-penny-rag-evidence-design.md` §5 Frontend and §9 Evaluation.

> **Execution rule:** the repo owner runs every git command, deploy and AWS command. Implementers never run git (not even `git status`), `cdk deploy`, or `aws`.

---

## Decisions (2026-10-08)

- **Base currency: USD.**
  - Every frontend amount uses `Money.fmt` (`$1,234.56`).
  - Account creation defaults to `USD`.
  - The foreign-currency pickers offer CNY/EUR/GBP/JPY/HKD/CAD/AUD.
  - ParseLambda converts CNY/EUR/JPY/GBP at the stored rate, which is in units per USD: `usd = original / rate`, rounded half-up to cents.
  - The entry total is converted once. Each side's rounding drift goes onto that side's largest line, so both sides equal the converted total exactly, with no upward bias.
  - Lines keep `originalCurrency`, `originalAmount` and `exchangeRate`, the same fields ManualEntryLambda writes.
  - An unsupported currency, a missing, malformed or stale (older than 14 days) rate, a rates-table error, or an amount that can't be converted books the amounts as printed. The entry gets `fxStatus: "unconverted"` and `printedCurrency`. The Upload page warns, and ConfirmLambda answers 409 unless the user explicitly acknowledges it.
  - `entryHash` for non-USD entries uses the printed total plus the currency code. The same receipt therefore matches itself after a rate change, and HKD 72.50 never collides with USD 72.50. The USD hash format is unchanged.
  - ParseLambda now rejects negative amounts (direction carries the sign) and amounts of 1e12 or more, skipping only that entry.
  - Known limitation: every entry converts at the latest stored rate, not the rate on its transaction date.
- **ConfirmLambda integrity (found in Task 1 review).**
  - Client-supplied `lines` used to replace the stored lines **without any balance check**, and the stored-line check used a float tolerance. Client lines are now validated (Decimal, at least 2 lines, valid directions, amounts from 0 up to 1e12) and must balance exactly before anything is written.
  - The original-currency fields are kept when lines are rewritten.
  - A session can only confirm its own entries; otherwise it gets 404.
- **Lean eval** instead of the spec's full §9:
  - 3 synthetic statements, 2 synthetic receipts and 20 questions.
  - Metrics: tool selection, numeric exactness, citation validity, supported rate, cost, p50/p95 latency, retrieval hit@3/hit@5/MRR, and evidence-linking accuracy.
  - Models compared: Haiku 4.5 and Sonnet 4.6.
  - **Deferred:** the 512 vs 1024-dimension ablation (needs a second index), the old-advisor baseline (its code is gone), and the citation validator's false-negative rate.
- **XSS fix (found during planning).**
  - The advisor page inserted the model's answer as raw HTML. Answers can quote uploaded-document text, so a crafted PDF could inject script.
  - Transaction descriptions and notes on the Transactions and Upload pages were also unescaped.
  - All of this text is now escaped. Evidence links are rendered only for `https://` URLs.
- **Document chips show the retrieved chunk only.** v1 vectors carry no presigned link to the original file. Transaction chips fetch `GET /api/entries/{id}/evidence` for the "Open source page" link.

## File map

| File | Task | Responsibility |
|---|---|---|
| `lambda/common/penny_common/fx.py` | 1 | currency normalization, rate parsing, balanced conversion to USD |
| `lambda/parse/index.py` | 1 | ask for `currency`, convert before hashing and booking, write original-currency fields |
| `lambda/manual-entry/index.py` | 1 | new accounts default to USD |
| `lambda/confirm/index.py` | 1 | validate and balance-check client lines, keep FX fields, session check, unconverted 409 |
| `lib/finance-stack.ts` | 1 | ParseLambda may `dynamodb:GetItem` the exchange-rates table (nothing else) |
| `frontend/format.js` | 2 | `Money.fmt` / `Money.symbol` |
| `frontend/pages/*.js`, `frontend/index*.html` | 2–4 | use `Money`, chips, viewer, polling |
| `frontend/citations.js` | 3 | pure: escape, chips, citation and evidence HTML |
| `frontend/evidence-viewer.js` | 3 | the shared modal |
| `frontend/upload-poll.js` | 4 | pure: wait for one file's parsed entries |
| `package.json`, `package-lock.json` | 5 | `playwright` → devDependencies |
| `eval/generate_dataset.py`, `eval/dataset/v1/*` | 6 | deterministic synthetic corpus, gold transactions and questions |
| `eval/metrics.py`, `eval/run_eval.py` | 7 | deterministic scoring; setup and run against the deployed stack |
| `.github/workflows/ci.yml` | 7 | also run `test/eval` |
| `docs/evaluation-results.md`, `eval/results/*.json` | 8 | written by the eval run |
| `README.md` | 9 | rewritten around architecture, cost and eval numbers |

---

### Task 1: Base currency USD — backend, and confirm integrity

**Files:**
- Create: `lambda/common/penny_common/fx.py`, `test/lambda/test_penny_fx.py`, `test/lambda/test_parse_currency.py`
- Modify: `lambda/parse/index.py`, `lambda/manual-entry/index.py`, `lambda/confirm/index.py`, `lib/finance-stack.ts`, `test/finance-stack.test.ts`, `test/lambda/test_confirm.py`

- [ ] **Step 1: Write the failing tests.**

Create `test/lambda/test_penny_fx.py`:

````python
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
````

Create `test/lambda/test_parse_currency.py`:

````python
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
````

Append the confirm tests:

Apply to `test/lambda/test_confirm.py`:

````diff
--- a/test/lambda/test_confirm.py
+++ b/test/lambda/test_confirm.py
@@ -61,3 +61,96 @@
         event = {'pathParameters': {'id': 'e1'}, 'httpMethod': 'PUT', 'body': None}
         resp = handler(event, {})
     assert resp['statusCode'] == 400
+
+
+BALANCED = [{'lineId': '000', 'accountId': 'dining', 'direction': 'DEBIT', 'amount': '10.00'},
+            {'lineId': '001', 'accountId': 'card', 'direction': 'CREDIT', 'amount': '10.00'}]
+
+
+def _confirm(entry, body=None, headers=None, stored=BALANCED):
+    from index import handler
+    entries, lines = MagicMock(), MagicMock()
+    entries.get_item.return_value = {'Item': entry} if entry else {}
+    lines.query.return_value = {'Items': stored}
+    with patch('index.dynamodb') as mock_db:
+        mock_db.Table.side_effect = lambda name: {'finance-journal-entries': entries,
+                                                  'finance-journal-lines': lines}[name]
+        event = {'pathParameters': {'id': 'e1'}, 'httpMethod': 'PUT', 'headers': headers,
+                 'body': json.dumps(body) if body is not None else None}
+        resp = handler(event, {})
+    return resp, entries, lines
+
+
+def test_lines_balance_is_exact_decimal():
+    from index import lines_balance
+    assert lines_balance([{'direction': 'DEBIT', 'amount': '100.00'},
+                          {'direction': 'CREDIT', 'amount': '99.995'}]) is False
+
+
+def test_confirm_marks_entry_confirmed_and_clears_fx_status():
+    resp, entries, _ = _confirm({'entryId': 'e1', 'status': 'PENDING'})
+    assert resp['statusCode'] == 200
+    update = entries.update_item.call_args.kwargs
+    assert update['UpdateExpression'] == 'SET #s = :s REMOVE fxStatus'
+    assert update['ExpressionAttributeValues'] == {':s': 'CONFIRMED'}
+
+
+def test_client_lines_must_balance_before_they_replace_stored_lines():
+    body = {'lines': [{'accountId': 'dining', 'direction': 'DEBIT', 'amount': '100.00'},
+                      {'accountId': 'card', 'direction': 'CREDIT', 'amount': '50.00'}]}
+    resp, entries, lines = _confirm({'entryId': 'e1', 'status': 'PENDING'}, body)
+    assert resp['statusCode'] == 400
+    lines.put_item.assert_not_called()
+    lines.delete_item.assert_not_called()
+    entries.update_item.assert_not_called()
+
+
+@pytest.mark.parametrize('bad', [
+    'not a list',
+    [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '1.00'}],                       # one line
+    [{'accountId': '', 'direction': 'DEBIT', 'amount': '1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}],
+    [{'accountId': 'a', 'direction': 'UP', 'amount': '1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}],
+    [{'accountId': 'a', 'direction': 'DEBIT', 'amount': 'abc'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}],
+    [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '-1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '-1.00'}],
+])
+def test_malformed_client_lines_are_rejected(bad):
+    resp, _, lines = _confirm({'entryId': 'e1', 'status': 'PENDING'}, {'lines': bad})
+    assert resp['statusCode'] == 400
+    lines.put_item.assert_not_called()
+
+
+def test_client_lines_keep_original_currency_fields():
+    body = {'lines': [{'accountId': 'dining', 'direction': 'DEBIT', 'amount': '10.00', 'originalCurrency': 'CNY',
+                       'originalAmount': '72.50', 'exchangeRate': '7.25', 'entryId': 'x', 'lineId': '009'},
+                      {'accountId': 'card', 'direction': 'CREDIT', 'amount': '10.00'}]}
+    resp, _, lines = _confirm({'entryId': 'e1', 'status': 'PENDING'}, body)
+    assert resp['statusCode'] == 200
+    written = [c.kwargs['Item'] for c in lines.put_item.call_args_list]
+    assert written[0] == {'entryId': 'e1', 'lineId': '000', 'accountId': 'dining', 'direction': 'DEBIT',
+                          'amount': '10.00', 'note': '', 'originalCurrency': 'CNY', 'originalAmount': '72.50',
+                          'exchangeRate': '7.25'}
+    assert 'originalCurrency' not in written[1]
+
+
+def test_unconverted_entry_needs_explicit_acknowledgement():
+    entry = {'entryId': 'e1', 'status': 'PENDING', 'fxStatus': 'unconverted'}
+    resp, entries, _ = _confirm(entry)
+    assert resp['statusCode'] == 409
+    entries.update_item.assert_not_called()
+    resp, _, _ = _confirm(entry, {'acknowledgeUnconverted': True})
+    assert resp['statusCode'] == 200
+
+
+def test_sessions_can_only_confirm_their_own_entries():
+    assert _confirm({'entryId': 'e1', 'status': 'PENDING'}, headers={'X-Session-Id': 'demo1'})[0]['statusCode'] == 404
+    assert _confirm({'entryId': 'e1', 'status': 'PENDING', 'sessionId': 'demo1'})[0]['statusCode'] == 404
+    assert _confirm({'entryId': 'e1', 'status': 'PENDING', 'sessionId': 'demo1'},
+                    headers={'x-session-id': 'demo1'})[0]['statusCode'] == 200
+
+
+def test_missing_entry_and_bad_requests():
+    assert _confirm(None)[0]['statusCode'] == 404
+    assert _confirm({'entryId': 'e1'}, headers={'X-Session-Id': 'bad id!'})[0]['statusCode'] == 400
+    from index import handler
+    resp = handler({'pathParameters': {'id': 'e1'}, 'body': '[1, 2]'}, {})
+    assert resp['statusCode'] == 400
````

Append the jest test:

Apply to `test/finance-stack.test.ts`:

````diff
--- a/test/finance-stack.test.ts
+++ b/test/finance-stack.test.ts
@@ -190,3 +190,18 @@
     expect(actions).not.toContain('s3vectors:DeleteVectors');
   });
 });
+
+describe('Base currency', () => {
+  test('ParseLambda can only GetItem on the exchange-rates table', () => {
+    const fnId = Object.keys(template.findResources('AWS::Lambda::Function')).find(id => id.startsWith('ParseLambda'))!;
+    const roleRef = template.findResources('AWS::Lambda::Function')[fnId].Properties.Role['Fn::GetAtt'][0];
+    const tableId = Object.entries(template.findResources('AWS::DynamoDB::Table'))
+      .find(([, t]: [string, any]) => t.Properties.TableName === 'finance-exchange-rates')![0];
+    const statements = Object.values(template.findResources('AWS::IAM::Policy'))
+      .filter((p: any) => p.Properties.Roles.some((r: any) => r.Ref === roleRef))
+      .flatMap((p: any) => p.Properties.PolicyDocument.Statement);
+    const onRates = statements.filter((st: any) => JSON.stringify(st.Resource).includes(tableId));
+    const actions = onRates.flatMap((st: any) => ([] as string[]).concat(st.Action));
+    expect(actions).toEqual(['dynamodb:GetItem']);
+  });
+});
````

- [ ] **Step 2: Run them to verify they fail.**

Run: `python -m pytest test/lambda/test_penny_fx.py test/lambda/test_parse_currency.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'penny_common.fx'`.

Run: `python -m pytest test/lambda/test_confirm.py -q`
Expected: several failures (no 409, client lines not balance-checked, no session check).

Run: `npx jest`
Expected: 1 failure, "ParseLambda can only GetItem on the exchange-rates table".

- [ ] **Step 3: Implement.**

Create `lambda/common/penny_common/fx.py`:

````python
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
````

Apply to `lambda/parse/index.py`:

````diff
--- a/lambda/parse/index.py
+++ b/lambda/parse/index.py
@@ -1,6 +1,7 @@
 import boto3
 from boto3.dynamodb.conditions import Attr
 from botocore.config import Config
+from botocore.exceptions import BotoCoreError, ClientError
 import json
 import os
 import base64
@@ -11,6 +12,7 @@
 from decimal import Decimal, InvalidOperation
 from urllib.parse import unquote_plus
 
+from penny_common.fx import BASE, FxError, normalize_currency, parse_rates, to_base
 from penny_common.masking import mask_identifiers
 from penny_common.pdftext import PdfTextError, extract_pdf_pages
 from penny_common.session import valid_session_id
@@ -28,6 +30,7 @@
 ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
 LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')
 APP_BUCKET     = os.environ.get('APP_BUCKET', '')
+EXCHANGE_RATES_TABLE = os.environ.get('EXCHANGE_RATES_TABLE', 'finance-exchange-rates')
 
 MODEL_ID            = 'us.anthropic.claude-sonnet-4-6'
 MAX_OUTPUT_TOKENS   = 12000
@@ -35,6 +38,8 @@
 
 _DIGITS     = re.compile(r'[0-9]+')
 _ISO_DAY    = re.compile(r'\d{4}-\d{2}-\d{2}')
+_ISO_CURRENCY = re.compile(r'[A-Z]{3}')
+MAX_AMOUNT  = Decimal('1000000000000')     # 1e12: anything larger is a parse error, not a transaction
 DIRECTIONS  = ('DEBIT', 'CREDIT')
 
 
@@ -42,14 +47,18 @@
     return hashlib.md5(data).hexdigest()
 
 
-def compute_entry_hash(entry: dict) -> str:
-    """Hash based on date + total amount + first 30 chars of description."""
+def compute_entry_hash(entry: dict, currency: str = BASE) -> str:
+    """Hash based on date + total amount + first 30 chars of description.
+
+    Non-USD entries hash their printed total plus the currency code, so the same receipt
+    matches itself on another day even after the exchange rate has changed.
+    """
     date = entry.get('date', '')
     desc = entry.get('description', '')[:30].strip().lower()
     # Sum all debit amounts as the canonical amount
     # Stable with the pre-Decimal float hashes for amounts with <= 2 decimals.
     total = sum(Decimal(str(l['amount'])) for l in entry.get('lines', []) if l['direction'] == 'DEBIT')
-    raw = f"{date}|{total:.2f}|{desc}"
+    raw = f"{date}|{total:.2f}|{desc}" if currency == BASE else f"{date}|{currency}|{total:.2f}|{desc}"
     return hashlib.md5(raw.encode()).hexdigest()
 
 
@@ -84,6 +93,40 @@
     if not isinstance(parsed, dict):
         raise ValueError('model output is not a JSON object')
     return parsed
+
+
+def load_rates() -> dict:
+    """Latest USD-based rates from ExchangeRateLambda's table; {} if unavailable."""
+    try:
+        item = dynamodb.Table(EXCHANGE_RATES_TABLE).get_item(Key={'base': BASE}).get('Item')
+    except (BotoCoreError, ClientError) as e:
+        print(json.dumps({'event': 'fx_rates_unavailable', 'errorType': type(e).__name__}))
+        return {}
+    return parse_rates(item)
+
+
+def printed_currency(entry: dict) -> str:
+    """The entry's currency as printed: a supported ISO code, a sanitized unsupported one
+    (e.g. 'HKD'), 'XXX' for anything unreadable, or BASE if the model reported none."""
+    try:
+        return normalize_currency(entry.get('currency')) or BASE
+    except FxError:
+        raw = entry.get('currency')
+        code = raw.strip().upper() if isinstance(raw, str) else ''
+        return code if _ISO_CURRENCY.fullmatch(code) else 'XXX'
+
+
+def convert_to_base(entry: dict, currency: str, rates_cache: dict) -> bool:
+    """Convert entry['lines'] to USD in place. False if the amounts had to stay as printed."""
+    if currency == BASE:
+        return True
+    try:
+        if 'rates' not in rates_cache:
+            rates_cache['rates'] = load_rates()
+        entry['lines'] = to_base(entry['lines'], currency, rates_cache['rates'])
+    except FxError:
+        return False
+    return True
 
 
 def get_accounts() -> list:
@@ -112,6 +155,7 @@
 For each transaction, produce one journal entry with balanced debit and credit lines.
 Every amount must be a number with exactly 2 decimal places, and debits must equal credits exactly.
 If classification is uncertain, add a note.
+Set "currency" on each entry to the ISO 4217 code of its amounts as printed (e.g. "USD", "CNY"). Copy amounts exactly as printed; never convert them.
 For each entry, set "evidence" to {{"page": <1-based integer>, "text": <one string: the exact source line(s) copied verbatim, multiple lines joined with \\n>}}. Do not paraphrase or reformat; omit "evidence" if there is no exact source line.
 {build_transcribe_instruction(transcribe_pages)}
 
@@ -121,6 +165,7 @@
     {{
       "date": "YYYY-MM-DD",
       "description": "...",
+      "currency": "USD",
       "lines": [
         {{ "accountId": "...", "direction": "DEBIT", "amount": 0.00, "note": "..." }},
         {{ "accountId": "...", "direction": "CREDIT", "amount": 0.00, "note": "..." }}
@@ -279,8 +324,8 @@
             amount = Decimal(str(line.get('amount')))
         except InvalidOperation:
             return None
-        if not amount.is_finite():
-            return None
+        if not amount.is_finite() or amount < 0 or amount >= MAX_AMOUNT:
+            return None             # direction carries the sign; amounts are never negative
         note = line.get('note', '')
         clean.append({'accountId': account, 'direction': direction, 'amount': amount,
                       'note': note if isinstance(note, str) else ''})
@@ -297,6 +342,7 @@
     lines_table   = dynamodb.Table(LINES_TABLE)
     saved = []
     prepared = []
+    rates_cache = {}    # exchange rates are read at most once per document, and only if needed
 
     # Pass 1: validate and build every item before writing anything. If a malformed entry
     # raised mid-write, the retry would see the file as a duplicate and the rest would be lost.
@@ -309,10 +355,16 @@
             # No amounts or dates in logs: transaction data stays out of CloudWatch.
             print(json.dumps({'event': 'entry_unbalanced_skipped', 'docId': doc_id or file_hash}))
             continue
+        if not entry.get('currency'):
+            print(json.dumps({'event': 'currency_missing_assumed_usd', 'docId': doc_id or file_hash}))
+        currency   = printed_currency(entry)
+        entry_hash = compute_entry_hash(entry, currency)      # printed amounts: rate-independent
+        converted  = convert_to_base(entry, currency, rates_cache)
+        if not converted:
+            print(json.dumps({'event': 'fx_unconverted', 'docId': doc_id or file_hash}))
 
         entry_id   = str(uuid.uuid4())
         date       = entry['date']
-        entry_hash = compute_entry_hash(entry)
         status     = 'DUPLICATE_SUSPECT' if is_duplicate_entry(entry_hash, session_id) else 'PENDING'
 
         item = {
@@ -329,6 +381,11 @@
         }
         if session_id:
             item['sessionId'] = session_id
+        if not converted:
+            # Amounts are as printed, not USD: the Upload page warns and ConfirmLambda requires
+            # an explicit acknowledgement before booking them.
+            item['fxStatus'] = 'unconverted'
+            item['printedCurrency'] = currency
         evidence = []
         if source_type:
             try:
@@ -346,14 +403,19 @@
     for item, lines in prepared:
         entries_table.put_item(Item=item)
         for i, line in enumerate(lines):
-            lines_table.put_item(Item={
+            line_item = {
                 'entryId':   item['entryId'],
                 'lineId':    f'{i:03d}',
                 'accountId': line['accountId'],
                 'direction': line['direction'],
                 'amount':    str(line['amount']),
                 'note':      line['note'],
-            })
+            }
+            if 'originalCurrency' in line:      # same fields ManualEntryLambda writes
+                line_item['originalCurrency'] = line['originalCurrency']
+                line_item['originalAmount']   = str(line['originalAmount'])
+                line_item['exchangeRate']     = str(line['exchangeRate'])
+            lines_table.put_item(Item=line_item)
     return saved
````

Apply to `lambda/manual-entry/index.py`:

````diff
--- a/lambda/manual-entry/index.py
+++ b/lambda/manual-entry/index.py
@@ -136,7 +136,7 @@
             'type':      body['type'],
             'parentId':  body.get('parentId'),
             'isSystem':  False,
-            'currency':  body.get('currency', 'CNY'),
+            'currency':  body.get('currency', 'USD'),
         })
         return {'statusCode': 201, 'headers': CORS, 'body': json.dumps({'accountId': account_id})}
````

Replace `lambda/confirm/index.py` entirely. It is small, and the handler is restructured:

````python
import boto3
import json
import os
from decimal import Decimal, InvalidOperation

from penny_common.session import session_from_headers

dynamodb = boto3.resource('dynamodb')

ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE   = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}
DIRECTIONS = ('DEBIT', 'CREDIT')
MAX_AMOUNT = Decimal('1000000000000')
FX_FIELDS = ('originalCurrency', 'originalAmount', 'exchangeRate')   # same as ParseLambda/ManualEntry


def _response(status, body):
    return {'statusCode': status, 'headers': CORS, 'body': json.dumps(body)}


def lines_balance(lines: list) -> bool:
    """Exact Decimal comparison: 100.00 vs 99.99 never passes."""
    debit  = sum((Decimal(str(l['amount'])) for l in lines if l['direction'] == 'DEBIT'), Decimal('0'))
    credit = sum((Decimal(str(l['amount'])) for l in lines if l['direction'] == 'CREDIT'), Decimal('0'))
    return debit == credit


def clean_lines(raw) -> list:
    """Validated copies of client-supplied lines, or None if any line is malformed."""
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    out = []
    for line in raw:
        if not isinstance(line, dict):
            return None
        account, direction = line.get('accountId'), line.get('direction')
        if not isinstance(account, str) or not account or direction not in DIRECTIONS:
            return None
        try:
            amount = Decimal(str(line.get('amount')))
        except InvalidOperation:
            return None
        if not amount.is_finite() or amount < 0 or amount >= MAX_AMOUNT:
            return None
        note = line.get('note', '')
        item = {'accountId': account, 'direction': direction, 'amount': amount,
                'note': note if isinstance(note, str) else ''}
        if all(line.get(k) not in (None, '') for k in FX_FIELDS):
            item.update({k: str(line[k]) for k in FX_FIELDS})
        out.append(item)
    return out


def handler(event, context):
    entry_id = (event.get('pathParameters') or {}).get('id', '')
    try:
        session_id = session_from_headers(event.get('headers'))
        body = json.loads(event.get('body') or '{}', parse_float=Decimal)
    except (ValueError, TypeError):
        return _response(400, {'error': 'invalid request'})
    if not isinstance(body, dict):
        return _response(400, {'error': 'invalid request'})

    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    entry = entries_table.get_item(Key={'entryId': entry_id}).get('Item') if entry_id else None
    # Owner entries have no sessionId and owner requests send none: a session can only
    # confirm its own entries.
    if not entry or entry.get('sessionId') != session_id:
        return _response(404, {'error': 'Entry not found'})

    if entry.get('status') == 'CONFIRMED':
        return _response(200, {'message': 'Already confirmed'})

    if entry.get('fxStatus') == 'unconverted' and body.get('acknowledgeUnconverted') is not True:
        return _response(409, {'error': 'Amounts were not converted to USD. Review them, then confirm again.',
                               'fxStatus': 'unconverted'})

    stored = lines_table.query(
        KeyConditionExpression='entryId = :e',
        ExpressionAttributeValues={':e': entry_id},
    ).get('Items', [])

    new_lines = None
    if 'lines' in body:
        new_lines = clean_lines(body['lines'])
        if new_lines is None:
            return _response(400, {'error': 'Invalid lines'})
    if not lines_balance(new_lines if new_lines is not None else stored):
        return _response(400, {'error': 'Debit/credit lines do not balance'})

    if new_lines is not None:
        for line in stored:
            lines_table.delete_item(Key={'entryId': entry_id, 'lineId': line['lineId']})
        for i, line in enumerate(new_lines):
            lines_table.put_item(Item={**line, 'entryId': entry_id, 'lineId': f'{i:03d}',
                                       'amount': str(line['amount'])})

    if isinstance(body.get('description'), str):
        entries_table.update_item(
            Key={'entryId': entry_id},
            UpdateExpression='SET description = :d',
            ExpressionAttributeValues={':d': body['description']},
        )

    entries_table.update_item(
        Key={'entryId': entry_id},
        UpdateExpression='SET #s = :s REMOVE fxStatus',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'CONFIRMED'},
    )

    return _response(200, {'entryId': entry_id, 'status': 'CONFIRMED'})
````

Apply to `lib/finance-stack.ts`:

````diff
--- a/lib/finance-stack.ts
+++ b/lib/finance-stack.ts
@@ -424,6 +424,11 @@
     // /api/exchange-rates — served by queryFn
     apiRoot.addResource('exchange-rates').addMethod('GET', new apigw.LambdaIntegration(queryFn));
     exchangeRates.grantReadData(queryFn);
+    // Non-USD documents are converted at parse time; ParseLambda only ever reads the one rates item.
+    parseFn.addToRolePolicy(new iam.PolicyStatement({
+      actions: ['dynamodb:GetItem'],
+      resources: [exchangeRates.tableArn],
+    }));
     monthlyCache.grantReadData(queryFn);
 
     // /api/push/subscribe — POST to subscribe, DELETE to unsubscribe
````

- [ ] **Step 4: Verify.**

Run: `python -m pytest test/lambda -q` and expect `364 passed`.
Run: `npx tsc --noEmit -p .` and expect no output.
Run: `npx jest` and expect `Tests: 21 passed, 21 total`.
Run: `CI=true npx cdk synth --quiet` and expect exit 0.

- [ ] **Step 5: Commit (user)**

```bash
git add lambda/common/penny_common/fx.py lambda/parse/index.py lambda/manual-entry/index.py lambda/confirm/index.py lib/finance-stack.ts test/finance-stack.test.ts test/lambda/test_penny_fx.py test/lambda/test_parse_currency.py test/lambda/test_confirm.py
git commit -m "feat: book parsed documents in USD with balanced conversion; validate lines on confirm"
```

---

### Task 2: Base currency USD — frontend formatting

No unit tests: this task is a mechanical symbol swap. It is checked by grep and syntax checks, then by the browser smoke test in Task 8.

**Files:**
- Create: `frontend/format.js`
- Modify: `frontend/index.html`, `frontend/index-mobile.html`, `frontend/pages/{dashboard,manual-entry,reports,settings,transactions,upload}.js`

- [ ] **Step 1: Add the formatter.**

Create `frontend/format.js`:

````js
// The ledger's base currency is USD: every page formats money through Money.fmt.
const Money = {
  symbol: '$',
  fmt(v) {
    const n = Number(v);
    const abs = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? '-' : '') + Money.symbol + abs;      // -$12.00, not $-12.00
  },
};
````

- [ ] **Step 2: Apply the diffs.**

Apply to `frontend/index.html`:

````diff
--- a/frontend/index.html
+++ b/frontend/index.html
@@ -8,6 +8,7 @@
   <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
   <script src="config.js"></script>
   <script src="api.js"></script>
+  <script src="format.js"></script>
   <style>
     .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-6; }
     .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
````

Apply to `frontend/index-mobile.html`:

````diff
--- a/frontend/index-mobile.html
+++ b/frontend/index-mobile.html
@@ -8,6 +8,7 @@
   <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
   <script src="config.js"></script>
   <script src="api.js"></script>
+  <script src="format.js"></script>
   <style>
     .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-4; }
     .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
````

Apply to `frontend/pages/dashboard.js`:

````diff
--- a/frontend/pages/dashboard.js
+++ b/frontend/pages/dashboard.js
@@ -28,8 +28,8 @@
     container.innerHTML = alerts.map(a => {
       const name = esc(a.accountName);
       const msg = a.overLimit
-        ? `<strong>${name}</strong> exceeded monthly limit of ¥${a.monthlyLimit.toFixed(2)} — spent ¥${a.currentMonthTotal.toFixed(2)} this month.`
-        : `<strong>${name}</strong> is ${a.percentOverAverage}% above the 6-month average (¥${a.sixMonthAverage.toFixed(2)}/mo) — spent ¥${a.currentMonthTotal.toFixed(2)} this month.`;
+        ? `<strong>${name}</strong> exceeded monthly limit of ${Money.fmt(a.monthlyLimit)} — spent ${Money.fmt(a.currentMonthTotal)} this month.`
+        : `<strong>${name}</strong> is ${a.percentOverAverage}% above the 6-month average (${Money.fmt(a.sixMonthAverage)}/mo) — spent ${Money.fmt(a.currentMonthTotal)} this month.`;
       return `<div class="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900">
         <span class="text-lg">⚠️</span><span>${msg}</span>
       </div>`;
@@ -79,7 +79,7 @@
           tension: 0.3,
         }],
       },
-      options: { scales: { y: { ticks: { callback: v => '¥' + v.toLocaleString() } } } },
+      options: { scales: { y: { ticks: { callback: v => Money.symbol + v.toLocaleString() } } } },
     });
   } catch (e) {
     document.getElementById('dash-loading').textContent = 'Error: ' + e.message;
@@ -87,4 +87,4 @@
 }
 
 const PALETTE = ['#3b82f6','#ef4444','#f59e0b','#10b981','#8b5cf6','#ec4899','#06b6d4','#84cc16'];
-const fmt = v => '¥' + Number(v).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
+const fmt = v => Money.fmt(v);
````

Apply to `frontend/pages/manual-entry.js`:

````diff
--- a/frontend/pages/manual-entry.js
+++ b/frontend/pages/manual-entry.js
@@ -49,7 +49,7 @@
         <div class="mb-4">
           <label class="text-sm text-gray-600 font-medium">Amount</label>
           <div class="mt-1 relative">
-            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">¥</span>
+            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
             <input id="me-amount" type="number" step="0.01" min="0"
               class="w-full border rounded-lg pl-7 pr-3 py-2 text-sm" placeholder="0.00" />
           </div>
@@ -69,7 +69,7 @@
             <div>
               <label class="text-xs text-gray-500">Currency</label>
               <select id="me-orig-currency" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
-                <option>USD</option><option>EUR</option><option>GBP</option><option>JPY</option><option>HKD</option><option>CAD</option><option>AUD</option>
+                <option>CNY</option><option>EUR</option><option>GBP</option><option>JPY</option><option>HKD</option><option>CAD</option><option>AUD</option>
               </select>
             </div>
             <div>
````

Apply to `frontend/pages/reports.js`:

````diff
--- a/frontend/pages/reports.js
+++ b/frontend/pages/reports.js
@@ -59,12 +59,12 @@
         <thead><tr class="text-xs text-gray-400 uppercase"><th class="text-left pb-2">Account</th><th class="text-right pb-2">Amount</th></tr></thead>
         <tbody>
           <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-2 pb-1">Income</td></tr>
-          ${is.income.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-green-600">¥${r.amount.toFixed(2)}</td></tr>`).join('')}
-          <tr class="border-t font-semibold"><td class="pt-2">Total Income</td><td class="text-right text-green-600 pt-2">¥${is.totalIncome.toFixed(2)}</td></tr>
+          ${is.income.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-green-600">${Money.fmt(r.amount)}</td></tr>`).join('')}
+          <tr class="border-t font-semibold"><td class="pt-2">Total Income</td><td class="text-right text-green-600 pt-2">${Money.fmt(is.totalIncome)}</td></tr>
           <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Expenses</td></tr>
-          ${is.expenses.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">¥${r.amount.toFixed(2)}</td></tr>`).join('')}
-          <tr class="border-t font-semibold"><td class="pt-2">Total Expenses</td><td class="text-right text-red-500 pt-2">¥${is.totalExpenses.toFixed(2)}</td></tr>
-          <tr class="border-t-2 font-bold text-lg"><td class="pt-2">Net Income</td><td class="text-right pt-2 ${is.netIncome >= 0 ? 'text-green-600' : 'text-red-500'}">¥${is.netIncome.toFixed(2)}</td></tr>
+          ${is.expenses.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">${Money.fmt(r.amount)}</td></tr>`).join('')}
+          <tr class="border-t font-semibold"><td class="pt-2">Total Expenses</td><td class="text-right text-red-500 pt-2">${Money.fmt(is.totalExpenses)}</td></tr>
+          <tr class="border-t-2 font-bold text-lg"><td class="pt-2">Net Income</td><td class="text-right pt-2 ${is.netIncome >= 0 ? 'text-green-600' : 'text-red-500'}">${Money.fmt(is.netIncome)}</td></tr>
         </tbody>
       </table>`;
 
@@ -73,12 +73,12 @@
         <thead><tr class="text-xs text-gray-400 uppercase"><th class="text-left pb-2">Account</th><th class="text-right pb-2">Balance</th></tr></thead>
         <tbody>
           <tr class="font-semibold text-gray-500"><td colspan="2" class="pb-1">Assets</td></tr>
-          ${bs.assets.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
-          <tr class="border-t font-semibold"><td class="pt-2">Total Assets</td><td class="text-right pt-2">¥${bs.totalAssets.toFixed(2)}</td></tr>
+          ${bs.assets.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">${Money.fmt(r.balance)}</td></tr>`).join('')}
+          <tr class="border-t font-semibold"><td class="pt-2">Total Assets</td><td class="text-right pt-2">${Money.fmt(bs.totalAssets)}</td></tr>
           <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Liabilities</td></tr>
-          ${bs.liabilities.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
+          ${bs.liabilities.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">${Money.fmt(r.balance)}</td></tr>`).join('')}
           <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Equity</td></tr>
-          ${bs.equity.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
+          ${bs.equity.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">${Money.fmt(r.balance)}</td></tr>`).join('')}
         </tbody>
       </table>`;
 
@@ -96,7 +96,7 @@
           tension: 0.3,
         }],
       },
-      options: { scales: { y: { ticks: { callback: v => '¥' + v.toLocaleString() } } } },
+      options: { scales: { y: { ticks: { callback: v => Money.symbol + v.toLocaleString() } } } },
     });
   };
````

Apply to `frontend/pages/settings.js`:

````diff
--- a/frontend/pages/settings.js
+++ b/frontend/pages/settings.js
@@ -49,7 +49,7 @@
               <select id="budget-acct" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
             </div>
             <div>
-              <label class="text-xs text-gray-500">Monthly Limit (¥)</label>
+              <label class="text-xs text-gray-500">Monthly Limit ($)</label>
               <input id="budget-limit" type="number" step="0.01" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="500.00" />
             </div>
             <div class="flex items-end">
@@ -111,7 +111,7 @@
       <div class="flex justify-between items-center py-2 border-b border-gray-50">
         <div>
           <span class="text-sm font-medium text-gray-700">${escHtmlSettings(b.name || b.accountId)}</span>
-          <span class="text-xs text-gray-400 ml-2">¥${parseFloat(b.monthlyLimit).toFixed(2)}/month</span>
+          <span class="text-xs text-gray-400 ml-2">${Money.fmt(parseFloat(b.monthlyLimit))}/month</span>
         </div>
         <button onclick="deleteBudget('${escHtmlSettings(b.accountId)}')" class="text-xs text-red-400 hover:text-red-600">Remove</button>
       </div>`).join('');
````

Apply to `frontend/pages/transactions.js`:

````diff
--- a/frontend/pages/transactions.js
+++ b/frontend/pages/transactions.js
@@ -52,7 +52,7 @@
         <div class="mb-3">
           <label class="text-sm text-gray-600 font-medium">Amount</label>
           <div class="mt-1 relative">
-            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">¥</span>
+            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
             <input id="edit-amount" type="number" step="0.01" min="0" class="w-full border rounded-lg pl-7 pr-3 py-2 text-sm" />
           </div>
         </div>
@@ -194,10 +194,10 @@
     list.innerHTML = _entries.map(e => {
       const s = summarize(e);
       const amountHtml = s.type === 'expense'
-        ? `<span class="font-semibold text-red-600">-¥${s.amount.toFixed(2)}</span>`
+        ? `<span class="font-semibold text-red-600">-${Money.fmt(s.amount)}</span>`
         : s.type === 'income'
-        ? `<span class="font-semibold text-green-600">+¥${s.amount.toFixed(2)}</span>`
-        : `<span class="font-semibold text-blue-600">¥${s.amount.toFixed(2)}</span>`;
+        ? `<span class="font-semibold text-green-600">+${Money.fmt(s.amount)}</span>`
+        : `<span class="font-semibold text-blue-600">${Money.fmt(s.amount)}</span>`;
 
       const typeLabel = s.type === 'expense' ? 'Expense' : s.type === 'income' ? 'Income' : 'Transfer';
       const typeBadge = s.type === 'expense' ? 'bg-red-50 text-red-600'
@@ -209,7 +209,7 @@
         <div class="flex justify-between text-sm py-1 border-t border-gray-50">
           <span class="text-gray-600">${acctName(l.accountId)}</span>
           <span class="text-gray-400 text-xs self-center">${l.direction === 'DEBIT' ? '→ Out' : '← In'}</span>
-          <span class="text-gray-800">¥${parseFloat(l.amount).toFixed(2)}${origCurr}</span>
+          <span class="text-gray-800">${Money.fmt(parseFloat(l.amount))}${origCurr}</span>
         </div>`;
       }).join('');
````

Apply to `frontend/pages/upload.js`:

````diff
--- a/frontend/pages/upload.js
+++ b/frontend/pages/upload.js
@@ -58,7 +58,7 @@
         <div class="mb-3">
           <label class="text-sm text-gray-600 font-medium">Amount</label>
           <div class="mt-1 relative">
-            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">¥</span>
+            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
             <input id="ue-amount" type="number" step="0.01" min="0" class="w-full border rounded-lg pl-7 pr-3 py-2 text-sm" />
           </div>
         </div>
@@ -77,7 +77,7 @@
             <div>
               <label class="text-xs text-gray-500">Currency</label>
               <select id="ue-orig-currency" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
-                <option>USD</option><option>EUR</option><option>GBP</option><option>JPY</option><option>HKD</option><option>CAD</option><option>AUD</option>
+                <option>CNY</option><option>EUR</option><option>GBP</option><option>JPY</option><option>HKD</option><option>CAD</option><option>AUD</option>
               </select>
             </div>
             <div>
@@ -174,7 +174,7 @@
     return `
       <td class="py-1 text-gray-700">${acctName(l.accountId)}</td>
       <td class="py-1"><span class="${l.direction === 'DEBIT' ? 'badge-debit' : 'badge-credit'}">${l.direction}</span></td>
-      <td class="py-1 text-right">¥${parseFloat(l.amount).toFixed(2)}</td>
+      <td class="py-1 text-right">${Money.fmt(parseFloat(l.amount))}</td>
       <td class="py-1 text-gray-400 text-xs">${l.note || ''}</td>
       <td class="py-1 text-right whitespace-nowrap">
         <button onclick="editLine('${entryId}', ${i})"
@@ -457,7 +457,7 @@
       document.getElementById('ue-foreign').checked = true;
       document.getElementById('ue-foreign-fields').classList.remove('hidden');
       document.getElementById('ue-orig-amount').value = fxLine.originalAmount || '';
-      document.getElementById('ue-orig-currency').value = fxLine.originalCurrency || 'USD';
+      document.getElementById('ue-orig-currency').value = fxLine.originalCurrency || 'CNY';
       document.getElementById('ue-rate').value = fxLine.exchangeRate || '';
     } else {
       document.getElementById('ue-foreign').checked = false;
````

- [ ] **Step 3: Verify.**

Run: `grep -rn "¥" frontend` and expect no output.
Run: `for f in frontend/*.js frontend/pages/*.js; do node --check "$f" || echo "BAD $f"; done` and expect no output.
Run: `npx jest` and expect `Tests: 21 passed, 21 total`.

- [ ] **Step 4: Commit (user)**

```bash
git add frontend/format.js frontend/index.html frontend/index-mobile.html frontend/pages/dashboard.js frontend/pages/manual-entry.js frontend/pages/reports.js frontend/pages/settings.js frontend/pages/transactions.js frontend/pages/upload.js
git commit -m "feat: show all amounts in USD through one formatter"
```

---

### Task 3: Citation chips and the evidence viewer

**Files:**
- Create: `frontend/citations.js`, `frontend/evidence-viewer.js`, `test/frontend/citations.test.ts`
- Modify: `frontend/pages/advisor.js`, `frontend/pages/transactions.js`, `frontend/index.html`, `frontend/index-mobile.html`

- [ ] **Step 1: Write the failing test.**

Create `test/frontend/citations.test.ts`:

````ts
// frontend/citations.js is a browser script; it also exports itself for these tests.
const Citations = require('../../frontend/citations.js');

const T1 = {
  ref: 'T1', type: 'transaction', entryId: 'e1', date: '2026-03-09', description: 'ABC Utilities',
  amount: '120.00', kind: 'expense', accounts: ['Bank Accounts', 'Other Expenses'],
  evidence: { page: 1, text: '03/09  ABC UTILITIES  -120.00' },
};
const D1 = { ref: 'D1', type: 'document', fileName: 'mar.pdf', page: 1, text: 'NETFLIX.COM -15.49', score: '0.81', chunkKey: 'k' };

describe('renderAnswer', () => {
  test('turns known refs into chips', () => {
    const html = Citations.renderAnswer('You spent $120.00 [T1].', [T1]);
    expect(html).toContain('<button type="button"');
    expect(html).toContain('data-ref="T1">T1</button>');
    expect(html.startsWith('You spent $120.00 ')).toBe(true);
  });

  test('leaves unknown refs as plain text', () => {
    expect(Citations.renderAnswer('See [D9].', [T1])).toBe('See [D9].');
  });

  test('escapes model output before adding markup', () => {
    const html = Citations.renderAnswer('<img src=x onerror=alert(1)> [T1]', [T1]);
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });

  test('keeps line breaks', () => {
    expect(Citations.renderAnswer('a\nb', [])).toBe('a<br>b');
  });

  test('tolerates missing citations and answer', () => {
    expect(Citations.renderAnswer(undefined, undefined)).toBe('');
  });
});

describe('detailHtml', () => {
  test('transaction shows the escaped source line', () => {
    const html = Citations.detailHtml({ ...T1, description: '<b>x</b>' });
    expect(html).toContain('03/09  ABC UTILITIES  -120.00');
    expect(html).toContain('$120.00');
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;');
    expect(html).toContain('Bank Accounts → Other Expenses');
  });

  test('transaction without evidence says so', () => {
    expect(Citations.detailHtml({ ...T1, evidence: null })).toContain('No source line');
  });

  test('document shows file, page and chunk text', () => {
    const html = Citations.detailHtml(D1);
    expect(html).toContain('mar.pdf');
    expect(html).toContain('NETFLIX.COM -15.49');
  });

  test('summaries by month and by account', () => {
    expect(Citations.detailHtml({ ref: 'S1', type: 'summary', groupBy: 'month', month: '2026-03',
      income: '3200.00', expense: '2097.87', net: '1102.13' })).toContain('$1102.13');
    expect(Citations.detailHtml({ ref: 'S2', type: 'summary', groupBy: 'account', accountName: 'Rent',
      amount: '1850.00', period: '2026-03..2026-03' })).toContain('Rent');
  });

  test('missing or unknown citation', () => {
    expect(Citations.detailHtml(undefined)).toContain('not found');
    expect(Citations.detailHtml({ ref: 'X1', type: 'other' })).toContain('Unknown');
  });
});

describe('evidence links', () => {
  test('presigned https link opens the cited page', () => {
    const html = Citations.evidenceHtml([{ page: 2, text: 'line', fileUrl: 'https://s3.example/x?sig=a&b=c' }]);
    expect(html).toContain('href="https://s3.example/x?sig=a&amp;b=c#page=2"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  test('non-https URLs are never linked', () => {
    const html = Citations.evidenceHtml([{ page: 1, text: 'line', fileUrl: 'javascript:alert(1)' }]);
    expect(html).not.toContain('href=');
    expect(html).toContain('Original file not available');
  });

  test('empty evidence', () => {
    expect(Citations.evidenceHtml([])).toContain('No source evidence');
    expect(Citations.sourceLinksHtml(undefined)).toBe('');
  });

  test('sourceLinksHtml omits the quoted text', () => {
    const html = Citations.sourceLinksHtml([{ page: 1, text: 'SECRET LINE', fileUrl: 'https://s3.example/x' }]);
    expect(html).not.toContain('SECRET LINE');
    expect(html).toContain('Open source page 1');
  });
});
````

- [ ] **Step 2: Run it to verify it fails.**

Run: `npx jest test/frontend`
Expected: FAIL with `Cannot find module '../../frontend/citations.js'`.

- [ ] **Step 3: Implement.**

Create `frontend/citations.js`:

````js
// Pure rendering helpers for advisor answers and source evidence (no DOM, no network).
// Model output and document text are untrusted: everything is escaped before it becomes HTML.
const Citations = (() => {
  const REF = /\[([DST]\d+)\]/g;
  const CHIP = 'cite-chip inline-flex items-center px-1.5 mx-0.5 rounded bg-[#f0f4e8] text-[#6a8a3e] '
             + 'text-xs font-semibold hover:bg-[#e2ebd3] align-baseline';

  function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Only presigned https links are ever rendered as hrefs (never javascript: or data: URLs).
  function safeUrl(url) {
    return typeof url === 'string' && url.startsWith('https://') ? url : null;
  }

  // Escaped answer with each known [T1]/[D2]/[S3] ref turned into a chip; unknown refs stay text.
  function renderAnswer(answer, citations) {
    const known = new Set((citations || []).map(c => c.ref));
    return esc(answer)
      .replace(REF, (m, ref) => known.has(ref)
        ? `<button type="button" class="${CHIP}" data-ref="${ref}">${ref}</button>` : m)
      .replace(/\n/g, '<br>');
  }

  function money(v) {
    return '$' + esc(v);   // amounts arrive as exact decimal strings from the API
  }

  function quote(text) {
    return `<pre class="whitespace-pre-wrap bg-gray-50 border rounded-lg p-3 text-xs text-gray-700 font-mono">${esc(text)}</pre>`;
  }

  function row(label, value) {
    return `<div class="flex gap-2 text-sm"><span class="text-gray-400 w-24 shrink-0">${esc(label)}</span>`
         + `<span class="text-gray-800">${value}</span></div>`;
  }

  // Viewer body for one advisor citation.
  function detailHtml(c) {
    if (!c) return '<p class="text-sm text-gray-500">Citation not found.</p>';
    if (c.type === 'transaction') {
      return [
        row('Transaction', esc(c.description)),
        row('Date', esc(c.date)),
        row('Amount', money(c.amount)),
        row('Accounts', esc((c.accounts || []).join(' → '))),
        c.evidence ? row('Source', `page ${esc(c.evidence.page)}`) + quote(c.evidence.text)
                   : '<p class="text-sm text-gray-400">No source line was recorded for this entry.</p>',
      ].join('');
    }
    if (c.type === 'document') {
      return row('Document', esc(c.fileName)) + row('Page', esc(c.page)) + row('Match', esc(c.score)) + quote(c.text);
    }
    if (c.type === 'summary' && c.groupBy === 'month') {
      return row('Month', esc(c.month)) + row('Income', money(c.income)) + row('Expense', money(c.expense))
           + row('Net', money(c.net));
    }
    if (c.type === 'summary') {
      return row('Account', esc(c.accountName)) + row('Period', esc(c.period)) + row('Spent', money(c.amount));
    }
    return '<p class="text-sm text-gray-500">Unknown citation type.</p>';
  }

  function linkHtml(ev) {
    const url = safeUrl(ev.fileUrl);
    return url
      ? `<a href="${esc(url)}#page=${encodeURIComponent(ev.page)}" target="_blank" rel="noopener noreferrer"`
        + ` class="text-sm text-[#6a8a3e] underline">Open source page ${esc(ev.page)} ↗</a>`
      : '<span class="text-xs text-gray-400">Original file not available.</span>';
  }

  // Viewer body for GET /api/entries/{id}/evidence.
  function evidenceHtml(evidence) {
    if (!evidence || !evidence.length) {
      return '<p class="text-sm text-gray-400">No source evidence was recorded for this entry.</p>';
    }
    return evidence.map(ev => `<div class="mb-4">${row('Page', esc(ev.page))}${quote(ev.text)}${linkHtml(ev)}</div>`).join('');
  }

  // Just the links to the original file (a transaction citation already shows the quoted line).
  function sourceLinksHtml(evidence) {
    return (evidence || []).map(ev => `<div>${linkHtml(ev)}</div>`).join('');
  }

  return { esc, safeUrl, renderAnswer, detailHtml, evidenceHtml, sourceLinksHtml };
})();

if (typeof module !== 'undefined') module.exports = Citations;   // jest
````

Create `frontend/evidence-viewer.js`:

````js
// One modal shared by the Transactions page ("View source evidence") and advisor citation chips.
const EvidenceViewer = (() => {
  function modal() {
    let el = document.getElementById('evidence-modal');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'evidence-modal';
    el.className = 'fixed inset-0 bg-black/40 z-50 hidden items-center justify-center p-4';
    el.innerHTML = `
      <div class="bg-white rounded-xl shadow-lg w-full max-w-lg max-h-[80vh] overflow-y-auto p-6">
        <div class="flex justify-between items-center mb-4">
          <h2 id="evidence-title" class="font-semibold text-gray-900"></h2>
          <button type="button" id="evidence-close" class="text-gray-400 hover:text-gray-600 text-xl" aria-label="Close">×</button>
        </div>
        <div id="evidence-body"></div>
      </div>`;
    el.addEventListener('click', e => { if (e.target === el || e.target.id === 'evidence-close') close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    window.addEventListener('hashchange', close);    // the modal lives outside #app, so close it on navigation
    document.body.appendChild(el);
    return el;
  }

  function show(title, bodyHtml) {
    const el = modal();
    document.getElementById('evidence-title').textContent = title;
    document.getElementById('evidence-body').innerHTML = bodyHtml;
    el.classList.remove('hidden');
    el.classList.add('flex');
  }

  function close() {
    const el = document.getElementById('evidence-modal');
    if (el) { el.classList.add('hidden'); el.classList.remove('flex'); }
  }

  async function fetchEvidence(entryId, render) {
    try {
      const res = await API.get(`/api/entries/${encodeURIComponent(entryId)}/evidence`);
      return render(res.evidence);
    } catch (e) {
      return `<p class="text-sm text-red-600">Could not load evidence: ${Citations.esc(e.message)}</p>`;
    }
  }

  async function openEntry(entryId) {
    show('Source evidence', '<p class="text-sm text-gray-400">Loading…</p>');
    show('Source evidence', await fetchEvidence(entryId, Citations.evidenceHtml));
  }

  // A transaction chip already quotes its source line; fetch only the presigned link to the file.
  async function openCitation(citation) {
    const title = citation ? `Citation ${citation.ref}` : 'Citation';
    const detail = Citations.detailHtml(citation);
    if (!citation || citation.type !== 'transaction' || !citation.evidence) return show(title, detail);
    show(title, detail + '<p class="text-sm text-gray-400 mt-3">Loading source file…</p>');
    const links = await fetchEvidence(citation.entryId, Citations.sourceLinksHtml);
    show(title, detail + `<div class="mt-3">${links}</div>`);
  }

  return { openEntry, openCitation, close };
})();
````

Apply to `frontend/pages/advisor.js`:

````diff
--- a/frontend/pages/advisor.js
+++ b/frontend/pages/advisor.js
@@ -2,7 +2,7 @@
   app.innerHTML = `
     <div class="max-w-2xl mx-auto">
       <h1 class="text-2xl font-bold text-gray-900 mb-2">AI Financial Advisor</h1>
-      <p class="text-sm text-gray-500 mb-6">Ask questions about your finances in plain language. Your data from the last 3 months is used as context.</p>
+      <p class="text-sm text-gray-500 mb-6">Ask questions about your finances in plain language. Every figure links to its source: click a citation to see the transaction or document it came from.</p>
 
       <div class="card mb-4" style="min-height: 400px; max-height: 600px; overflow-y: auto;" id="chat-history">
         <div class="text-center text-gray-400 text-sm py-16" id="chat-placeholder">
@@ -34,23 +34,33 @@
       </div>
     </div>`;
 
-  function escHtml(s) {
-    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
-  }
+  const citationsByMessage = new Map();   // message id -> citations array, for chip clicks
+  let messageSeq = 0;
 
-  function appendMessage(role, text) {
+  document.getElementById('chat-history').addEventListener('click', e => {
+    const chip = e.target.closest('.cite-chip');
+    if (!chip) return;
+    const citations = citationsByMessage.get(chip.closest('[data-msg]')?.dataset.msg) || [];
+    EvidenceViewer.openCitation(citations.find(c => c.ref === chip.dataset.ref));
+  });
+
+  // html must already be safe: user text is escaped, advisor text goes through Citations.renderAnswer.
+  function appendMessage(role, html, citations) {
     const history = document.getElementById('chat-history');
     const placeholder = document.getElementById('chat-placeholder');
     if (placeholder) placeholder.remove();
 
     const isUser = role === 'user';
+    const id = String(++messageSeq);
+    if (citations) citationsByMessage.set(id, citations);
     const div = document.createElement('div');
+    div.dataset.msg = id;
     div.className = `flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`;
     div.innerHTML = `
       <div class="max-w-lg px-4 py-3 rounded-2xl text-sm ${isUser
         ? 'bg-[#8aaa5e] text-white rounded-br-sm'
         : 'bg-gray-100 text-gray-800 rounded-bl-sm'}">
-        ${(isUser ? escHtml(text) : text).replace(/\n/g, '<br>')}
+        ${html}
       </div>`;
     history.appendChild(div);
     history.scrollTop = history.scrollHeight;
@@ -67,7 +77,7 @@
     sendBtn.disabled = true;
     sendBtn.textContent = '...';
 
-    appendMessage('user', question);
+    appendMessage('user', Citations.esc(question));
 
     // Thinking indicator
     const history = document.getElementById('chat-history');
@@ -81,10 +91,15 @@
     try {
       const res = await API.post('/api/advisor', { question });
       document.getElementById('thinking')?.remove();
-      appendMessage('advisor', res.answer || 'No response.');
+      const notes = [];
+      if (res.evidenceStatus === 'unsupported') notes.push('Some figures in this answer have no cited source.');
+      if (res.truncated) notes.push('The lookup was cut short; the answer may be incomplete.');
+      appendMessage('advisor', Citations.renderAnswer(res.answer || 'No response.', res.citations)
+        + notes.map(n => `<p class="mt-2 text-xs text-amber-700">${Citations.esc(n)}</p>`).join(''),
+        res.citations || []);
     } catch (e) {
       document.getElementById('thinking')?.remove();
-      appendMessage('advisor', `Error: ${e.message}`);
+      appendMessage('advisor', Citations.esc(`Error: ${e.message}`));
     } finally {
       input.disabled = false;
       sendBtn.disabled = false;
````

Apply to `frontend/pages/transactions.js`:

````diff
--- a/frontend/pages/transactions.js
+++ b/frontend/pages/transactions.js
@@ -223,7 +223,7 @@
                 <span class="text-xs font-medium px-2 py-0.5 rounded-full ${typeBadge}">${typeLabel}</span>
                 <span class="text-xs text-gray-400">${s.category}</span>
               </div>
-              <p class="font-medium text-gray-800 truncate">${e.description}</p>
+              <p class="font-medium text-gray-800 truncate">${escHtml(e.description)}</p>
               ${(e.tags || []).length ? `<div class="flex flex-wrap gap-1 mt-1">${(e.tags||[]).map(t => `<span class="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">#${escHtml(t)}</span>`).join('')}</div>` : ''}
               <p class="text-xs text-gray-400">${e.date} · ${e.source}</p>
             </div>
@@ -233,7 +233,9 @@
               <button onclick="event.stopPropagation(); deleteEntry('${e.entryId}')" class="text-xs text-red-400 hover:text-red-600">Delete</button>
             </div>
           </div>
-          <div id="detail-${e.entryId}" class="hidden mt-2">${detailLines}</div>
+          <div id="detail-${e.entryId}" class="hidden mt-2">${detailLines}${(e.evidence || []).length
+            ? `<button type="button" onclick="EvidenceViewer.openEntry('${e.entryId}')" class="mt-2 text-xs text-[#6a8a3e] hover:underline">View source evidence →</button>`
+            : ''}</div>
         </div>`;
     }).join('');
   }
````

Apply to `frontend/index.html`:

````diff
--- a/frontend/index.html
+++ b/frontend/index.html
@@ -9,6 +9,8 @@
   <script src="config.js"></script>
   <script src="api.js"></script>
   <script src="format.js"></script>
+  <script src="citations.js"></script>
+  <script src="evidence-viewer.js"></script>
   <style>
     .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-6; }
     .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
````

Apply to `frontend/index-mobile.html`:

````diff
--- a/frontend/index-mobile.html
+++ b/frontend/index-mobile.html
@@ -9,6 +9,8 @@
   <script src="config.js"></script>
   <script src="api.js"></script>
   <script src="format.js"></script>
+  <script src="citations.js"></script>
+  <script src="evidence-viewer.js"></script>
   <style>
     .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-4; }
     .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
````

- [ ] **Step 4: Verify.**

Run: `npx jest` and expect `Tests: 35 passed, 35 total`.
Run: `npx tsc --noEmit -p .` and expect no output.
Run: `for f in frontend/*.js frontend/pages/*.js; do node --check "$f" || echo "BAD $f"; done` and expect no output.

- [ ] **Step 5: Commit (user)**

```bash
git add frontend/citations.js frontend/evidence-viewer.js frontend/pages/advisor.js frontend/pages/transactions.js frontend/index.html frontend/index-mobile.html test/frontend/citations.test.ts
git commit -m "feat: clickable advisor citations and a shared source-evidence viewer; escape model text"
```

---

### Task 4: Upload page waits for the real parse result

The page used to wait a fixed 20 s and then report "Parsing complete" whether or not parsing had finished. Now it polls for PENDING entries whose `fileKey` matches the upload. It finishes once their count holds steady for one more poll, because entries are written one by one, or after 2 minutes. A file uploaded before is skipped as a duplicate, so it always ends in the timeout message. The same task:
- escapes pending-entry text;
- shows original-currency amounts;
- shows the `fxStatus: "unconverted"` warning, including the printed currency;
- asks for explicit acknowledgement before confirming such an entry (ConfirmLambda otherwise answers 409);
- makes Confirm All skip and count entries that still need review, instead of failing halfway.

**Files:**
- Create: `frontend/upload-poll.js`, `test/frontend/upload-poll.test.ts`
- Modify: `frontend/pages/upload.js`, `frontend/index.html`, `frontend/index-mobile.html`

- [ ] **Step 1: Write the failing test.**

Create `test/frontend/upload-poll.test.ts`:

````ts
// frontend/upload-poll.js is a browser script; it also exports itself for these tests.
const UploadPoll = require('../../frontend/upload-poll.js');

const KEY = 'uploads/abc-mar.pdf';

// Replays one response per poll and advances a fake clock by each sleep.
function harness(responses: any[]) {
  let t = 0;
  let calls = 0;
  const fetchPending = async () => {
    const r = responses[Math.min(calls++, responses.length - 1)];
    if (r instanceof Error) throw r;
    return r;
  };
  const opts = { intervalMs: 3000, timeoutMs: 12000, sleep: async (ms: number) => { t += ms; }, now: () => t };
  return { fetchPending, opts, calls: () => calls };
}

const entry = (id: string, fileKey = KEY) => ({ entryId: id, fileKey });

test('waits until this file has entries and the count is stable', async () => {
  const h = harness([[], [entry('a')], [entry('a'), entry('b')], [entry('a'), entry('b')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('parsed');
  expect(res.entries.map((e: any) => e.entryId)).toEqual(['a', 'b']);
  expect(h.calls()).toBe(4);
});

test('ignores pending entries from other files', async () => {
  const h = harness([[entry('x', 'uploads/other.pdf')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res).toEqual({ status: 'timeout', entries: [] });
});

test('times out when nothing appears (e.g. duplicate file skipped)', async () => {
  const h = harness([[]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('timeout');
  expect(h.calls()).toBe(5);    // t = 0, 3, 6, 9, 12 s
});

test('a transient API error is retried on the next poll', async () => {
  const h = harness([new Error('503'), [entry('a')], [entry('a')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('parsed');
});

test('entries still arriving at the deadline are returned, not reported as a timeout', async () => {
  const h = harness([[], [], [entry('a')], [entry('a'), entry('b')], [entry('a'), entry('b'), entry('c')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('parsed');
  expect(res.entries).toHaveLength(3);
});
````

- [ ] **Step 2: Run it to verify it fails.**

Run: `npx jest test/frontend/upload-poll.test.ts`
Expected: FAIL with `Cannot find module '../../frontend/upload-poll.js'`.

- [ ] **Step 3: Implement.**

Create `frontend/upload-poll.js`:

````js
// Waits for ParseLambda to finish writing the entries of one uploaded file.
const UploadPoll = (() => {
  const realSleep = ms => new Promise(r => setTimeout(r, ms));

  // Resolves {status: 'parsed', entries} once the file's PENDING entries appear and their count
  // holds steady for one more poll (entries are written one by one), or {status: 'timeout'}.
  // A file uploaded before is skipped by ParseLambda as a duplicate, so it always times out.
  async function waitForParsedEntries(fetchPending, fileKey,
      { intervalMs = 3000, timeoutMs = 120000, sleep = realSleep, now = () => Date.now() } = {}) {
    const deadline = now() + timeoutMs;
    let lastCount = 0;
    for (;;) {
      let pending = null;
      try { pending = await fetchPending(); } catch (e) { /* transient: try again next poll */ }
      const mine = (Array.isArray(pending) ? pending : []).filter(e => e.fileKey === fileKey);
      if (mine.length && mine.length === lastCount) return { status: 'parsed', entries: mine };
      lastCount = mine.length;
      if (now() >= deadline) {
        return mine.length ? { status: 'parsed', entries: mine } : { status: 'timeout', entries: [] };
      }
      await sleep(intervalMs);
    }
  }

  return { waitForParsedEntries };
})();

if (typeof module !== 'undefined') module.exports = UploadPoll;   // jest
````

Apply to `frontend/pages/upload.js`:

````diff
--- a/frontend/pages/upload.js
+++ b/frontend/pages/upload.js
@@ -174,8 +174,9 @@
     return `
       <td class="py-1 text-gray-700">${acctName(l.accountId)}</td>
       <td class="py-1"><span class="${l.direction === 'DEBIT' ? 'badge-debit' : 'badge-credit'}">${l.direction}</span></td>
-      <td class="py-1 text-right">${Money.fmt(parseFloat(l.amount))}</td>
-      <td class="py-1 text-gray-400 text-xs">${l.note || ''}</td>
+      <td class="py-1 text-right">${Money.fmt(parseFloat(l.amount))}${l.originalCurrency
+        ? `<div class="text-xs text-gray-400">${Citations.esc(l.originalCurrency)} ${Citations.esc(l.originalAmount)}</div>` : ''}</td>
+      <td class="py-1 text-gray-400 text-xs">${Citations.esc(l.note || '')}</td>
       <td class="py-1 text-right whitespace-nowrap">
         <button onclick="editLine('${entryId}', ${i})"
           class="text-gray-300 hover:text-blue-500 transition-colors px-1" title="Edit this line">✏️</button>
@@ -254,11 +255,17 @@
     try {
       const file = await normalizeFile(rawFile);
       if (file !== rawFile) showStatus(`Converting iPhone photo to JPEG…`);
-      await API.uploadFile(file.name, file.type, file);
-      showStatus('File uploaded. Parsing with Claude AI… this may take 20–30 seconds.', 'text-blue-600');
-      await new Promise(r => setTimeout(r, 20000));
+      const key = await API.uploadFile(file.name, file.type, file);
+      showStatus('File uploaded. Parsing with Claude AI… this usually takes 20–60 seconds.', 'text-blue-600');
+      const result = await UploadPoll.waitForParsedEntries(() => API.get('/api/entries?status=PENDING'), key);
       await loadPending();
-      showStatus('Parsing complete. Review entries below.', 'text-green-600');
+      if (result.status === 'parsed') {
+        const n = result.entries.length;
+        showStatus(`Parsing complete: ${n} ${n === 1 ? 'entry' : 'entries'} ready to review below.`, 'text-green-600');
+      } else {
+        showStatus('No entries from this file yet. If it was uploaded before, it was skipped as a duplicate; '
+                 + 'otherwise check back in a minute.', 'text-amber-600');
+      }
     } catch (e) {
       showStatus('Error: ' + e.message, 'text-red-600');
     }
@@ -277,9 +284,14 @@
           <span>⚠️</span>
           <span><strong>Possible duplicate</strong> — a similar transaction already exists. Review before confirming.</span>
         </div>` : ''}
+        ${e.fxStatus === 'unconverted' ? `
+        <div class="flex items-center gap-2 mb-3 px-3 py-2 bg-amber-50 rounded-lg text-sm text-amber-800">
+          <span>💱</span>
+          <span><strong>Not converted to USD</strong> — amounts are as printed${e.printedCurrency ? ` (${Citations.esc(e.printedCurrency)})` : ''}. Edit them to USD before confirming.</span>
+        </div>` : ''}
         <div class="flex justify-between items-start mb-3">
           <div>
-            <p class="font-medium text-gray-800">${e.date} — ${e.description}</p>
+            <p class="font-medium text-gray-800">${Citations.esc(e.date)} — ${Citations.esc(e.description)}</p>
             <span class="text-xs text-gray-400 uppercase tracking-wide">${e.source}</span>
           </div>
           <div class="flex gap-2">
@@ -380,7 +392,17 @@
     const entry = (window._pendingEntries || []).find(e => e.entryId === id);
     // Pass current (possibly edited) lines so ConfirmLambda uses them
     const body = entry?.lines ? { lines: entry.lines } : {};
-    await API.put(`/api/entries/${id}/confirm`, body);
+    if (entry?.fxStatus === 'unconverted') {
+      const cur = entry.printedCurrency || 'a foreign currency';
+      if (!confirm(`These amounts are in ${cur} and were not converted to USD. Book them as USD anyway?`)) return;
+      body.acknowledgeUnconverted = true;
+    }
+    try {
+      await API.put(`/api/entries/${id}/confirm`, body);
+    } catch (e) {
+      showStatus('Could not confirm: ' + e.message, 'text-red-600');
+      return;
+    }
     document.getElementById(`entry-${id}`)?.remove();
     const remaining = document.querySelectorAll('[id^="entry-"]');
     if (!remaining.length) document.getElementById('pending-section').classList.add('hidden');
@@ -396,13 +418,22 @@
 
   document.getElementById('confirm-all').onclick = async () => {
     const cards = document.querySelectorAll('[id^="entry-"]');
+    let skipped = 0;
     for (const card of cards) {
       const id = card.id.replace('entry-', '');
-      await API.put(`/api/entries/${id}/confirm`);
-      card.remove();
+      try {
+        await API.put(`/api/entries/${id}/confirm`);   // unconverted entries answer 409 and stay
+        card.remove();
+      } catch (e) {
+        skipped++;
+      }
     }
-    document.getElementById('pending-section').classList.add('hidden');
-    showStatus('All entries confirmed.', 'text-green-600');
+    if (!skipped) {
+      document.getElementById('pending-section').classList.add('hidden');
+      showStatus('All entries confirmed.', 'text-green-600');
+    } else {
+      showStatus(`${skipped} ${skipped === 1 ? 'entry needs' : 'entries need'} review before confirming.`, 'text-amber-600');
+    }
   };
 
   // Edit modal logic
````

Apply to `frontend/index.html`:

````diff
--- a/frontend/index.html
+++ b/frontend/index.html
@@ -11,6 +11,7 @@
   <script src="format.js"></script>
   <script src="citations.js"></script>
   <script src="evidence-viewer.js"></script>
+  <script src="upload-poll.js"></script>
   <style>
     .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-6; }
     .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
````

Apply to `frontend/index-mobile.html`:

````diff
--- a/frontend/index-mobile.html
+++ b/frontend/index-mobile.html
@@ -11,6 +11,7 @@
   <script src="format.js"></script>
   <script src="citations.js"></script>
   <script src="evidence-viewer.js"></script>
+  <script src="upload-poll.js"></script>
   <style>
     .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-4; }
     .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
````

- [ ] **Step 4: Verify.**

Run: `npx jest` and expect `Tests: 40 passed, 40 total`.
Run: `for f in frontend/*.js frontend/pages/*.js; do node --check "$f" || echo "BAD $f"; done` and expect no output.

- [ ] **Step 5: Commit (user)**

```bash
git add frontend/upload-poll.js frontend/pages/upload.js frontend/index.html frontend/index-mobile.html test/frontend/upload-poll.test.ts
git commit -m "fix: upload page waits for the file's parsed entries instead of a fixed 20 s"
```

---

### Task 5: Move `playwright` to devDependencies

Playwright is only used by the local poster-export script; it is not a runtime dependency.

- [ ] **Step 1: Apply the diff.**

Apply to `package.json`:

````diff
--- a/package.json
+++ b/package.json
@@ -11,8 +11,7 @@
     "@aws-sdk/client-dynamodb": "^3.1024.0",
     "@aws-sdk/lib-dynamodb": "^3.1024.0",
     "aws-cdk-lib": "^2.242.0",
-    "constructs": "^10.5.0",
-    "playwright": "^1.59.1"
+    "constructs": "^10.5.0"
   },
   "devDependencies": {
     "@types/jest": "^30",
@@ -20,6 +19,7 @@
     "aws-cdk": "^2.242.0",
     "aws-sdk-client-mock": "^4.1.0",
     "jest": "^30",
+    "playwright": "^1.59.1",
     "ts-jest": "^29",
     "ts-node": "^10.9.2",
     "typescript": "~5.9.3"
````

- [ ] **Step 2: Refresh the lockfile.**

Run: `npm install --package-lock-only --ignore-scripts`
Expected: `package-lock.json` changes in exactly these places:
- `playwright` moves from `dependencies` to `devDependencies` in the root package entry;
- `"dev": true` is added to `node_modules/playwright`, `node_modules/playwright-core` and `node_modules/playwright/node_modules/fsevents`.

- [ ] **Step 3: Verify.**

Run: `npx jest` and expect `Tests: 40 passed, 40 total`.

- [ ] **Step 4: Commit (user)**

```bash
git add package.json package-lock.json
git commit -m "chore: move playwright to devDependencies"
```

---

### Task 6: Eval dataset (synthetic, deterministic)

The corpus is 3 text-layer bank statements (January–March 2026) and 2 PNG receipts. Everything is fake: the account number `0000123456789012` is masked to `****9012` by the pipeline, and every document says SYNTHETIC SAMPLE. One seeded generator writes:
- the files;
- `gold_transactions.json`: every statement line, with its date, amount and page;
- `queries.json`: 20 questions across summary, transaction, document and no-data, each with its expected tools and exact expected amounts or text. Gold amounts are computed from the same rows that are printed, so the data cannot disagree with itself.

**Files:**
- Create: `eval/generate_dataset.py`, `eval/requirements.txt`, `eval/dataset/v1/*` (generated), `test/eval/test_eval_dataset.py`

- [ ] **Step 1: Write the failing test.**

Create `test/eval/test_eval_dataset.py`:

````python
import json
import os
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
D = Decimal


def test_committed_dataset_is_consistent():
    base = os.path.join(ROOT, 'eval', 'dataset', 'v1')
    queries = json.load(open(os.path.join(base, 'queries.json')))
    manifest = json.load(open(os.path.join(base, 'manifest.json')))
    gold = json.load(open(os.path.join(base, 'gold_transactions.json')))
    files = {f['file'] for f in manifest['files']}
    assert all(os.path.exists(os.path.join(base, f)) for f in files)
    assert len({q['id'] for q in queries}) == len(queries) == 20
    for q in queries:
        assert q['expectTools'] or q['expectNoData']
        assert q['goldDoc'] is None or q['goldDoc']['file'] in files
        assert all(D(a) > 0 for a in q['expectAmounts'])
    assert sum(f['entries'] for f in manifest['files']) == len(gold)
````

- [ ] **Step 2: Run it to verify it fails.**

Run: `python -m pytest test/eval -q`
Expected: FAIL with `FileNotFoundError` for `eval/dataset/v1/queries.json`.

- [ ] **Step 3: Implement and generate.**

Create `eval/generate_dataset.py`:

````python
"""Generate the synthetic eval corpus, gold transactions, and questions (deterministic).

    python eval/generate_dataset.py          # writes eval/dataset/v1/

Everything is fake: no real names, accounts, or merchants' real data. The output is committed,
so eval runs are comparable; regenerate only when bumping DATASET_VERSION.
"""
import json
import os
import random
import sys
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'test', 'lambda'))
from pdf_fixtures import make_pdf  # noqa: E402  (tiny dependency-free PDF writer)

DATASET_VERSION = 'v1'
SEED = 20260101
OUT = os.path.join(ROOT, 'eval', 'dataset', DATASET_VERSION)
ACCOUNT_NUMBER = '0000123456789012'          # masked to ****9012 by the pipeline
OPENING_BALANCE = Decimal('2400.00')
MONTHS = [(2026, 1, 'January'), (2026, 2, 'February'), (2026, 3, 'March')]
D = Decimal


def money(v: Decimal) -> str:
    return f'{v:.2f}'


def statement_rows(rng: random.Random, month: int) -> list:
    """(day, description, signed amount) in date order."""
    cents = lambda lo, hi: D(rng.randint(lo, hi)) / 100
    return [
        (2,  'TRADER JOES #552',   -cents(4000, 9000)),
        (5,  'PAYROLL ACME CORP',  D('3200.00')),
        (9,  'ABC UTILITIES',      -cents(9000, 14000)),
        (14, 'NETFLIX.COM',        D('-15.49')),
        (17, 'TRADER JOES #552',   -cents(4000, 9000)),
        (21, 'SHELL OIL 57442',    -cents(3000, 6000)),
        (24, 'BLUE BOTTLE COFFEE', -cents(500, 1500)),
        (28, 'RENT PAYMENT',       D('-1850.00')),
    ]


def statement_line(month: int, day: int, desc: str, amount: Decimal) -> str:
    sign = '+' if amount > 0 else '-'
    return f'{month:02d}/{day:02d}  {desc:<22}  {sign + money(abs(amount)):>9}'


RECEIPTS = [
    {'file': 'receipt_2026_02_hardware.png', 'date': '2026-02-11', 'merchant': 'ACE HARDWARE #1182',
     'items': [('Claw Hammer', D('18.99')), ('Wood Screws 100ct', D('6.49')), ('Masking Tape', D('4.29'))],
     'tax': D('1.86'), 'tip': None},
    {'file': 'receipt_2026_03_cafe.png', 'date': '2026-03-19', 'merchant': 'PENNY EVAL CAFE',
     'items': [('Oat Latte', D('5.25')), ('Butter Croissant', D('4.10'))],
     'tax': D('0.65'), 'tip': D('2.00')},
]


def receipt_lines(r: dict) -> list:
    subtotal = sum(p for _, p in r['items'])
    total = subtotal + r['tax'] + (r['tip'] or 0)
    y, m, d = r['date'].split('-')
    lines = [r['merchant'], f'Date: {m}/{d}/{y}', '-' * 28]
    lines += [f'{name:<20}{money(p):>8}' for name, p in r['items']]
    lines += ['-' * 28, f'{"Subtotal":<20}{money(subtotal):>8}', f'{"Tax":<20}{money(r["tax"]):>8}']
    if r['tip'] is not None:
        lines.append(f'{"Tip":<20}{money(r["tip"]):>8}')
    lines += [f'{"TOTAL":<20}{money(total):>8}', 'VISA ****4242', 'SYNTHETIC SAMPLE - NOT A REAL RECEIPT']
    return lines, total


def render_png(lines: list, path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont   # only needed to regenerate: pip install pillow
    font = ImageFont.load_default(size=28)
    img = Image.new('RGB', (640, 90 + 40 * len(lines)), 'white')
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        y = 40 + 40 * i
        label, _, price = line.rpartition(' ')
        if label.endswith(' ') and price[:1].isdigit():      # "Tax      0.65": right-align the price
            draw.text((40, y), label.strip(), fill='black', font=font)
            draw.text((600, y), price, fill='black', font=font, anchor='ra')
        else:
            draw.text((40, y), line, fill='black', font=font)
    img.save(path, optimize=False)


def build():
    rng = random.Random(SEED)
    os.makedirs(OUT, exist_ok=True)
    gold, files = [], []
    balance = OPENING_BALANCE
    month_spend, month_income = {}, {}
    for year, month, name in MONTHS:
        ym = f'{year}-{month:02d}'
        fname = f'statement_{year}_{month:02d}.pdf'
        rows = statement_rows(rng, month)
        begin = balance
        balance = begin + sum(a for _, _, a in rows)
        text = ['PENNY EVAL BANK - Monthly Statement (SYNTHETIC SAMPLE)',
                f'Account Number: {ACCOUNT_NUMBER}',
                f'Statement Period: {month:02d}/01/{year} - {month:02d}/28/{year}',
                f'Beginning Balance               {money(begin)}',
                'Date   Description               Amount']
        for day, desc, amount in rows:
            line = statement_line(month, day, desc, amount)
            text.append(line)
            gold.append({'file': fname, 'page': 1, 'line': line, 'date': f'{ym}-{day:02d}',
                         'description': desc, 'amount': money(amount)})
        text.append(f'Ending Balance                  {money(balance)}')
        with open(os.path.join(OUT, fname), 'wb') as f:
            f.write(make_pdf([text]))
        files.append({'file': fname, 'kind': 'statement', 'month': ym, 'entries': len(rows),
                      'beginningBalance': money(begin), 'endingBalance': money(balance)})
        month_spend[ym] = -sum(a for _, _, a in rows if a < 0)
        month_income[ym] = sum(a for _, _, a in rows if a > 0)

    for r in RECEIPTS:
        lines, total = receipt_lines(r)
        render_png(lines, os.path.join(OUT, r['file']))
        gold.append({'file': r['file'], 'page': 1, 'line': None, 'date': r['date'],
                     'description': r['merchant'], 'amount': money(-total)})
        files.append({'file': r['file'], 'kind': 'receipt', 'month': r['date'][:7], 'entries': 1})
        month_spend[r['date'][:7]] += total

    by = lambda desc, ym: [g for g in gold if g['description'] == desc and g['date'].startswith(ym)]
    amt = lambda g: money(abs(D(g['amount'])))
    file_of = {f['month']: f for f in files if f['kind'] == 'statement'}
    q = []

    def add(category, question, tools, amounts=(), gold_doc=None, text=None, no_data=False):
        q.append({'id': f'q{len(q) + 1:02d}', 'category': category, 'question': question,
                  'expectTools': list(tools), 'expectAmounts': list(amounts), 'expectText': text,
                  'goldDoc': gold_doc, 'expectNoData': no_data})

    for ym, (_, _, mname) in zip(['2026-01', '2026-02', '2026-03'], MONTHS):
        add('summary', f'How much did I spend in total in {mname} 2026?', ['get_spending_summary'],
            [money(month_spend[ym])])
    add('summary', 'What was my total income in January 2026?', ['get_spending_summary'],
        [money(month_income['2026-01'])])
    add('summary', 'What was my net income (income minus spending) in March 2026?', ['get_spending_summary'],
        [money(month_income['2026-03'] - month_spend['2026-03'])])
    add('summary', 'How much did I spend in total from January through March 2026?', ['get_spending_summary'],
        [money(sum(month_spend.values()))])

    add('transaction', 'How much was my ABC Utilities bill in February 2026?', ['find_transactions'],
        [amt(by('ABC UTILITIES', '2026-02')[0])])
    add('transaction', 'How much was the Netflix charge in March 2026?', ['find_transactions'],
        [amt(by('NETFLIX.COM', '2026-03')[0])])
    add('transaction', 'How much did I spend at Trader Joe\'s in January 2026, in total?', ['find_transactions'],
        [money(sum(abs(D(g['amount'])) for g in by('TRADER JOES #552', '2026-01')))])
    add('transaction', 'How much did I pay for gas at Shell in March 2026?', ['find_transactions'],
        [amt(by('SHELL OIL 57442', '2026-03')[0])])
    add('transaction', 'What was my largest single expense in February 2026?', ['find_transactions'],
        ['1850.00'])
    add('transaction', 'How much did I spend at Ace Hardware?', ['find_transactions'],
        [amt(by('ACE HARDWARE #1182', '2026-02')[0])])

    add('document', 'What is the ending balance on my February 2026 bank statement?', ['search_documents'],
        [file_of['2026-02']['endingBalance']], {'file': 'statement_2026_02.pdf', 'page': 1})
    add('document', 'What was the beginning balance on my March 2026 statement?', ['search_documents'],
        [file_of['2026-03']['beginningBalance']], {'file': 'statement_2026_03.pdf', 'page': 1})
    add('document', 'What are the last four digits of the account number on my bank statements?',
        ['search_documents'], [], {'file': 'statement_2026_01.pdf', 'page': 1}, text='9012')
    add('document', 'How much tip did I leave at the cafe in March?', ['search_documents'],
        ['2.00'], {'file': 'receipt_2026_03_cafe.png', 'page': 1})
    add('document', 'What items did I buy at the hardware store?', ['search_documents'],
        [], {'file': 'receipt_2026_02_hardware.png', 'page': 1}, text='Hammer')
    add('document', 'What does my January statement show for the coffee shop?', ['search_documents'],
        [amt(by('BLUE BOTTLE COFFEE', '2026-01')[0])], {'file': 'statement_2026_01.pdf', 'page': 1})

    add('no_data', 'How much did I spend in December 2025?', [], no_data=True)
    add('no_data', 'What is my credit score?', [], no_data=True)

    manifest = {'datasetVersion': DATASET_VERSION, 'seed': SEED, 'files': files}
    for name, obj in [('manifest.json', manifest), ('gold_transactions.json', gold), ('queries.json', q)]:
        with open(os.path.join(OUT, name), 'w') as f:
            json.dump(obj, f, indent=2)
            f.write('\n')
    print(f'wrote {len(files)} files, {len(gold)} gold transactions, {len(q)} questions to {OUT}')


if __name__ == '__main__':
    build()
````

Create `eval/requirements.txt`:

````text
# Only needed to regenerate the committed dataset (eval/generate_dataset.py).
pillow==12.0.0
````

Run: `python eval/generate_dataset.py`
Expected: `wrote 5 files, 26 gold transactions, 20 questions to …/eval/dataset/v1`.
Run it again and confirm with `md5 eval/dataset/v1/*` that the output is byte-identical; it is deterministic.

- [ ] **Step 4: Verify.**

Run: `python -m pytest test/lambda test/eval -q` and expect `365 passed`.
Open `eval/dataset/v1/receipt_2026_03_cafe.png` and check it is a legible receipt totalling 12.00.

- [ ] **Step 5: Commit (user)**

```bash
git add eval/generate_dataset.py eval/requirements.txt eval/dataset/v1 test/eval/test_eval_dataset.py
git commit -m "feat: deterministic synthetic eval corpus with gold answers"
```

---

### Task 7: Eval metrics and runner

`metrics.py` is pure and fully unit-tested. `run_eval.py` has two commands:
- **`setup`** uploads the corpus to session `eval-v1` through the public API. It waits for parsing until the counts are stable, confirms the entries, then waits until IndexLambda has backfilled every `chunkKey`. Re-running it is safe, because files already uploaded are skipped.
- **`run`** imports `lambda/advisor/index.py` with the deployed function's environment, read via `lambda:GetFunctionConfiguration`. It calls the real handler once per question and model, then scores the answers. It also scores:
  - retrieval, through the agent's own `search_documents` with the same session filter;
  - evidence linking, from `GET /api/entries` in the eval session.

  It writes `eval/results/run-<timestamp>.json` and `docs/evaluation-results.md`.

**Files:**
- Create: `eval/metrics.py`, `eval/run_eval.py`, `eval/results/.gitkeep`, `test/eval/test_eval_metrics.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the failing test.**

Create `test/eval/test_eval_metrics.py`:

````python
import os
import sys
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(ROOT, 'eval'))

import metrics  # noqa: E402

D = Decimal
Q_SPEND = {'id': 'q01', 'category': 'summary', 'expectTools': ['get_spending_summary'],
           'expectAmounts': ['2100.77'], 'expectText': None, 'expectNoData': False}


def test_amounts_in_ignores_years_days_and_percentages_without_cents():
    text = 'In January 2026 you spent $2,100.77 [S1] (up 12% on day 28); rent was 1850.00 and fee 0.5.'
    assert metrics.amounts_in(text) == {D('2100.77'), D('1850.00')}


def test_amounts_in_handles_signs_and_symbols():
    assert metrics.amounts_in('-$15.49 and ¥72.50, total: 3,200.00.') == {D('15.49'), D('72.50'), D('3200.00')}
    assert metrics.amounts_in(None) == set()


def test_score_answer_all_checks_pass():
    resp = {'answer': 'You spent $2,100.77 in January [S1].', 'toolsUsed': ['get_spending_summary'],
            'citations': [{'ref': 'S1'}], 'invalidCitations': 0, 'evidenceStatus': 'supported', 'truncated': False}
    s = metrics.score_answer(Q_SPEND, resp)
    assert s == {'id': 'q01', 'category': 'summary', 'toolOk': True, 'numericOk': True,
                 'citationsValid': True, 'supported': True, 'cited': True, 'truncated': False}


def test_score_answer_wrong_number_and_tool():
    resp = {'answer': 'About $2,000.00.', 'toolsUsed': ['find_transactions'], 'citations': [],
            'invalidCitations': 1, 'evidenceStatus': 'unsupported'}
    s = metrics.score_answer(Q_SPEND, resp)
    assert (s['toolOk'], s['numericOk'], s['citationsValid'], s['supported'], s['cited']) == (False,) * 5


def test_extra_tools_do_not_fail_tool_selection():
    resp = {'answer': '2100.77', 'toolsUsed': ['search_documents', 'get_spending_summary']}
    assert metrics.score_answer(Q_SPEND, resp)['toolOk'] is True


def test_no_data_question_must_not_invent_figures():
    q = {'id': 'q19', 'category': 'no_data', 'expectTools': [], 'expectAmounts': [], 'expectNoData': True}
    assert metrics.score_answer(q, {'answer': "I don't have any data for December 2025."})['numericOk']
    assert metrics.score_answer(q, {'answer': 'You spent $0.00 in December 2025.'})['numericOk']
    assert not metrics.score_answer(q, {'answer': 'You spent $312.40.'})['numericOk']


def test_expect_text_is_case_insensitive():
    q = {'id': 'q15', 'category': 'document', 'expectTools': [], 'expectAmounts': [], 'expectText': '9012'}
    assert metrics.score_answer(q, {'answer': 'It ends in ****9012 [D1].'})['numericOk']
    q2 = {**q, 'expectText': 'Hammer'}
    assert metrics.score_answer(q2, {'answer': 'A claw hammer, screws and tape.'})['numericOk']
    assert not metrics.score_answer(q2, {'answer': 'Screws.'})['numericOk']


def test_rank_and_retrieval_metrics():
    gold = {'file': 'b.pdf', 'page': 1}
    results = [{'fileName': 'a.pdf', 'page': 1}, {'fileName': 'b.pdf', 'page': 2}, {'fileName': 'b.pdf', 'page': 1}]
    assert metrics.rank_of(results, gold) == 3
    assert metrics.rank_of([], gold) is None
    assert metrics.retrieval_metrics([1, 3, None, 6]) == {'n': 4, 'hit@3': 0.5, 'hit@5': 0.5,
                                                          'mrr': round((1 + 1 / 3 + 1 / 6) / 4, 3)}
    assert metrics.retrieval_metrics([])['mrr'] is None


def _entry(date, amount, page=1, text=None):
    e = {'date': date, 'lines': [{'direction': 'DEBIT', 'amount': amount}, {'direction': 'CREDIT', 'amount': amount}]}
    if text is not None:
        e['evidence'] = [{'page': page, 'text': text}]
    return e


def test_evidence_metrics_matches_by_date_and_amount():
    gold = [
        {'file': 's.pdf', 'page': 1, 'line': '01/09  ABC UTILITIES  -90.91', 'date': '2026-01-09', 'amount': '-90.91'},
        {'file': 's.pdf', 'page': 1, 'line': '01/14  NETFLIX.COM  -15.49', 'date': '2026-01-14', 'amount': '-15.49'},
        {'file': 's.pdf', 'page': 1, 'line': '01/28  RENT PAYMENT  -1850.00', 'date': '2026-01-28', 'amount': '-1850.00'},
        {'file': 'r.png', 'page': 1, 'line': None, 'date': '2026-02-11', 'amount': '-31.63'},   # receipt: skipped
    ]
    entries = [
        _entry('2026-01-09', '90.91', text='01/09 ABC UTILITIES -90.91'),       # whitespace differs: ok
        _entry('2026-01-14', '15.49', page=2, text='01/14  NETFLIX.COM  -15.49'),  # wrong page
        _entry('2026-02-11', '31.63'),
    ]
    out = metrics.evidence_metrics(gold, entries)
    assert out == {'n': 3, 'booked': 2, 'bookedRate': 0.667, 'evidenceCorrect': 1, 'evidenceAccuracy': 0.5}


def test_each_entry_matches_at_most_one_gold_line():
    gold = [{'page': 1, 'line': 'a 5.00', 'date': '2026-01-02', 'amount': '-5.00'},
            {'page': 1, 'line': 'b 5.00', 'date': '2026-01-02', 'amount': '-5.00'}]
    out = metrics.evidence_metrics(gold, [_entry('2026-01-02', '5.00', text='a 5.00')])
    assert out['booked'] == 1


def test_percentile_and_summarize():
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([5, 1, 3, 2, 4], 50) == 3
    assert metrics.percentile(list(range(1, 21)), 95) == 19
    scores = [{'toolOk': True, 'numericOk': True, 'citationsValid': True, 'supported': True, 'truncated': False},
              {'toolOk': False, 'numericOk': True, 'citationsValid': True, 'supported': False, 'truncated': True}]
    out = metrics.summarize(scores, ['0.004000', '0.002000'], [2000, 3000])
    assert out['toolSelectionAccuracy'] == 0.5 and out['numericExactness'] == 1.0
    assert out['meanCostUsd'] == '0.003000' and out['totalCostUsd'] == '0.006000'
    assert (out['latencyP50Ms'], out['latencyP95Ms']) == (2000, 3000)


def test_run_eval_maps_upload_keys_to_dataset_files():
    import run_eval
    key = 'uploads/demo-eval-v1/0f8fad5b-d9cb-469f-a165-70867728950e-statement_2026_01.pdf'
    assert run_eval.file_of({'fileKey': key}) == 'statement_2026_01.pdf'
    assert run_eval.file_of({}) == ''


def test_run_eval_report_renders_every_section():
    import run_eval
    summary = metrics.summarize([{'toolOk': True, 'numericOk': True, 'citationsValid': True,
                                  'supported': True, 'truncated': False}], ['0.004000'], [2400])
    q = {'id': 'q01', 'category': 'summary', 'toolOk': True, 'numericOk': True}
    report = {'startedAt': '2026-10-08T00:00:00+00:00', 'datasetVersion': 'v1', 'embedDimensions': '512',
              'vectorIndex': 'penny-docs-v1', 'chunkerVersion': '1',
              'retrieval': metrics.retrieval_metrics([1, 2]),
              'evidence': {'n': 24, 'booked': 24, 'bookedRate': 1.0, 'evidenceCorrect': 23, 'evidenceAccuracy': 0.958},
              'models': {'haiku-4.5': {'modelId': 'h', 'summary': summary, 'questions': [q]},
                         'sonnet-4.6': {'modelId': 's', 'summary': summary,
                                        'questions': [{**q, 'numericOk': False}]}}}
    md = run_eval.render(report, 'eval/results/run-x.json')
    assert '| Numeric exactness | 100% | 100% |' in md
    assert '| Mean cost / question | $0.004000 | $0.004000 |' in md
    assert 'hit@3 100%' in md and '23/24' in md
    assert '**haiku-4.5:** none' in md and '**sonnet-4.6:** q01 (summary)' in md
````

- [ ] **Step 2: Run it to verify it fails.**

Run: `python -m pytest test/eval -q`
Expected: collection error `ModuleNotFoundError: No module named 'metrics'`.

- [ ] **Step 3: Implement.**

Create `eval/metrics.py`:

````python
"""Deterministic eval metrics (no LLM judge). Pure functions: no AWS, no network."""
import re
from decimal import Decimal

# 1,234.56 / 1234.56 / 15.49: two-decimal amounts only, so years and day numbers never count.
_AMOUNT = re.compile(r'(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d{2}(?![\d])|(?<![\d.,])\d+\.\d{2}(?![\d])')
_WS = re.compile(r'\s+')


def amounts_in(text: str) -> set:
    """Every two-decimal amount in the text, as Decimal (sign and currency symbols ignored)."""
    return {Decimal(m.group(0).replace(',', '')) for m in _AMOUNT.finditer(text or '')}


def score_answer(q: dict, resp: dict) -> dict:
    """Per-question checks for one advisor response."""
    answer = resp.get('answer') or ''
    found = amounts_in(answer)
    expected = {Decimal(a) for a in q.get('expectAmounts') or []}
    tools = set(resp.get('toolsUsed') or [])
    if q.get('expectNoData'):
        numeric = not any(a != 0 for a in found)            # "no data" answers must not invent figures
    else:
        numeric = expected <= found
    text_ok = True
    if q.get('expectText'):
        text_ok = q['expectText'].lower() in answer.lower()
    return {
        'id': q['id'],
        'category': q['category'],
        'toolOk': set(q.get('expectTools') or []) <= tools,
        'numericOk': numeric and text_ok,
        'citationsValid': resp.get('invalidCitations', 0) == 0,
        'supported': resp.get('evidenceStatus') == 'supported',
        'cited': bool(resp.get('citations')),
        'truncated': bool(resp.get('truncated')),
    }


def rank_of(results: list, gold: dict):
    """1-based rank of the first retrieved chunk from the gold file and page, else None."""
    for i, r in enumerate(results, start=1):
        if r.get('fileName') == gold['file'] and int(r.get('page') or 0) == gold['page']:
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


def evidence_metrics(gold: list, entries: list) -> dict:
    """Match each gold statement line to the booked entry with the same date and amount, then
    check that the entry's evidence quotes that line on the right page. Receipts (line None)
    are skipped: image evidence is only checked against Claude's own transcript."""
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


def summarize(scores: list, costs: list, latencies_ms: list) -> dict:
    n = len(scores)
    rate = lambda key: round(sum(1 for s in scores if s[key]) / n, 3) if n else None
    total = sum((Decimal(c) for c in costs), Decimal('0'))
    return {
        'n': n,
        'toolSelectionAccuracy': rate('toolOk'),
        'numericExactness': rate('numericOk'),
        'citationValidity': rate('citationsValid'),
        'supportedRate': rate('supported'),
        'truncatedRate': rate('truncated'),
        'meanCostUsd': f'{total / n:.6f}' if n else None,
        'totalCostUsd': f'{total:.6f}',
        'latencyP50Ms': percentile(latencies_ms, 50),
        'latencyP95Ms': percentile(latencies_ms, 95),
    }
````

Create `eval/run_eval.py`:

````python
"""Run Penny's deterministic eval against the deployed stack (manual; needs AWS credentials).

    python eval/run_eval.py setup            # once: upload + confirm the synthetic corpus (~$0.10)
    python eval/run_eval.py run              # 20 questions x 2 models (~$0.40), writes results

Everything lives in its own demo session (X-Session-Id: eval-v1), isolated from owner data.
`run` imports the real AdvisorLambda code and calls its handler locally with the deployed
function's configuration, so both models run the same agent against the same data; only
ADVISOR_MODEL_ID changes. Latency is measured from this machine, not inside Lambda.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import boto3
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics  # noqa: E402

SESSION = 'eval-v1'
STACK = 'FinanceStack'
REGION = 'us-east-1'
DATASET = os.path.join(ROOT, 'eval', 'dataset', 'v1')
RESULTS_DIR = os.path.join(ROOT, 'eval', 'results')
REPORT = os.path.join(ROOT, 'docs', 'evaluation-results.md')
MODELS = {
    'haiku-4.5':  'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    'sonnet-4.6': 'us.anthropic.claude-sonnet-4-6',
}
CONTENT_TYPES = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg'}
POLL_S = 10


def load(name):
    with open(os.path.join(DATASET, name)) as f:
        return json.load(f)


def site_url() -> str:
    outputs = boto3.client('cloudformation', region_name=REGION).describe_stacks(StackName=STACK)['Stacks'][0]['Outputs']
    return next(o['OutputValue'] for o in outputs if o['OutputKey'] == 'SiteUrl').rstrip('/')


def api(site, method, path, body=None):
    r = requests.request(method, f'{site}{path}', json=body, timeout=30, headers={'X-Session-Id': SESSION})
    r.raise_for_status()
    return r.json()


def session_entries(site):
    return api(site, 'GET', '/api/entries?status=PENDING') + api(site, 'GET', '/api/entries')


def file_of(entry) -> str:
    return entry.get('fileKey', '').rsplit('/', 1)[-1].split('-', 5)[-1]   # uploads/demo-sid/<uuid>-<name>


# ── setup ────────────────────────────────────────────────────────────────────

def setup(site):
    manifest = load('manifest.json')
    have = {file_of(e) for e in session_entries(site)}
    for f in manifest['files']:
        if f['file'] in have:
            print(f'{f["file"]}: already uploaded')
            continue
        ext = f['file'].rsplit('.', 1)[-1]
        up = api(site, 'POST', '/api/upload', {'filename': f['file'], 'contentType': CONTENT_TYPES[ext],
                                               'sessionId': SESSION})
        with open(os.path.join(DATASET, f['file']), 'rb') as fh:
            requests.put(up['uploadUrl'], data=fh.read(), headers={'Content-Type': CONTENT_TYPES[ext]},
                         timeout=60).raise_for_status()
        print(f'{f["file"]}: uploaded')

    # Parsing runs asynchronously; wait until every file has entries and the count stops changing.
    deadline, last = time.monotonic() + 600, None
    while True:
        entries = session_entries(site)
        counts = {f['file']: sum(1 for e in entries if file_of(e) == f['file']) for f in manifest['files']}
        if all(counts.values()) and counts == last:
            break
        if time.monotonic() > deadline:
            sys.exit(f'timed out waiting for parsing: {counts}')
        last = counts
        time.sleep(POLL_S)
    for f in manifest['files']:
        flag = '' if counts[f['file']] == f['entries'] else f'  (expected {f["entries"]})'
        print(f'{f["file"]}: {counts[f["file"]]} entries{flag}')

    pending = api(site, 'GET', '/api/entries?status=PENDING')
    for e in pending:
        api(site, 'PUT', f'/api/entries/{e["entryId"]}/confirm', {})
    print(f'confirmed {len(pending)} entries')

    # IndexLambda backfills chunkKey on evidence once the document's vectors exist.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        missing = [e for e in api(site, 'GET', '/api/entries')
                   for ev in e.get('evidence', []) if not ev.get('chunkKey')]
        if not missing:
            print('vectors indexed; setup complete')
            return
        time.sleep(POLL_S)
    print(f'warning: {len(missing)} evidence items still have no chunkKey (check IndexLambda logs / DLQ)')


# ── run ──────────────────────────────────────────────────────────────────────

def load_advisor():
    """Import lambda/advisor/index.py with the deployed function's environment."""
    lam = boto3.client('lambda', region_name=REGION)
    name = next(fn['FunctionName'] for page in lam.get_paginator('list_functions').paginate()
                for fn in page['Functions'] if 'AdvisorLambda' in fn['FunctionName'])
    env = lam.get_function_configuration(FunctionName=name)['Environment']['Variables']
    os.environ.update(env)
    os.environ.setdefault('AWS_DEFAULT_REGION', REGION)
    sys.path.insert(0, os.path.join(ROOT, 'lambda', 'common'))
    spec = importlib.util.spec_from_file_location('advisor_index', os.path.join(ROOT, 'lambda', 'advisor', 'index.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, env


class _LambdaContext:
    aws_request_id = 'eval'

    @staticmethod
    def get_remaining_time_in_millis():
        return 30000


def ask(adv, question):
    event = {'httpMethod': 'POST', 'headers': {'X-Session-Id': SESSION}, 'body': json.dumps({'question': question})}
    started = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()):      # the handler's own JSON log lines
        resp = adv.handler(event, _LambdaContext())
    latency = int((time.monotonic() - started) * 1000)
    body = json.loads(resp['body'])
    if resp['statusCode'] != 200:
        body = {'answer': '', 'error': body.get('error'), 'statusCode': resp['statusCode'], 'invalidCitations': 0}
    return body, latency


def retrieval(adv, queries):
    ranks = []
    for q in queries:
        if not q.get('goldDoc'):
            continue
        ctx = adv.Context(SESSION, {}, deadline=time.monotonic() + 60)
        results = adv.search_documents({'query': q['question'], 'topK': 5}, ctx)
        ranks.append(metrics.rank_of(results, q['goldDoc']))
    return metrics.retrieval_metrics(ranks)


def run(site, model_names, limit):
    queries = load('queries.json')[:limit]
    adv, env = load_advisor()
    report = {'startedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'datasetVersion': load('manifest.json')['datasetVersion'], 'session': SESSION,
              'embedDimensions': env.get('EMBED_DIMENSIONS'), 'vectorIndex': env.get('VECTOR_INDEX'),
              'models': {}}
    from penny_common.chunking import CHUNKER_VERSION
    report['chunkerVersion'] = CHUNKER_VERSION
    report['retrieval'] = retrieval(adv, queries)
    report['evidence'] = metrics.evidence_metrics(load('gold_transactions.json'), api(site, 'GET', '/api/entries'))
    for label in model_names:
        adv.MODEL_ID = MODELS[label]
        rows, costs, latencies = [], [], []
        for q in queries:
            body, latency = ask(adv, q['question'])
            score = metrics.score_answer(q, body)
            cost = (body.get('usage') or {}).get('estCostUsd') or '0'
            rows.append({**score, 'question': q['question'], 'answer': body.get('answer'),
                         'toolsUsed': body.get('toolsUsed'), 'error': body.get('error'),
                         'latencyMs': latency, 'estCostUsd': cost})
            costs.append(cost)
            latencies.append(latency)
            mark = 'ok ' if score['numericOk'] and score['toolOk'] else 'MISS'
            print(f'[{label}] {q["id"]} {mark} {latency:>5} ms  ${cost}')
        report['models'][label] = {'modelId': MODELS[label],
                                   'summary': metrics.summarize(rows, costs, latencies), 'questions': rows}
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = report['startedAt'].replace(':', '').replace('+0000', 'Z')
    raw_path = os.path.join(RESULTS_DIR, f'run-{stamp}.json')
    with open(raw_path, 'w') as f:
        json.dump(report, f, indent=2)
    with open(REPORT, 'w') as f:
        f.write(render(report, os.path.relpath(raw_path, ROOT)))
    print(f'wrote {os.path.relpath(raw_path, ROOT)} and {os.path.relpath(REPORT, ROOT)}')


def _pct(v):
    return '—' if v is None else f'{v * 100:.0f}%'


def render(r, raw_path) -> str:
    models = list(r['models'])
    rows = [('Tool-selection accuracy', 'toolSelectionAccuracy', _pct), ('Numeric exactness', 'numericExactness', _pct),
            ('Citation validity', 'citationValidity', _pct), ('Supported (every figure cited)', 'supportedRate', _pct),
            ('Truncated', 'truncatedRate', _pct), ('Mean cost / question', 'meanCostUsd', lambda v: f'${v}'),
            ('Latency p50', 'latencyP50Ms', lambda v: f'{v / 1000:.1f} s'),
            ('Latency p95', 'latencyP95Ms', lambda v: f'{v / 1000:.1f} s')]
    out = ['# Penny evaluation results', '',
           f'Generated by `eval/run_eval.py` on {r["startedAt"]}. Raw results: `{raw_path}`. '
           'Resume figures come only from this file.', '',
           '| Run metadata | |', '|---|---|',
           f'| Dataset | `{r["datasetVersion"]}` (3 synthetic statements, 2 synthetic receipts, '
           f'{r["models"][models[0]]["summary"]["n"]} questions) |',
           f'| Embeddings | Titan Text Embeddings V2, {r["embedDimensions"]} dims, index `{r["vectorIndex"]}` |',
           f'| Chunker version | `{r["chunkerVersion"]}` |',
           '| Models | ' + ', '.join(f'{m} (`{r["models"][m]["modelId"]}`)' for m in models) + ' |', '',
           '## Agent', '', '| Metric | ' + ' | '.join(models) + ' |', '|---|' + '---|' * len(models)]
    for label, key, fmt in rows:
        out.append(f'| {label} | ' + ' | '.join(fmt(r['models'][m]['summary'][key]) for m in models) + ' |')
    ret, ev = r['retrieval'], r['evidence']
    out += ['', '## Retrieval (search_documents, same session filter as the agent)', '',
            f'{ret["n"]} questions with a gold document: hit@3 {_pct(ret["hit@3"])}, hit@5 {_pct(ret["hit@5"])}, '
            f'MRR {ret["mrr"]}.', '',
            '## Evidence linking (statement lines, text-layer PDFs)', '',
            f'{ev["booked"]}/{ev["n"]} gold lines were booked as entries ({_pct(ev["bookedRate"])}); '
            f'{ev["evidenceCorrect"]}/{ev["booked"]} carry the exact source line on the right page '
            f'({_pct(ev["evidenceAccuracy"])}).', '',
            '## Misses', '']
    for m in models:
        misses = [q for q in r['models'][m]['questions'] if not (q['toolOk'] and q['numericOk'])]
        out.append(f'- **{m}:** ' + (', '.join(f'{q["id"]} ({q["category"]})' for q in misses) or 'none'))
    out += ['', '## Method', '',
            '- Deterministic checks only, no LLM judge. A question passes numeric exactness when every gold '
            'amount appears in the answer (compared as Decimal), or, for no-data questions, when the answer '
            'states no non-zero amount.',
            '- Tool selection passes when the expected tool was among the tools the agent called.',
            '- Not measured in this lean version: the 512 vs 1024-dimension ablation, the old advisor baseline, '
            'and the citation validator\'s false-negative rate.', '']
    return '\n'.join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['setup', 'run'])
    p.add_argument('--site', help='CloudFront URL (default: FinanceStack SiteUrl output)')
    p.add_argument('--models', default=','.join(MODELS), help=f'comma-separated, from: {", ".join(MODELS)}')
    p.add_argument('--limit', type=int, default=None, help='only the first N questions (smoke run)')
    a = p.parse_args()
    site = (a.site or site_url()).rstrip('/')
    if a.command == 'setup':
        setup(site)
    else:
        names = [m.strip() for m in a.models.split(',') if m.strip()]
        unknown = [m for m in names if m not in MODELS]
        if unknown:
            sys.exit(f'unknown model(s): {unknown}')
        run(site, names, a.limit)


if __name__ == '__main__':
    main()
````

Create the empty file `eval/results/.gitkeep`.

Apply to `.github/workflows/ci.yml`:

````diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -28,7 +28,7 @@
             requirements-dev.txt
             lambda/layer/requirements.txt
       - run: pip install -r requirements-dev.txt
-      - run: python -m pytest test/lambda -q
+      - run: python -m pytest test/lambda test/eval -q
 
       - uses: actions/setup-node@v4
         with:
````

- [ ] **Step 4: Verify.**

Run: `python -m pytest test/lambda test/eval -q` and expect `378 passed`.
Run: `python eval/run_eval.py --help` and expect usage text listing `{setup,run}`.

- [ ] **Step 5: Commit (user)**

```bash
git add eval/metrics.py eval/run_eval.py eval/results/.gitkeep test/eval/test_eval_metrics.py .github/workflows/ci.yml
git commit -m "feat: deterministic eval runner (tools, numbers, citations, retrieval, evidence, cost)"
```

---

### Task 8: Deploy, frontend upload, and eval run (user)

`penny_common` gained `fx.py`, so **the layer must be rebuilt**. Docker must be running.

- [ ] **Step 1: Build and deploy the backend**

```bash
./scripts/build-layer.sh
```
```bash
npx cdk deploy
```
Expected changes:
- ParseLambda gains `dynamodb:GetItem` on `finance-exchange-rates`;
- the new layer version;
- updated Lambda code.

- [ ] **Step 2: Upload the frontend.** This repo has no frontend deploy step in CDK. Copy the files to the app bucket without `--delete`: the bucket also holds `uploads/`, `text/` and `manifests/`.

```bash
aws s3 sync frontend/ s3://<BucketName>/ --exclude ".DS_Store" --exclude "*/.gitkeep"
```
```bash
aws cloudfront create-invalidation --distribution-id <DistributionId> --paths "/*"
```
`<BucketName>` and `<DistributionId>` are in the deploy outputs.

- [ ] **Step 3: Browser smoke test** at the SiteUrl:
  - Advisor: ask about March utilities. The answer shows a `T1` chip, and clicking it opens the source line with an "Open source page 1" link.
  - Transactions: expand ABC Utilities and use "View source evidence".
  - Upload: re-upload the March synthetic PDF. It should end with "skipped as a duplicate" after 2 minutes, not a false "complete".
  - Every amount shows `$`.

- [ ] **Step 4: Run the eval** (about $0.50 in total):

```bash
python eval/run_eval.py setup
```
Expected:
- 5 files uploaded;
- per-file entry counts (statements expect 8 each; receipts 1 each);
- `confirmed 26 entries` (or the count actually parsed);
- `vectors indexed; setup complete`.

```bash
python eval/run_eval.py run
```
Expected:
- 40 lines like `[haiku-4.5] q01 ok  2410 ms  $0.004…`;
- then `wrote eval/results/run-….json and docs/evaluation-results.md`.

- [ ] **Step 5: Commit (user)**

```bash
git add docs/evaluation-results.md eval/results
git commit -m "docs: record eval results (Haiku 4.5 vs Sonnet 4.6)"
```

---

### Task 9: README

The README is written **after** Task 8, because its numbers are copied from `docs/evaluation-results.md` and nowhere else. Sections:

1. **What Penny is**, in one paragraph: a serverless double-entry ledger with AI parsing and a cited RAG advisor; synthetic demo data only.
2. **Architecture diagram**: the upload → parse → index → advisor flow from the Plan 1 and Plan 2 PR descriptions.
3. **Design decisions**:
   - evidence vouching validated against pypdf;
   - deterministic citation validation;
   - Decimal money and balanced currency conversion;
   - session isolation;
   - least-privilege IAM;
   - crash-safe indexing.
4. **Cost**: per-document parse cost, per-question advisor cost for Haiku vs Sonnet (from the eval), and idle cost of about $0.
5. **Evaluation**: the agent table, retrieval and evidence numbers, with a link to `docs/evaluation-results.md` and the method's limitations.
6. **Running it**: tests, layer build, deploy, frontend sync, eval.
7. **Known limitations**, from spec §12: no auth (the session header can be spoofed), a synthetic-only public demo, and FX at the latest rate rather than the transaction-date rate.

- [ ] **Step 1:** Draft `README.md` with the numbers filled in from `docs/evaluation-results.md`. The user reviews it.
- [ ] **Step 2: Commit (user)**

```bash
git add README.md
git commit -m "docs: rewrite README around architecture, cost and eval results"
```

---

## Self-Review Notes

- **Spec §5 Frontend:**
  - "View source evidence" → Task 3 (Transactions).
  - Advisor chips reusing the same viewer → Task 3.
- **Spec §9:**
  - Versioned, deterministic synthetic dataset → Task 6.
  - Deterministic metrics, output file and model IDs recorded → Tasks 7 and 8.
  - The deferred items are listed under Decisions.
- **Backlog from Plans 1 and 2:**
  - Upload page polling → Task 4.
  - `playwright` → Task 5.
  - ¥ on USD documents → Tasks 1 and 2.
- **Found and fixed during Task 1 review:** `PUT /api/entries/{id}/confirm` wrote client lines without a balance check, dropped FX fields, and did not check the session. All three are fixed in Task 1.
- **Test counts:**
  - pytest: 303 → 364 (Task 1) → 365 (Task 6) → 378 (Task 7).
  - jest: 20 → 21 (Task 1) → 35 (Task 3) → 40 (Task 4).
