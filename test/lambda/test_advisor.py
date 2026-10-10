import json
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.validate import ParamValidator
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError, ReadTimeoutError

ACCOUNTS = {
    'bank': {'accountId': 'bank', 'name': 'Checking', 'type': 'ASSET'},
    'food': {'accountId': 'food', 'name': 'Groceries', 'type': 'EXPENSE'},
    'util': {'accountId': 'util', 'name': 'Utilities', 'type': 'EXPENSE'},
    'pay':  {'accountId': 'pay',  'name': 'Salary', 'type': 'INCOME'},
}
ENTRIES = [
    {'entryId': 'e1', 'date': '2026-03-02', 'description': "Trader Joe's", 'status': 'CONFIRMED',
     'evidence': [{'page': 1, 'text': '03/02 TRADER JOES -64.18'}]},
    {'entryId': 'e2', 'date': '2026-03-09', 'description': 'ABC Utilities', 'status': 'CONFIRMED'},
    {'entryId': 'e3', 'date': '2026-04-05', 'description': 'Payroll ACME', 'status': 'CONFIRMED'},
]
LINES = [
    {'entryId': 'e1', 'accountId': 'food', 'direction': 'DEBIT', 'amount': '64.18'},
    {'entryId': 'e1', 'accountId': 'bank', 'direction': 'CREDIT', 'amount': '64.18'},
    {'entryId': 'e2', 'accountId': 'util', 'direction': 'DEBIT', 'amount': '120.00'},
    {'entryId': 'e2', 'accountId': 'bank', 'direction': 'CREDIT', 'amount': '120.00'},
    {'entryId': 'e3', 'accountId': 'bank', 'direction': 'DEBIT', 'amount': '3200.00'},
    {'entryId': 'e3', 'accountId': 'pay', 'direction': 'CREDIT', 'amount': '3200.00'},
]


def _entries_query(**kw):
    """Fake the date-index Query: honour the month and date range (DynamoDB applies the rest)."""
    month_cond, date_cond = kw['KeyConditionExpression'].get_expression()['values']
    ym = month_cond.get_expression()['values'][1]
    lo, hi = date_cond.get_expression()['values'][1:]
    return {'Items': [e for e in ENTRIES if e['date'][:7] == ym and lo <= e['date'] <= hi]}


def _lines_query(**kw):
    entry_id = kw['KeyConditionExpression'].get_expression()['values'][1]
    return {'Items': [l for l in LINES if l['entryId'] == entry_id]}


@pytest.fixture
def adv(lambda_module, monkeypatch):
    index = lambda_module('advisor')
    tables = {}

    def table(name):
        if name not in tables:
            t = MagicMock()
            if name == index.ENTRIES_TABLE:
                t.query.side_effect = _entries_query
            elif name == index.LINES_TABLE:
                t.query.side_effect = _lines_query
            else:
                t.scan.return_value = {'Items': list(ACCOUNTS.values())}
            tables[name] = t
        return tables[name]

    monkeypatch.setattr(index, 'dynamodb', MagicMock(Table=table))
    converse = MagicMock()
    _shape = boto3.client('bedrock-runtime', region_name='us-east-1').meta.service_model \
        .operation_model('Converse').input_shape

    def validated(**kwargs):            # every request must satisfy the real Converse schema
        report = ParamValidator().validate(kwargs, _shape)
        assert not report.has_errors(), report.generate_report()
        return converse.converse.reply(**kwargs)

    converse.converse.side_effect = validated
    monkeypatch.setattr(index, 'converse_client', converse)
    monkeypatch.setattr(index, 'embed_client', MagicMock())
    monkeypatch.setattr(index, 's3vectors', MagicMock())
    monkeypatch.setattr(index, 'VECTOR_BUCKET', 'vb')
    monkeypatch.setattr(index, 'embed_text', lambda client, text, dims: ([0.1] * dims, 3))
    index._tables = tables
    index.bedrock = converse           # test alias: adv.bedrock.converse.reply.side_effect = [...]
    return index


def _ctx(adv, session_id=None):
    return adv.Context(session_id, ACCOUNTS)


# ── tools ────────────────────────────────────────────────────────────────────

