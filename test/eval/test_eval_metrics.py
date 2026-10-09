import os
import sys
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(ROOT, 'eval'))

import metrics  # noqa: E402
import run_eval  # noqa: E402

D = Decimal
Q_SPEND = {'id': 'q01', 'category': 'summary', 'expectTools': ['get_spending_summary'],
           'expectAmounts': ['2100.77'], 'expectText': None, 'expectNoData': False}
Q_NODATA = {'id': 'q19', 'category': 'no_data', 'expectTools': [], 'expectAmounts': [], 'expectNoData': True}
GOOD = {'answer': 'You spent $2,100.77 in January [S1].', 'toolsUsed': ['get_spending_summary'],
        'citations': [{'ref': 'S1'}], 'invalidCitations': 0, 'evidenceStatus': 'supported', 'truncated': False}


def test_amounts_in_ignores_years_days_and_percentages_without_cents():
    text = 'In January 2026 you spent $2,100.77 [S1] (up 12% on day 28); rent was 1850.00 and fee 0.5.'
    assert metrics.amounts_in(text) == {D('2100.77'), D('1850.00')}


def test_amounts_in_handles_signs_and_symbols():
    assert metrics.amounts_in('-$15.49 and ¥72.50, total: 3,200.00.') == {D('15.49'), D('72.50'), D('3200.00')}
    assert metrics.amounts_in(None) == set()


def test_good_answer_passes_every_applicable_check():
    s = metrics.score_answer(Q_SPEND, GOOD)
    assert s == {'id': 'q01', 'category': 'summary', 'ok': True, 'truncated': False, 'toolOk': True,
                 'numericOk': True, 'textOk': None, 'citationsValid': True, 'moneyCited': True}
    assert metrics.passed(s)


def test_wrong_number_and_tool_fail():
    resp = {'answer': 'About $2,000.00.', 'toolsUsed': ['find_transactions'], 'citations': []}
    s = metrics.score_answer(Q_SPEND, resp)
    assert (s['toolOk'], s['numericOk'], s['moneyCited']) == (False, False, False)
    assert s['citationsValid'] is None                       # no citations: not counted either way
    assert not metrics.passed(s)


def test_errors_and_truncation_fail_instead_of_passing():
    err = metrics.score_answer(Q_NODATA, {}, ok=False)
    assert err['numericOk'] is False and not metrics.passed(err)
    assert metrics.score_answer(Q_SPEND, {}, ok=False)['toolOk'] is False
    cut = metrics.score_answer(Q_SPEND, {**GOOD, 'truncated': True})
    assert cut['numericOk'] is False and not metrics.passed(cut)


def test_extra_tools_are_allowed_but_no_tool_questions_are_excluded():
    assert metrics.score_answer(Q_SPEND, {**GOOD, 'toolsUsed': ['search_documents', 'get_spending_summary']})['toolOk']
    assert metrics.score_answer(Q_NODATA, {'answer': 'No data.'})['toolOk'] is None


def test_shotgun_answers_fail_numeric_exactness():
    q = {'id': 'q11', 'category': 'transaction', 'expectTools': [], 'expectAmounts': ['1850.00']}
    listing = 'Your February expenses: 44.10, 121.43, 15.49, 41.76, 37.27, 14.16 and 1850.00.'
    assert not metrics.score_answer(q, {'answer': listing})['numericOk']
    assert metrics.score_answer(q, {'answer': 'Rent, $1,850.00, out of $2,155.84 total.'})['numericOk']


def test_amount_matching_is_whole_token():
    q = {'id': 'q16', 'category': 'document', 'expectTools': [], 'expectAmounts': ['2.00']}
    assert not metrics.score_answer(q, {'answer': 'The total was $12.00.'})['numericOk']
    assert metrics.score_answer(q, {'answer': 'You tipped $2.00 on a $12.00 bill.'})['numericOk']


