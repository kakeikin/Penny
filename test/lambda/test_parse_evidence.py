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
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda *a: False)
    entry = {'date': '2026-03-14', 'description': 'X',
             'lines': [{'accountId': 'a', 'direction': 'DEBIT', 'amount': Decimal('100.00')},
                       {'accountId': 'b', 'direction': 'CREDIT', 'amount': Decimal('99.99')}]}
    parse.save_pending_entries([entry], 'uploads/k.pdf', 'h', 'PDF')
    out = capsys.readouterr().out
    assert 'entry_unbalanced_skipped' in out and '"debit": "100.00"' in out
    assert 'description' not in out          # no document text in logs
    parse.dynamodb.Table.return_value.put_item.assert_not_called()


def test_transcribe_instruction(parse):
    assert '"transcripts": []' in parse.build_transcribe_instruction([])
    assert 'Pages 2, 3' in parse.build_transcribe_instruction([2, 3])


PAGES = [{'page': 1, 'text': 'Statement\n03/14  ABC UTILITIES   -120.00\n', 'extractor': 'pypdf'},
         {'page': 2, 'text': 'scanned text 4111111111111111', 'extractor': 'claude'}]


def test_evidence_accepted(parse):
    ev = parse.build_evidence({'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -120.00'}},
                              PAGES, 'd1', 'bank_statement')
    assert ev == [{'docId': 'd1', 'sourceType': 'bank_statement', 'page': 1,
                   'text': '03/14 ABC UTILITIES -120.00', 'chunkKey': None}]


def test_evidence_fabricated_rejected(parse, capsys):
    ev = parse.build_evidence({'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -210.00'}},
                              PAGES, 'd1', 'bank_statement')
    assert ev == []
    assert 'evidence_rejected' in capsys.readouterr().out


def test_evidence_unknown_page_rejected(parse, capsys):
    assert parse.build_evidence({'evidence': {'page': 9, 'text': 'x'}}, PAGES, 'd1', 'bank_statement') == []
    assert '"reason": "unknown_page"' in capsys.readouterr().out


@pytest.mark.parametrize('evidence', [
    {'page': 'two', 'text': '03/14 ABC UTILITIES -120.00'},
    {'page': [1], 'text': '03/14 ABC UTILITIES -120.00'},
    {'page': 0, 'text': '03/14 ABC UTILITIES -120.00'},
    {'page': None, 'text': '03/14 ABC UTILITIES -120.00'},
    {'page': True, 'text': '03/14 ABC UTILITIES -120.00'},
    {'page': Decimal('1.5'), 'text': '03/14 ABC UTILITIES -120.00'},
    {'page': 1, 'text': 5},
    {'page': 1, 'text': {'a': 1}},
    'p1: ABC',
    ['p1'],
])
def test_build_evidence_never_raises_on_malformed_input(parse, evidence):
    assert parse.build_evidence({'evidence': evidence}, PAGES, 'd1', 'bank_statement') == []


def test_evidence_page_and_text_coercion(parse):
    ok = parse.build_evidence({'evidence': {'page': '1', 'text': ['03/14  ABC UTILITIES', '-120.00']}},
                              [{'page': 1, 'text': '03/14 ABC UTILITIES\n-120.00', 'extractor': 'pypdf'}], 'd1', 'bank_statement')
    assert ok[0]['page'] == 1 and ok[0]['text'] == '03/14  ABC UTILITIES\n-120.00'
    assert parse.build_evidence({'evidence': {'page': Decimal('1.0'), 'text': '03/14 ABC UTILITIES -120.00'}},
                                PAGES, 'd1', 'bank_statement')[0]['page'] == 1


def test_evidence_too_long_rejected(parse):
    long_text = 'Statement ' * 60
    pages = [{'page': 1, 'text': long_text, 'extractor': 'pypdf'}]
    assert parse.build_evidence({'evidence': {'page': 1, 'text': long_text}}, pages, 'd1', 'bank_statement') == []


def test_merge_transcripts_ignores_malformed_items(parse):
    pages = [{'page': 1, 'text': '', 'extractor': 'claude'}, {'page': 2, 'text': '', 'extractor': 'claude'}]
    out = parse.merge_transcripts(pages, [{'text': 'no page'}, {'page': 'one', 'text': 'x'}, 'junk',
                                          {'page': 2, 'text': ['line a', 'line b']}])
    assert out[0]['text'] == '' and out[1]['text'] == 'line a\nline b'
    assert parse.merge_transcripts(pages, None) == pages