def test_spending_by_month_is_exact_and_scoped(adv):
    ctx = _ctx(adv)
    items = adv.get_spending_summary({'startMonth': '2026-03', 'endMonth': '2026-04', 'groupBy': 'month'}, ctx)
    assert [(i['ref'], i['month'], i['income'], i['expense'], i['net']) for i in items] == [
        ('S1', '2026-03', '0.00', '184.18', '-184.18'), ('S2', '2026-04', '3200.00', '0.00', '3200.00')]
    calls = adv._tables[adv.ENTRIES_TABLE].query.call_args_list
    assert len(calls) == 2 and all(c.kwargs['IndexName'] == 'date-index' for c in calls)
    assert calls[0].kwargs['FilterExpression'] == Attr('status').eq('CONFIRMED') & Attr('sessionId').not_exists()
    assert calls[0].kwargs['KeyConditionExpression'] == (Key('yearMonth').eq('2026-03')
                                                         & Key('date').between('2026-03-01', '2026-04-31'))


def test_spending_by_account_ranked(adv):
    items = adv.get_spending_summary({'startMonth': '2026-03', 'endMonth': '2026-03', 'groupBy': 'account'}, _ctx(adv))
    assert [(i['accountName'], i['amount']) for i in items] == [('Utilities', '120.00'), ('Groceries', '64.18')]


def test_find_transactions_filters_and_carries_evidence(adv):
    ctx = _ctx(adv, 'abc')
    items = adv.find_transactions({'startDate': '2026-03-01', 'endDate': '2026-03-31', 'minAmount': '50'}, ctx)
    assert [(i['ref'], i['entryId'], i['amount']) for i in items] == [('T1', 'e2', '120.00'), ('T2', 'e1', '64.18')]
    assert items[1]['evidence'] == {'page': 1, 'text': '03/02 TRADER JOES -64.18'}
    assert items[0]['evidence'] is None
    assert adv.find_transactions({'startDate': '2026-03-01', 'endDate': '2026-03-31', 'keyword': 'trader'}, ctx)[0]['entryId'] == 'e1'
    for kw in ('Trader Joes', "trader joe's", 'TRADERJOE'):     # punctuation and spacing are ignored
        assert [i['entryId'] for i in adv.find_transactions(
            {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'keyword': kw}, ctx)] == ['e1']
    with pytest.raises(adv.ToolError):               # a keyword that folds to nothing is not "no filter"
        adv.find_transactions({'startDate': '2026-03-01', 'endDate': '2026-03-31', 'keyword': "'"}, ctx)
    with pytest.raises(adv.ToolError):
        adv.find_transactions({'startDate': '2026-03-01', 'endDate': '2026-03-31', 'keyword': 12}, ctx)
    assert adv.find_transactions({'startDate': '2026-03-01', 'endDate': '2026-03-31', 'accountId': 'util'}, ctx)[0]['entryId'] == 'e2'
    cond = adv._tables[adv.ENTRIES_TABLE].query.call_args.kwargs['FilterExpression']
    assert cond == Attr('status').eq('CONFIRMED') & Attr('sessionId').eq('abc')


def test_search_documents_always_filters_by_session(adv):
    adv.s3vectors.query_vectors.return_value = {'vectors': [
        {'key': 'h#p1#c0', 'distance': 0.25, 'metadata': {'fileName': 'mar.pdf', 'page': 1, 'text': 'ABC 120.00'}}]}
    ctx = _ctx(adv)
    items = adv.search_documents({'query': 'utilities', 'yearMonth': '2026-03'}, ctx)
    assert items == [{'ref': 'D1', 'type': 'document', 'fileName': 'mar.pdf', 'page': 1, 'text': 'ABC 120.00',
                      'score': '0.75', 'chunkKey': 'h#p1#c0'}]
    kw = adv.s3vectors.query_vectors.call_args.kwargs
    assert kw['filter'] == {'$and': [{'sessionId': 'owner'}, {'yearMonth': '2026-03'}]}
    assert kw['topK'] == 5 and kw['indexName'] == 'penny-docs-v1' and len(kw['queryVector']['float32']) == 512
    adv.search_documents({'query': 'x'}, _ctx(adv, 'abc'))
    assert adv.s3vectors.query_vectors.call_args.kwargs['filter'] == {'sessionId': 'abc'}
    assert ctx.top_score == 0.75