def test_no_data_question_must_not_invent_figures():
    assert metrics.score_answer(Q_NODATA, {'answer': "I don't have any data for December 2025."})['numericOk']
    assert metrics.score_answer(Q_NODATA, {'answer': 'You spent $0.00 in December 2025.'})['numericOk']
    assert not metrics.score_answer(Q_NODATA, {'answer': 'You spent $312.40.'})['numericOk']
    assert not metrics.score_answer(Q_NODATA, {'answer': 'Roughly $2,100 that month.'})['numericOk']


def test_text_match_is_its_own_case_insensitive_check():
    q = {'id': 'q17', 'category': 'document', 'expectTools': [], 'expectAmounts': [], 'expectText': 'Hammer'}
    s = metrics.score_answer(q, {'answer': 'A claw hammer, screws and tape.'})
    assert (s['textOk'], s['numericOk']) == (True, None)
    assert not metrics.score_answer(q, {'answer': 'Screws.'})['textOk']


def test_rank_and_retrieval_metrics():
    gold = [{'file': 'b.pdf', 'page': 1}]
    results = [{'fileName': 'a.pdf', 'page': 1}, {'fileName': 'b.pdf', 'page': 2}, {'fileName': 'b.pdf', 'page': 1}]
    assert metrics.rank_of(results, gold) == 3
    assert metrics.rank_of(results, gold + [{'file': 'a.pdf', 'page': 1}]) == 1   # any gold doc counts
    assert metrics.rank_of([], gold) is None
    assert metrics.retrieval_metrics([1, 3, None, 6]) == {'n': 4, 'hit@3': 0.5, 'hit@5': 0.5,
                                                          'mrr': round((1 + 1 / 3 + 1 / 6) / 4, 3)}
    assert metrics.retrieval_metrics([])['mrr'] is None


def _entry(date, amount, page=1, text=None):
    e = {'date': date, 'lines': [{'direction': 'DEBIT', 'amount': amount}, {'direction': 'CREDIT', 'amount': amount}]}
    if text is not None:
        e['evidence'] = [{'page': page, 'text': text}]
    return e


GOLD = [
    {'file': 's.pdf', 'page': 1, 'line': '01/09  ABC UTILITIES  -90.91', 'date': '2026-01-09', 'amount': '-90.91'},
    {'file': 's.pdf', 'page': 1, 'line': '01/14  NETFLIX.COM  -15.49', 'date': '2026-01-14', 'amount': '-15.49'},
    {'file': 's.pdf', 'page': 1, 'line': '01/28  RENT PAYMENT  -1850.00', 'date': '2026-01-28', 'amount': '-1850.00'},
    {'file': 'r.png', 'page': 1, 'line': None, 'date': '2026-02-11', 'amount': '-31.63'},   # receipt
]


def test_evidence_metrics_matches_by_date_and_amount():
    entries = [
        _entry('2026-01-09', '90.91', text='01/09 ABC UTILITIES -90.91'),       # whitespace differs: ok
        _entry('2026-01-14', '15.49', page=2, text='01/14  NETFLIX.COM  -15.49'),  # wrong page
        _entry('2026-02-11', '31.63'),
    ]
    out = metrics.evidence_metrics(GOLD, entries)
    assert out == {'n': 3, 'booked': 2, 'bookedRate': 0.667, 'evidenceCorrect': 1, 'evidenceAccuracy': 0.5}


def test_each_entry_matches_at_most_one_gold_line():
    gold = [{'page': 1, 'line': 'a 5.00', 'date': '2026-01-02', 'amount': '-5.00'},
            {'page': 1, 'line': 'b 5.00', 'date': '2026-01-02', 'amount': '-5.00'}]
    assert metrics.evidence_metrics(gold, [_entry('2026-01-02', '5.00', text='a 5.00')])['booked'] == 1


def test_ledger_must_equal_gold_exactly():
    exact = [_entry(g['date'], g['amount'].lstrip('-')) for g in GOLD]
    assert metrics.ledger_matches_gold(GOLD, exact)['ok']
    out = metrics.ledger_matches_gold(GOLD, exact + [_entry('2026-02-12', '1.00')])
    assert out == {'ok': False, 'expected': 4, 'booked': 5, 'missing': 0, 'extra': 1}
    assert metrics.ledger_matches_gold(GOLD, exact[1:])['missing'] == 1