def test_evidence_on_transcribed_page_checked_against_transcript_and_masked(parse):
    ev = parse.build_evidence({'evidence': {'page': 2, 'text': 'scanned text 4111111111111111'}},
                              PAGES, 'd1', 'bank_statement')
    assert ev[0]['text'] == 'scanned text ****1111'
    assert parse.build_evidence({'evidence': {'page': 2, 'text': 'not in transcript'}},
                                PAGES, 'd1', 'bank_statement') == []


def test_evidence_on_untranscribed_page_is_accepted(parse):
    pages = [{'page': 1, 'text': '', 'extractor': 'claude'}]
    assert parse.build_evidence({'evidence': {'page': 1, 'text': 'ABC 1.00'}}, pages, 'd1', 'receipt')[0]['page'] == 1


def test_evidence_missing(parse):
    assert parse.build_evidence({}, PAGES, 'd1', 'receipt') == []


def test_merge_transcripts_only_fills_claude_pages(parse):
    pages = [{'page': 1, 'text': 'pdf text', 'extractor': 'pypdf'}, {'page': 2, 'text': '', 'extractor': 'claude'}]
    out = parse.merge_transcripts(pages, [{'page': 2, 'text': 'from claude'}, {'page': 1, 'text': 'ignored'}])
    assert out[0]['text'] == 'pdf text' and out[1]['text'] == 'from claude'


def test_s3_event_writes_evidence_and_text_doc(parse, monkeypatch):
    pdf = make_pdf([['Statement March', '03/14 ABC UTILITIES -120.00']])
    s3 = MagicMock()
    s3.get_object.return_value = {'Body': io.BytesIO(pdf)}
    monkeypatch.setattr(parse, 's3_client', s3)
    table = MagicMock()
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    monkeypatch.setattr(parse, 'is_duplicate', lambda *a: False)
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda *a: False)
    monkeypatch.setattr(parse, 'get_accounts', lambda: [])
    seen = {}

    def fake_claude(data, media, accounts, transcribe_pages):
        seen['transcribe'] = transcribe_pages
        return {'entries': [{'date': '2026-03-14', 'description': 'ABC UTILITIES',
                             'lines': [{'accountId': '5100', 'direction': 'DEBIT', 'amount': Decimal('120.00')},
                                       {'accountId': '1100', 'direction': 'CREDIT', 'amount': Decimal('120.00')}],
                             'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -120.00'}}],
                'transcripts': []}

    monkeypatch.setattr(parse, 'parse_with_claude', fake_claude)
    key = 'uploads/123e4567-e89b-12d3-a456-426614174000-mar.pdf'
    parse.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': key}}}]}, None)

    assert seen['transcribe'] == []
    entry_item = table.put_item.call_args_list[0].kwargs['Item']
    assert entry_item['evidence'][0]['page'] == 1
    assert entry_item['evidence'][0]['docId'] == entry_item['fileHash']
    put = s3.put_object.call_args.kwargs
    assert put['Key'] == f"text/{entry_item['fileHash']}.json"
    doc = json.loads(put['Body'])
    assert doc['fileName'] == 'mar.pdf' and doc['docType'] == 'bank_statement' and doc['sessionId'] is None
    assert doc['pages'][0]['extractor'] == 'pypdf'
    assert doc['entries'] == [{'entryId': entry_item['entryId'], 'page': 1,
                               'evidenceText': '03/14 ABC UTILITIES -120.00'}]


def _run_s3_event(parse, monkeypatch, file_bytes, key, claude_result, seen=None):
    s3 = MagicMock()
    s3.get_object.return_value = {'Body': io.BytesIO(file_bytes)}
    monkeypatch.setattr(parse, 's3_client', s3)
    table = MagicMock()
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    monkeypatch.setattr(parse, 'is_duplicate', lambda *a: False)
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda *a: False)
    monkeypatch.setattr(parse, 'get_accounts', lambda: [])

    def fake_claude(data, media, accounts, transcribe_pages):
        if seen is not None:
            seen['transcribe'] = transcribe_pages
        return claude_result

    monkeypatch.setattr(parse, 'parse_with_claude', fake_claude)
    parse.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': key}}}]}, None)
    return s3, table


