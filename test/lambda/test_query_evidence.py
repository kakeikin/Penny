import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def q(lambda_module, monkeypatch):
    index = lambda_module('query')
    monkeypatch.setattr(index, 'APP_BUCKET', 'app')
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = 'https://signed'
    monkeypatch.setattr(index, 's3_client', s3)
    return index


def _entry(q, monkeypatch, item):
    table = MagicMock()
    table.get_item.return_value = {'Item': item} if item else {}
    monkeypatch.setattr(q, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))


EV = {'docId': 'h1', 'sourceType': 'bank_statement', 'page': Decimal('2'), 'text': 't', 'chunkKey': None}


def _call(q, session=None):
    headers = {'X-Session-Id': session} if session else {}
    return q.handler({'path': '/api/entries/e1/evidence', 'pathParameters': {'id': 'e1'},
                      'headers': headers}, None)


def test_evidence_owner_array_with_deduped_presign(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'uploads/x.pdf',
                            'evidence': [EV, {**EV, 'page': Decimal('3')}]})
    resp = _call(q)
    body = json.loads(resp['body'])
    assert resp['statusCode'] == 200
    assert [e['page'] for e in body['evidence']] == [2, 3]
    assert all(e['fileUrl'] == 'https://signed' for e in body['evidence'])
    q.s3_client.generate_presigned_url.assert_called_once()


def test_evidence_absent_returns_empty_list(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k'})
    assert json.loads(_call(q)['body']) == {'evidence': []}


def test_evidence_other_session_is_404(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'sessionId': 'abc', 'evidence': [EV]})
    assert _call(q)['statusCode'] == 404
    assert _call(q, session='zzz')['statusCode'] == 404
    assert _call(q, session='abc')['statusCode'] == 200


def test_evidence_missing_entry_is_404(q, monkeypatch):
    _entry(q, monkeypatch, None)
    assert _call(q)['statusCode'] == 404


def test_demo_entry_evidence_gets_url_via_namespaced_doc_id(q, monkeypatch):
    ev = {**EV, 'docId': 'demo-abc-h1'}
    _entry(q, monkeypatch, {'entryId': 'e1', 'sessionId': 'abc', 'fileHash': 'h1', 'fileKey': 'uploads/demo-abc/x.pdf',
                            'evidence': [ev]})
    body = json.loads(_call(q, session='abc')['body'])
    assert body['evidence'][0]['fileUrl'] == 'https://signed'
    params = q.s3_client.generate_presigned_url.call_args.kwargs['Params']
    assert (params['Bucket'], params['Key']) == ('app', 'uploads/demo-abc/x.pdf')


def test_evidence_for_foreign_doc_has_no_url(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k', 'evidence': [{**EV, 'docId': 'other'}]})
    body = json.loads(_call(q)['body'])
    assert body['evidence'][0]['fileUrl'] is None
    q.s3_client.generate_presigned_url.assert_not_called()


def test_evidence_route_skips_accounts_scan(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k'})
    monkeypatch.setattr(q, 'get_all_accounts', MagicMock(side_effect=AssertionError('should not scan accounts')))
    assert _call(q)['statusCode'] == 200


def test_demo_session_cannot_read_owner_entry(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k.pdf', 'evidence': [EV]})
    assert _call(q, session='abc')['statusCode'] == 404


def test_empty_session_header_is_owner(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k.pdf'})
    resp = q.handler({'path': '/api/entries/e1/evidence', 'pathParameters': {'id': 'e1'},
                      'headers': {'X-Session-Id': ''}}, None)
    assert resp['statusCode'] == 200


def test_demo_entry_with_owner_doc_id_gets_no_url(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'sessionId': 'abc', 'fileHash': 'h1', 'fileKey': 'k.pdf',
                            'evidence': [{**EV, 'docId': 'h1'}]})
    assert json.loads(_call(q, session='abc')['body'])['evidence'][0]['fileUrl'] is None


def test_presign_is_short_lived_inline_and_typed(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'uploads/x.PDF', 'evidence': [EV]})
    _call(q)
    kw = q.s3_client.generate_presigned_url.call_args.kwargs
    assert kw['ExpiresIn'] == 300
    assert kw['Params']['ResponseContentType'] == 'application/pdf'
    assert kw['Params']['ResponseContentDisposition'] == 'inline'


def test_unknown_file_type_gets_no_url(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'uploads/x.html', 'evidence': [EV]})
    assert json.loads(_call(q)['body'])['evidence'][0]['fileUrl'] is None


def test_malformed_evidence_item_is_skipped(q, monkeypatch, capsys):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k.pdf',
                            'evidence': [{**EV, 'page': None}, {k: v for k, v in EV.items() if k != 'page'}, EV]})
    body = json.loads(_call(q)['body'])
    assert [e['page'] for e in body['evidence']] == [2]
    assert 'evidence_malformed_skipped' in capsys.readouterr().out


def test_response_fields_are_allowlisted(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'k.pdf', 'evidence': [{**EV, 'internal': 'x'}]})
    item = json.loads(_call(q)['body'])['evidence'][0]
    assert set(item) == {'docId', 'sourceType', 'text', 'chunkKey', 'page', 'fileUrl'}


def test_missing_doc_id_never_matches(q, monkeypatch):
    _entry(q, monkeypatch, {'entryId': 'e1', 'fileKey': 'k.pdf', 'evidence': [{k: v for k, v in EV.items() if k != 'docId'}]})
    assert json.loads(_call(q)['body'])['evidence'][0]['fileUrl'] is None


def test_empty_id_is_404_and_dynamo_error_is_500_with_cors(q, monkeypatch):
    resp = q.handler({'path': '/api/entries//evidence', 'pathParameters': {'id': ''}, 'headers': {}}, None)
    assert resp['statusCode'] == 404 and resp['headers']['Access-Control-Allow-Origin'] == '*'
    table = MagicMock()
    table.get_item.side_effect = ClientError({'Error': {'Code': 'ProvisionedThroughputExceededException'}}, 'GetItem')
    monkeypatch.setattr(q, 'dynamodb', MagicMock(Table=MagicMock(return_value=table)))
    resp = _call(q)
    assert resp['statusCode'] == 500 and resp['headers']['Access-Control-Allow-Origin'] == '*'