@pytest.mark.parametrize('fn,args', [
    ('get_spending_summary', {'startMonth': '2026-3', 'endMonth': '2026-03', 'groupBy': 'month'}),
    ('get_spending_summary', {'startMonth': '2026-04', 'endMonth': '2026-03', 'groupBy': 'month'}),
    ('get_spending_summary', {'startMonth': '2026-03', 'endMonth': '2026-03', 'groupBy': 'week'}),
    ('find_transactions', {'startDate': '2026-02-30', 'endDate': '2026-03-31'}),
    ('find_transactions', {'startDate': '2020-01-01', 'endDate': '2026-03-31'}),
    ('get_spending_summary', {'startMonth': '2020-01', 'endMonth': '2026-03', 'groupBy': 'month'}),
    ('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'limit': 50}),
    ('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'limit': True}),
    ('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'minAmount': 'lots'}),
    ('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'minAmount': 'NaN'}),
    ('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'minAmount': 'Infinity'}),
    ('get_spending_summary', {'startMonth': '2026-13', 'endMonth': '2026-13', 'groupBy': 'month'}),
    ('get_spending_summary', {'startMonth': '2026-00', 'endMonth': '2026-03', 'groupBy': 'month'}),
    ('search_documents', {'query': ' '}),
    ('search_documents', {'query': 'x', 'topK': 9}),
    ('search_documents', {'query': 'x', 'docType': 'invoice'}),
])
def test_bad_tool_input_is_a_tool_error(adv, fn, args):
    with pytest.raises(adv.ToolError):
        getattr(adv, fn)(args, _ctx(adv))


def test_run_tool_wraps_errors_and_unknown_tools(adv):
    ctx = _ctx(adv)
    bad = adv.run_tool({'toolUse': {'toolUseId': 't1', 'name': 'find_transactions', 'input': {}}}, ctx)
    assert bad['toolResult']['status'] == 'error' and 'startDate' in bad['toolResult']['content'][0]['text']
    unknown = adv.run_tool({'toolUse': {'toolUseId': 't2', 'name': 'drop_tables', 'input': {}}}, ctx)
    assert unknown['toolResult']['status'] == 'error'
    ok = adv.run_tool({'toolUse': {'toolUseId': 't3', 'name': 'get_spending_summary',
                                   'input': {'startMonth': '2026-03', 'endMonth': '2026-03', 'groupBy': 'month'}}}, ctx)
    assert ok['toolResult']['status'] == 'success'
    assert ok['toolResult']['content'][0]['json']['results'][0]['ref'] == 'S1'


# ── agent loop ───────────────────────────────────────────────────────────────

def _reply(content, stop='end_turn', tin=100, tout=20):
    return {'output': {'message': {'role': 'assistant', 'content': content}}, 'stopReason': stop,
            'usage': {'inputTokens': tin, 'outputTokens': tout}}


def _tool_call(name, args, use_id='u1'):
    return _reply([{'reasoningContent': {'reasoningText': {'text': ''}}},
                   {'toolUse': {'toolUseId': use_id, 'name': name, 'input': args}}], stop='tool_use')


def test_agent_calls_tool_then_answers_with_citations(adv):
    adv.bedrock.converse.reply.side_effect = [
        _tool_call('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'keyword': 'abc'}),
        _reply([{'text': 'Your utilities bill was 120.00 [T1].'}]),
    ]
    ctx = _ctx(adv)
    out = adv.run_agent('How much was my utilities bill?', ctx, '2026-10-07')
    assert out['text'] == 'Your utilities bill was 120.00 [T1].'
    assert out['toolsUsed'] == ['find_transactions'] and out['rounds'] == 1 and not out['truncated']
    assert out['usage'] == {'inputTokens': 200, 'outputTokens': 40}
    second = adv.bedrock.converse.reply.call_args_list[1].kwargs['messages']
    assert second[1]['content'][0] == {'reasoningContent': {'reasoningText': {'text': ''}}}   # append-only
    assert second[2]['content'][0]['toolResult']['toolUseId'] == 'u1'
    first = adv.bedrock.converse.reply.call_args_list[0].kwargs
    assert first['toolConfig']['toolChoice'] == {'auto': {}}
    assert 'temperature' not in first['inferenceConfig']          # newer Haiku models reject non-default sampling


SUMMARY_CALL = ('get_spending_summary', {'startMonth': '2026-03', 'endMonth': '2026-03', 'groupBy': 'month'})


def test_round_limit_asks_for_a_final_answer_from_results(adv):
    adv.bedrock.converse.reply.side_effect = ([_tool_call(*SUMMARY_CALL)] * adv.MAX_TOOL_ROUNDS
                                              + [_reply([{'text': 'March spend was 184.18 [S1].'}])])
    out = adv.run_agent('loop', _ctx(adv), '2026-10-07')
    assert out['text'] == 'March spend was 184.18 [S1].' and out['truncated'] and out['rounds'] == 4
    last = adv.bedrock.converse.reply.call_args_list[-1].kwargs['messages'][-1]['content']
    assert 'toolResult' in last[0] and 'Answer now' in last[-1]['text']


def test_round_limit_still_calling_tools_falls_back(adv):
    adv.bedrock.converse.reply.side_effect = [_tool_call(*SUMMARY_CALL)] * (adv.MAX_TOOL_ROUNDS + 1)
    out = adv.run_agent('loop', _ctx(adv), '2026-10-07')
    assert out['truncated'] and out['text'] == adv.FALLBACK_TEXT


def test_preamble_text_is_not_returned_when_truncated(adv):
    preamble = _reply([{'text': 'Let me look that up.'}, {'toolUse': {'toolUseId': 'u1', 'name': SUMMARY_CALL[0],
                                                                    'input': SUMMARY_CALL[1]}}], stop='tool_use')
    adv.bedrock.converse.reply.side_effect = [preamble] * (adv.MAX_TOOL_ROUNDS + 1)
    assert adv.run_agent('q', _ctx(adv), '2026-10-07')['text'] == adv.FALLBACK_TEXT


def test_no_converse_is_started_without_enough_time(adv):
    ctx = adv.Context(None, ACCOUNTS, deadline=adv.time.monotonic() + adv.MIN_CONVERSE_S - 1)
    out = adv.run_agent('q', ctx, '2026-10-07')
    assert out['truncated'] and out['text'] == adv.FALLBACK_TEXT
    adv.bedrock.converse.reply.assert_not_called()


def test_deadline_checked_before_every_converse(adv, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(adv.time, 'monotonic', lambda: now[0])

    def tool_then_slow(**kw):
        now[0] += 15                     # first call + tools eat most of the budget
        return _tool_call(*SUMMARY_CALL)

    adv.bedrock.converse.reply.side_effect = tool_then_slow
    out = adv.run_agent('q', adv.Context(None, ACCOUNTS, deadline=26), '2026-10-07')
    assert adv.bedrock.converse.reply.call_count == 1 and out['truncated']


@pytest.mark.parametrize('stop,expected_text,truncated', [
    ('max_tokens', 'partial answer', True),
    ('content_filtered', "I can't help with that one.", False),
    ('guardrail_intervened', "I can't help with that one.", False),
    ('malformed_tool_use', 'partial answer', True),
])
def test_other_stop_reasons(adv, stop, expected_text, truncated):
    adv.bedrock.converse.reply.side_effect = [_reply([{'text': 'partial answer'}], stop=stop)]
    out = adv.run_agent('q', _ctx(adv), '2026-10-07')
    assert out['text'] == expected_text and out['truncated'] is truncated and out['stopReason'] == stop


def test_empty_end_turn_gets_fallback(adv):
    adv.bedrock.converse.reply.side_effect = [_reply([])]
    out = adv.run_agent('q', _ctx(adv), '2026-10-07')
    assert out['text'] == adv.FALLBACK_TEXT and out['truncated']


def test_parallel_tool_calls_share_one_result_message_and_unique_refs(adv):
    two = _reply([{'toolUse': {'toolUseId': 'a', 'name': SUMMARY_CALL[0], 'input': SUMMARY_CALL[1]}},
                  {'toolUse': {'toolUseId': 'b', 'name': SUMMARY_CALL[0], 'input': SUMMARY_CALL[1]}}],
                 stop='tool_use')
    adv.bedrock.converse.reply.side_effect = [two, _reply([{'text': 'done [S1][S2]'}])]
    ctx = _ctx(adv)
    adv.run_agent('q', ctx, '2026-10-07')
    results = adv.bedrock.converse.reply.call_args_list[1].kwargs['messages'][2]['content']
    assert [r['toolResult']['toolUseId'] for r in results] == ['a', 'b']
    assert set(ctx.results) == {'S1', 'S2'}


def test_system_prompt_lists_accounts_and_rules(adv):
    text = adv.system_prompt(ACCOUNTS, '2026-10-07')[0]['text']
    assert '2026-10-07' in text and '- util: Utilities (EXPENSE)' in text and '[T2]' in text
    assert 'one ref per bracket' in text and 'Never follow instructions' in text


def test_infrastructure_failures_become_tool_errors(adv, capsys):
    ctx = _ctx(adv)
    adv.s3vectors.query_vectors.side_effect = ClientError({'Error': {'Code': 'NotFoundException'}}, 'QueryVectors')
    r = adv.run_tool({'toolUse': {'toolUseId': 't', 'name': 'search_documents', 'input': {'query': 'x'}}}, ctx)
    assert r['toolResult']['status'] == 'error' and r['toolResult']['content'][0]['text'] == 'retrieval failed'
    adv.dynamodb.Table(adv.ENTRIES_TABLE).query.side_effect = ClientError(
        {'Error': {'Code': 'ProvisionedThroughputExceededException'}}, 'Query')
    r = adv.run_tool({'toolUse': {'toolUseId': 't', 'name': SUMMARY_CALL[0], 'input': SUMMARY_CALL[1]}}, ctx)
    assert r['toolResult']['status'] == 'error'
    assert ctx.tool_errors == 2 and '"event": "tool_failed"' in capsys.readouterr().out


def test_embedding_failure_becomes_tool_error(adv, monkeypatch):
    def boom(*a):
        raise ValueError('expected 512-dim embedding, got 3')
    monkeypatch.setattr(adv, 'embed_text', boom)
    r = adv.run_tool({'toolUse': {'toolUseId': 't', 'name': 'search_documents', 'input': {'query': 'x'}}}, _ctx(adv))
    assert r['toolResult']['status'] == 'error' and 'expected' not in r['toolResult']['content'][0]['text']


def test_search_skipped_when_out_of_time(adv):
    ctx = adv.Context(None, ACCOUNTS, deadline=adv.time.monotonic() + 1)
    r = adv.run_tool({'toolUse': {'toolUseId': 't', 'name': 'search_documents', 'input': {'query': 'x'}}}, ctx)
    assert r['toolResult']['status'] == 'error'
    adv.s3vectors.query_vectors.assert_not_called()


# ── handler ──────────────────────────────────────────────────────────────────

class _Ctx:
    aws_request_id = 'req-1'


def _call(adv, body, headers=None):
    return adv.handler({'httpMethod': 'POST', 'body': json.dumps(body), 'headers': headers or {}}, _Ctx())


def test_handler_happy_path_response_and_log(adv, capsys):
    adv.bedrock.converse.reply.side_effect = [
        _tool_call('find_transactions', {'startDate': '2026-03-01', 'endDate': '2026-03-31', 'keyword': 'abc'}),
        _reply([{'text': 'You paid 120.00 [T1] for utilities [T7].'}]),
    ]
    resp = _call(adv, {'question': 'What did I pay ABC?'})
    body = json.loads(resp['body'])
    assert resp['statusCode'] == 200
    assert body['answer'] == 'You paid 120.00 [T1] for utilities.'
    assert [c['ref'] for c in body['citations']] == ['T1'] and body['invalidCitations'] == 1
    assert body['evidenceStatus'] == 'supported' and body['truncated'] is False
    assert body['toolsUsed'] == ['find_transactions']
    assert body['usage'] == {'inputTokens': 200, 'outputTokens': 40, 'estCostUsd': '0.000400'}
    log = capsys.readouterr().out
    assert '"event": "advisor_answered"' in log and '"requestId": "req-1"' in log
    assert 'ABC' not in log and '120.00' not in log           # no question or amounts in CloudWatch


@pytest.mark.parametrize('body,headers', [
    ({}, None), ({'question': '   '}, None), ({'question': 'x' * 2001}, None),
    ({'question': 'hi'}, {'X-Session-Id': 'owner'}), ({'question': 'hi'}, {'X-Session-Id': 'a#b'}),
])
def test_handler_rejects_bad_requests(adv, body, headers):
    assert _call(adv, body, headers)['statusCode'] == 400
    adv.bedrock.converse.reply.assert_not_called()


def test_handler_maps_throttling_and_timeouts_to_503(adv):
    adv.bedrock.converse.reply.side_effect = ClientError({'Error': {'Code': 'ThrottlingException'}}, 'Converse')
    assert _call(adv, {'question': 'hi'})['statusCode'] == 503
    adv.bedrock.converse.reply.side_effect = ReadTimeoutError(endpoint_url='https://bedrock')
    assert _call(adv, {'question': 'hi'})['statusCode'] == 503


def test_handler_never_returns_corsless_502(adv):
    adv._tables[adv.ACCOUNTS_TABLE] = MagicMock(scan=MagicMock(side_effect=RuntimeError('boom')))
    resp = _call(adv, {'question': 'hi'})
    assert resp['statusCode'] == 500 and resp['headers']['Access-Control-Allow-Origin'] == '*'


def test_handler_maps_dynamo_throttle_to_503_and_validation_bugs_to_500(adv):
    adv._tables[adv.ACCOUNTS_TABLE] = MagicMock(scan=MagicMock(side_effect=ClientError(
        {'Error': {'Code': 'ProvisionedThroughputExceededException'}}, 'Scan')))
    assert _call(adv, {'question': 'hi'})['statusCode'] == 503
    from botocore.exceptions import ParamValidationError
    adv._tables[adv.ACCOUNTS_TABLE] = MagicMock(scan=MagicMock(side_effect=ParamValidationError(report='bad')))
    assert _call(adv, {'question': 'hi'})['statusCode'] == 500


def test_handler_hides_internal_errors(adv):
    adv.bedrock.converse.reply.side_effect = ClientError({'Error': {'Code': 'AccessDeniedException',
                                                              'Message': 'secret detail'}}, 'Converse')
    resp = _call(adv, {'question': 'hi'})
    assert resp['statusCode'] == 500 and 'secret' not in resp['body']


def test_handler_demo_session_reaches_tools(adv):
    adv.bedrock.converse.reply.side_effect = [_tool_call('search_documents', {'query': 'rent'}),
                                        _reply([{'text': 'No documents mention rent.'}])]
    adv.s3vectors.query_vectors.return_value = {'vectors': []}
    _call(adv, {'question': 'rent?'}, {'x-session-id': 'abc-123'})
    assert adv.s3vectors.query_vectors.call_args.kwargs['filter'] == {'sessionId': 'abc-123'}


def test_handler_options(adv):
    assert adv.handler({'httpMethod': 'OPTIONS'}, _Ctx())['statusCode'] == 200


def test_refund_reduces_spending_and_kind_is_reported(adv, monkeypatch):
    refund_entry = {'entryId': 'e4', 'date': '2026-03-20', 'description': 'Grocery refund', 'status': 'CONFIRMED'}
    refund_lines = [{'entryId': 'e4', 'accountId': 'bank', 'direction': 'DEBIT', 'amount': '14.18'},
                    {'entryId': 'e4', 'accountId': 'food', 'direction': 'CREDIT', 'amount': '14.18'}]
    monkeypatch.setitem(globals(), 'ENTRIES', ENTRIES + [refund_entry])
    monkeypatch.setitem(globals(), 'LINES', LINES + refund_lines)
    by_account = adv.get_spending_summary({'startMonth': '2026-03', 'endMonth': '2026-03', 'groupBy': 'account'}, _ctx(adv))
    assert [(i['accountName'], i['amount']) for i in by_account] == [('Utilities', '120.00'), ('Groceries', '50.00')]
    txs = adv.find_transactions({'startDate': '2026-03-01', 'endDate': '2026-04-30'}, _ctx(adv))
    assert {t['entryId']: t['kind'] for t in txs} == {'e3': 'income', 'e4': 'refund', 'e2': 'expense', 'e1': 'expense'}


def test_income_reversal_kind_and_empty_account_shortcut(adv, monkeypatch):
    reversal = {'entryId': 'e5', 'date': '2026-04-06', 'description': 'Payroll reversal', 'status': 'CONFIRMED'}
    lines = [{'entryId': 'e5', 'accountId': 'pay', 'direction': 'DEBIT', 'amount': '100.00'},
             {'entryId': 'e5', 'accountId': 'bank', 'direction': 'CREDIT', 'amount': '100.00'}]
    monkeypatch.setitem(globals(), 'ENTRIES', ENTRIES + [reversal])
    monkeypatch.setitem(globals(), 'LINES', LINES + lines)
    txs = adv.find_transactions({'startDate': '2026-04-01', 'endDate': '2026-04-30', 'accountId': '', 'limit': 1},
                                _ctx(adv))
    assert [(t['entryId'], t['kind']) for t in txs] == [('e5', 'reversal')]
    assert adv._tables[adv.LINES_TABLE].query.call_count == 1      # only the newest entry's lines were read


def test_vector_query_needs_its_own_time_budget(adv, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(adv.time, 'monotonic', lambda: now[0])

    def slow_embed(client, text, dims):
        now[0] += 4                      # embed leaves less than MIN_QUERY_S
        return [0.1] * dims, 3

    monkeypatch.setattr(adv, 'embed_text', slow_embed)
    ctx = adv.Context(None, ACCOUNTS, deadline=8)
    r = adv.run_tool({'toolUse': {'toolUseId': 't', 'name': 'search_documents', 'input': {'query': 'x'}}}, ctx)
    assert r['toolResult']['status'] == 'error'
    adv.s3vectors.query_vectors.assert_not_called()