BALANCED_ENTRY = {'date': '2026-03-14', 'description': 'ABC UTILITIES',
                  'lines': [{'accountId': '5100', 'direction': 'DEBIT', 'amount': Decimal('120.00')},
                            {'accountId': '1100', 'direction': 'CREDIT', 'amount': Decimal('120.00')}],
                  'evidence': {'page': 1, 'text': '03/14 ABC UTILITIES -120.00'}}


def test_unreadable_pdf_still_books_entries_without_evidence(parse, monkeypatch, capsys):
    s3, table = _run_s3_event(parse, monkeypatch, b'not a pdf', 'uploads/123e4567-e89b-12d3-a456-426614174000-x.pdf',
                              {'entries': [BALANCED_ENTRY], 'transcripts': []})
    item = table.put_item.call_args_list[0].kwargs['Item']
    assert 'evidence' not in item                     # unknown page -> evidence rejected
    s3.put_object.assert_not_called()                 # no pages -> no text doc, nothing to index
    assert 'pdf_text_unavailable' in capsys.readouterr().out


def test_s3_event_key_is_url_decoded(parse, monkeypatch):
    pdf = make_pdf([['Statement March', '03/14 ABC UTILITIES -120.00']])
    s3, table = _run_s3_event(parse, monkeypatch, pdf, 'uploads/123e4567-e89b-12d3-a456-426614174000-bank+mar%282%29.pdf',
                              {'entries': [BALANCED_ENTRY], 'transcripts': []})
    decoded = 'uploads/123e4567-e89b-12d3-a456-426614174000-bank mar(2).pdf'
    assert s3.get_object.call_args.kwargs['Key'] == decoded
    assert table.put_item.call_args_list[0].kwargs['Item']['fileKey'] == decoded
    assert json.loads(s3.put_object.call_args.kwargs['Body'])['fileName'] == 'bank mar(2).pdf'


def test_receipt_image_requests_transcript_and_keeps_it(parse, monkeypatch):
    seen = {}
    receipt_entry = {**BALANCED_ENTRY, 'evidence': {'page': 1, 'text': 'ABC UTILITIES 120.00'}}
    s3, table = _run_s3_event(parse, monkeypatch, b'\xff\xd8jpeg', 'uploads/123e4567-e89b-12d3-a456-426614174000-r.jpg',
                              {'entries': [receipt_entry], 'transcripts': [{'page': 1, 'text': 'ABC UTILITIES 120.00'}]},
                              seen)
    assert seen['transcribe'] == [1]
    doc = json.loads(s3.put_object.call_args.kwargs['Body'])
    assert doc['docType'] == 'receipt' and doc['pages'][0]['text'] == 'ABC UTILITIES 120.00'
    assert table.put_item.call_args_list[0].kwargs['Item']['evidence'][0]['sourceType'] == 'receipt'


def test_load_claude_json_uppercase_fence(parse):
    fence = '`' * 3
    assert parse.load_claude_json(f'{fence}JSON\n{{"entries": []}}\n{fence}') == {'entries': []}


def test_garbage_evidence_still_books_entry(parse, monkeypatch):
    pdf = make_pdf([['Statement March', '03/14 ABC UTILITIES -120.00']])
    entry = {**BALANCED_ENTRY, 'evidence': {'page': 'two', 'text': ['x', 3]}}
    s3, table = _run_s3_event(parse, monkeypatch, pdf, 'uploads/123e4567-e89b-12d3-a456-426614174000-m.pdf',
                              {'entries': [entry], 'transcripts': []})
    item = table.put_item.call_args_list[0].kwargs['Item']
    assert item['description'] == 'ABC UTILITIES' and 'evidence' not in item


def test_demo_session_upload_is_namespaced(parse, monkeypatch):
    pdf = make_pdf([['Statement March', '03/14 ABC UTILITIES -120.00']])
    s3, table = _run_s3_event(parse, monkeypatch, pdf, 'uploads/demo-abc/123e4567-e89b-12d3-a456-426614174000-m.pdf',
                              {'entries': [BALANCED_ENTRY], 'transcripts': []})
    item = table.put_item.call_args_list[0].kwargs['Item']
    assert item['sessionId'] == 'abc'
    assert item['evidence'][0]['docId'] == f"demo-abc-{item['fileHash']}"
    doc = json.loads(s3.put_object.call_args.kwargs['Body'])
    assert doc['sessionId'] == 'abc' and doc['docId'] == f"demo-abc-{item['fileHash']}"
    assert s3.put_object.call_args.kwargs['Key'] == f"text/demo-abc-{item['fileHash']}.json"