def test_percentile_and_summarize_show_denominators():
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([5, 1, 3, 2, 4], 50) == 3
    assert metrics.percentile(list(range(1, 21)), 95) == 19
    scores = [metrics.score_answer(Q_SPEND, GOOD), metrics.score_answer(Q_NODATA, {}, ok=False)]
    out = metrics.summarize(scores, ['0.004000', None], [2000])
    assert out['passRate'] == {'rate': 0.5, 'passed': 1, 'n': 2}
    assert out['toolSelection'] == {'rate': 1.0, 'passed': 1, 'n': 1}      # q19 expects no tool: excluded
    assert out['errors'] == 1 and out['unpricedAnswers'] == 1
    assert out['meanCostUsd'] == '0.004000' and (out['latencyP50Ms'], out['latencyP95Ms']) == (2000, 2000)
    assert metrics.summarize([], [], [])['meanCostUsd'] is None


def test_run_eval_maps_upload_keys_to_dataset_files():
    key = 'uploads/demo-eval-v1-ab12/0f8fad5b-d9cb-469f-a165-70867728950e-statement_2026_01.pdf'
    assert run_eval.file_of({'fileKey': key}) == 'statement_2026_01.pdf'
    assert run_eval.file_of({}) == ''


def test_run_eval_reads_ids_and_counts_from_handler_logs():
    captured = '\n'.join(['not json',
                          '{"event": "tool_failed", "tool": "search_documents", "errorType": "ReadTimeoutError"}',
                          '{"event": "advisor_answered", "rounds": 2, "toolErrors": 1, "stopReason": "end_turn",'
                          ' "retrievalMs": 300, "latencyMs": 2400, "inputTokens": 3000}'])
    out = run_eval._log_fields(captured)
    assert out == {'errors': [{'event': 'tool_failed', 'tool': 'search_documents', 'errorType': 'ReadTimeoutError'}],
                   'rounds': 2, 'toolErrors': 1, 'stopReason': 'end_turn', 'retrievalMs': 300, 'latencyMs': 2400}


def test_run_eval_report_renders_every_section_with_denominators():
    rows = [{**metrics.score_answer(Q_SPEND, GOOD), 'pass': True},
            {**metrics.score_answer(Q_SPEND, {'answer': 'About $2,000.00.'}), 'pass': False}]
    summary = metrics.summarize(rows, ['0.004000', '0.002000'], [2400, 3100])
    report = {'startedAt': '2026-10-08T00:00:00+00:00', 'datasetVersion': 'v1', 'embedDimensions': '512',
              'vectorIndex': 'penny-docs-v1', 'chunkerVersion': '1',
              'manifest': [{'kind': 'statement'}] * 3 + [{'kind': 'receipt'}] * 2,
              'ledger': {'ok': True, 'expected': 26, 'booked': 26, 'missing': 0, 'extra': 0},
              'retrieval': metrics.retrieval_metrics([1, 2, None, 1]),
              'evidence': {'n': 24, 'booked': 24, 'bookedRate': 1.0, 'evidenceCorrect': 23, 'evidenceAccuracy': 0.958},
              'models': {'haiku-4.5': {'modelId': 'h', 'summary': summary, 'questions': rows},
                         'sonnet-4.6': {'modelId': 's', 'summary': summary, 'questions': rows}}}
    md = run_eval.render(report, 'eval/results/run-x.json')
    assert '| Questions fully passed | 50% (1/2) | 50% (1/2) |' in md
    assert '| Mean cost / question | $0.003000 | $0.003000 |' in md
    assert '3 synthetic statements, 2 synthetic receipts, 26 gold transactions, 2 questions' in md
    assert 'hit@3 75% (3/4)' in md and '23/24' in md
    assert '**haiku-4.5:** q01 (summary)' in md
    assert 'one question is 50 percentage points' in md and 'not through API Gateway' in md


def test_zero_figures_are_not_money_to_cite():
    s = metrics.score_answer(Q_NODATA, {'answer': 'You spent $0 in December 2025.'})
    assert s['numericOk'] and s['moneyCited'] is None