def test_text_doc_write_failure_does_not_fail_bookkeeping(parse, monkeypatch, capsys):
    pdf = make_pdf([['Statement March', '03/14 ABC UTILITIES -120.00']])
    s3 = MagicMock()
    s3.get_object.return_value = {'Body': io.BytesIO(pdf)}
    s3.put_object.side_effect = RuntimeError('s3 down')
    monkeypatch.setattr(parse, 's3_client', s3)
    table = MagicMock()
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    monkeypatch.setattr(parse, 'is_duplicate', lambda *a: False)
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda *a: False)
    monkeypatch.setattr(parse, 'get_accounts', lambda: [])
    monkeypatch.setattr(parse, 'parse_with_claude', lambda *a: {'entries': [BALANCED_ENTRY], 'transcripts': []})
    parse.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'uploads/m.pdf'}}}]}, None)
    assert table.put_item.called
    assert 'text_doc_write_failed' in capsys.readouterr().out


def test_one_bad_record_does_not_skip_the_others(parse, monkeypatch):
    calls = []

    def process(bucket, key):
        calls.append(key)
        if key == 'uploads/bad.pdf':
            raise ValueError('boom')

    monkeypatch.setattr(parse, 'process_upload', process)
    records = [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': k}}} for k in ('uploads/bad.pdf', 'uploads/ok.pdf')]
    with pytest.raises(ValueError):
        parse.handler({'Records': records}, None)
    assert calls == ['uploads/bad.pdf', 'uploads/ok.pdf']


def _bedrock_reply(text, stop_reason='end_turn'):
    return {'body': io.BytesIO(json.dumps({'content': [{'text': text}], 'stop_reason': stop_reason}).encode())}


def test_truncated_output_retries_without_transcripts(parse, monkeypatch, capsys):
    bedrock = MagicMock()
    bedrock.invoke_model.side_effect = [_bedrock_reply('{"entries": [', 'max_tokens'),
                                        _bedrock_reply('{"entries": [], "transcripts": []}')]
    monkeypatch.setattr(parse, 'bedrock', bedrock)
    out = parse.parse_with_claude(b'%PDF', 'application/pdf', [], [1, 2])
    assert out == {'entries': [], 'transcripts': []}
    second_prompt = json.loads(bedrock.invoke_model.call_args_list[1].kwargs['body'])['messages'][0]['content'][1]['text']
    assert 'Return "transcripts": [].' in second_prompt
    assert 'transcripts_truncated' in capsys.readouterr().out


def test_parse_with_claude_tolerates_null_lists(parse, monkeypatch):
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = _bedrock_reply('{"entries": null, "transcripts": null}')
    monkeypatch.setattr(parse, 'bedrock', bedrock)
    assert parse.parse_with_claude(b'%PDF', 'application/pdf', [], []) == {'entries': [], 'transcripts': []}


def test_duplicate_checks_page_through_scan_and_scope_by_session(parse, monkeypatch):
    table = MagicMock()
    table.scan.side_effect = [{'Items': [], 'LastEvaluatedKey': {'entryId': 'x'}}, {'Items': [{'entryId': 'e'}]}]
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    assert parse.is_duplicate('h', 'abc') is True
    assert table.scan.call_args_list[1].kwargs['ExclusiveStartKey'] == {'entryId': 'x'}
    from boto3.dynamodb.conditions import Attr
    assert table.scan.call_args_list[0].kwargs['FilterExpression'] == Attr('fileHash').eq('h') & Attr('sessionId').eq('abc')
    table.scan.side_effect = [{'Items': []}]
    assert parse.is_duplicate_entry('h') is False
    assert table.scan.call_args.kwargs['FilterExpression'] == Attr('entryHash').eq('h') & Attr('sessionId').not_exists()


@pytest.mark.parametrize('entry', [
    'not a dict',
    {'description': 'no date', 'lines': BALANCED_ENTRY['lines']},
    {**BALANCED_ENTRY, 'date': '14/03/2026'},
    {**BALANCED_ENTRY, 'lines': None},
    {**BALANCED_ENTRY, 'lines': [BALANCED_ENTRY['lines'][0]]},
    {**BALANCED_ENTRY, 'lines': [{'direction': 'DEBIT', 'amount': '1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}]},
    {**BALANCED_ENTRY, 'lines': [{'accountId': 'a', 'direction': 'debit', 'amount': '1.00'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1.00'}]},
    {**BALANCED_ENTRY, 'lines': [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '12.0x'}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '12.00'}]},
    {**BALANCED_ENTRY, 'lines': [{'accountId': 'a', 'direction': 'DEBIT', 'amount': None}, {'accountId': 'b', 'direction': 'CREDIT', 'amount': '1'}]},
])
def test_normalize_entry_rejects_malformed(parse, entry):
    assert parse.normalize_entry(entry) is None


def test_normalize_entry_coerces_amounts_and_text(parse):
    e = parse.normalize_entry({'date': '2026-03-14', 'description': 5,
                               'lines': [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '120.00', 'note': None},
                                         {'accountId': 'b', 'direction': 'CREDIT', 'amount': 120}]})
    assert [l['amount'] for l in e['lines']] == [Decimal('120.00'), Decimal('120')]
    assert e['description'] == '' and e['lines'][0]['note'] == ''


def test_malformed_entry_in_batch_does_not_block_the_others(parse, monkeypatch, capsys):
    table = MagicMock()
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda *a: False)
    bad = {**BALANCED_ENTRY, 'lines': [{'accountId': 'a', 'direction': 'DEBIT', 'amount': '12.0x'},
                                       {'accountId': 'b', 'direction': 'CREDIT', 'amount': '12.00'}]}
    first = {**BALANCED_ENTRY, 'description': 'FIRST'}
    third = {**BALANCED_ENTRY, 'description': 'THIRD'}
    parse.save_pending_entries([first, bad, third], 'uploads/k.pdf', 'h', 'PDF')
    descriptions = [c.kwargs['Item']['description'] for c in table.put_item.call_args_list if 'description' in c.kwargs['Item']]
    assert descriptions == ['FIRST', 'THIRD']
    assert 'entry_invalid_skipped' in capsys.readouterr().out


def test_no_writes_happen_before_all_entries_are_prepared(parse, monkeypatch):
    order = []
    table = MagicMock()
    table.put_item.side_effect = lambda **kw: order.append('write')
    monkeypatch.setattr(parse, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    monkeypatch.setattr(parse, 'is_duplicate_entry', lambda *a: order.append('check') or False)
    parse.save_pending_entries([BALANCED_ENTRY, BALANCED_ENTRY], 'uploads/k.pdf', 'h', 'PDF')
    assert order[:2] == ['check', 'check'] and set(order[2:]) == {'write'}


def test_unicode_digit_page_is_rejected_not_crashing(parse):
    pages = [{'page': 1, 'text': '', 'extractor': 'claude'}]
    assert parse.merge_transcripts(pages, [{'page': '\u00b2', 'text': 'x'}]) == pages
    assert parse.build_evidence({'evidence': {'page': '\u00b2', 'text': 'x'}}, pages, 'd1', 'receipt') == []


def test_upload_rejects_invalid_session_id(parse, monkeypatch):
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = 'https://signed'
    monkeypatch.setattr(parse, 's3_client', s3)
    resp = parse.handler({'httpMethod': 'POST', 'path': '/api/upload',
                          'body': json.dumps({'filename': 'a.pdf', 'sessionId': 'a#b/c'})}, None)
    assert resp['statusCode'] == 400
    ok = parse.handler({'httpMethod': 'POST', 'path': '/api/upload',
                        'body': json.dumps({'filename': 'a.pdf', 'sessionId': '123e4567-e89b-12d3-a456-426614174000'})}, None)
    assert ok['statusCode'] == 200


def test_s3_key_with_invalid_session_is_skipped(parse, monkeypatch, capsys):
    s3 = MagicMock()
    monkeypatch.setattr(parse, 's3_client', s3)
    parse.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'uploads/demo-a%23b/x.pdf'}}}]}, None)
    s3.get_object.assert_not_called()
    assert 'invalid_session_key_skipped' in capsys.readouterr().out
