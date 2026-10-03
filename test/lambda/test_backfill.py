import importlib.util
import io
import json
import os
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from pdf_fixtures import make_pdf

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
TEXT_PDF = make_pdf([['03/14 ABC UTILITIES -120.00 statement']])
SCANNED_PDF = make_pdf([[]])


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SCRIPTS, f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backfill():
    return _load('backfill_index')


def _no_such_key(*a, **kw):
    raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')


def test_list_documents_dedupes_paginates_namespaces_and_keeps_evidence(backfill):
    table = MagicMock()
    table.scan.side_effect = [
        {'Items': [{'entryId': 'e2', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '2026-03-02',
                    'evidence': [{'page': Decimal('1'), 'text': '03/14 ABC -120.00'}]},
                   {'entryId': 'manual'}], 'LastEvaluatedKey': {'entryId': 'x'}},
        {'Items': [{'entryId': 'e1', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '2026-03-01'},
                   {'entryId': 'e3', 'fileHash': 'h1', 'fileKey': 'uploads/demo-s/b.pdf', 'sessionId': 's',
                    'createdAt': '2026-03-05', 'evidence': [{'page': 'bad'}]}]},
    ]
    docs = backfill.list_documents(table)
    assert docs == [
        {'docId': 'demo-s-h1', 'fileKey': 'uploads/demo-s/b.pdf', 'sessionId': 's', 'uploadedAt': '2026-03-05',
         'entries': []},
        {'docId': 'h1', 'fileKey': 'uploads/a.pdf', 'sessionId': None, 'uploadedAt': '2026-03-01',
         'entries': [{'entryId': 'e2', 'page': 1, 'evidenceText': '03/14 ABC -120.00'}]},
    ]
    assert table.scan.call_args_list[1].kwargs['ExclusiveStartKey'] == {'entryId': 'x'}


def test_build_pages_rules(backfill):
    s3 = MagicMock()
    s3.get_object.side_effect = lambda **kw: {'Body': io.BytesIO({'uploads/a.pdf': TEXT_PDF,
                                                                    'uploads/scan.pdf': SCANNED_PDF,
                                                                    'uploads/bad.pdf': b'not a pdf'}[kw['Key']])}
    doc_type, pages = backfill.build_pages(s3, MagicMock(), 'app', 'uploads/a.pdf', include_images=False)
    assert doc_type == 'bank_statement' and pages[0]['extractor'] == 'pypdf'
    assert backfill.build_pages(s3, MagicMock(), 'app', 'uploads/scan.pdf', False) == (None, 'no_text_layer')
    assert backfill.build_pages(s3, MagicMock(), 'app', 'uploads/bad.pdf', False) == (None, 'pdf_malformed')
    assert backfill.build_pages(s3, MagicMock(), 'app', 'uploads/r.jpg', False) == (None, 'unsupported_or_images_disabled')
    assert backfill.build_pages(s3, MagicMock(), 'app', 'uploads/notes.txt', True) == (None, 'unsupported_or_images_disabled')


def _run_main(backfill, monkeypatch, items, files, existing_docs, argv, bedrock=None):
    s3 = MagicMock()
    bedrock = bedrock or MagicMock()

    def get_object(Bucket, Key):
        if Key.startswith('text/'):
            if Key not in existing_docs:
                _no_such_key()
            return {'Body': io.BytesIO(json.dumps(existing_docs[Key]).encode())}
        return {'Body': io.BytesIO(files[Key])}

    s3.get_object.side_effect = get_object
    table = MagicMock()
    table.scan.return_value = {'Items': items}
    monkeypatch.setattr(backfill.boto3, 'client', lambda name, **kw: s3 if name == 's3' else bedrock)
    monkeypatch.setattr(backfill.boto3, 'resource', lambda name, **kw: MagicMock(Table=lambda n: table))
    return backfill.main(['--bucket', 'app', *argv]), s3


def test_main_skips_existing_writes_new_and_namespaces_demo(backfill, monkeypatch, capsys):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'},
             {'entryId': 'b', 'fileHash': 'h2', 'fileKey': 'uploads/demo-s/b.pdf', 'sessionId': 's', 'createdAt': '2'},
             {'entryId': 'c', 'fileHash': 'h3', 'fileKey': 'uploads/c.png', 'createdAt': '3'}]
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/demo-s/b.pdf': TEXT_PDF},
                          {'text/h1.json': {'pages': []}}, [])
    assert stats == {'written': 1, 'reindexed': 0, 'exists': 1, 'skipped': 1, 'failed': 0}
    out = capsys.readouterr().out
    assert 'skipped' in out and 'c.png' in out and 'unsupported_or_images_disabled' in out
    put = s3.put_object.call_args.kwargs
    assert put['Key'] == 'text/demo-s-h2.json'
    doc = json.loads(put['Body'])
    assert doc['docId'] == 'demo-s-h2' and doc['sessionId'] == 's'


def test_force_reuploads_transcript_docs_unchanged(backfill, monkeypatch, capsys):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'}]
    existing_doc = {'docId': 'h1', 'pages': [{'page': 1, 'text': 'from claude', 'extractor': 'claude'}]}
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/a.pdf': TEXT_PDF},
                          {'text/h1.json': existing_doc}, ['--force'])
    assert stats['reindexed'] == 1 and stats['written'] == 0
    assert json.loads(s3.put_object.call_args.kwargs['Body']) == existing_doc
    assert 'kept_transcripts' in capsys.readouterr().out


def test_force_rewrites_text_only_docs_with_evidence_links(backfill, monkeypatch):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1',
              'evidence': [{'page': 1, 'text': '03/14 ABC UTILITIES -120.00'}]}]
    existing = {'text/h1.json': {'pages': [{'page': 1, 'text': 'x' * 30, 'extractor': 'pypdf'}]}}
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/a.pdf': TEXT_PDF}, existing, ['--force'])
    assert stats['written'] == 1
    doc = json.loads(s3.put_object.call_args.kwargs['Body'])
    assert doc['entries'] == [{'entryId': 'a', 'page': 1, 'evidenceText': '03/14 ABC UTILITIES -120.00'}]


def test_dry_run_writes_nothing_and_never_calls_claude(backfill, monkeypatch):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'},
             {'entryId': 'b', 'fileHash': 'h2', 'fileKey': 'uploads/r.jpg', 'createdAt': '2'}]
    bedrock = MagicMock()
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/a.pdf': TEXT_PDF}, {},
                          ['--dry-run', '--include-images'], bedrock)
    assert stats['written'] == 2
    s3.put_object.assert_not_called()
    bedrock.invoke_model.assert_not_called()


def test_read_text_doc_reraises_access_denied(backfill):
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')
    with pytest.raises(ClientError):
        backfill.read_text_doc(s3, 'app', 'h1')


def test_delete_script_defaults_to_versioned_index(monkeypatch):
    script = _load('delete_document_vectors')
    calls = {}
    monkeypatch.setattr(script, 'delete_document_vectors', lambda s3, sv, b, vb, idx, d: calls.update(idx=idx, d=d) or 3)
    monkeypatch.setattr(script.boto3, 'client', lambda *a, **kw: MagicMock())
    assert script.main(['--bucket', 'app', '--vector-bucket', 'vb', '--doc-id', 'h1']) == 3
    assert calls == {'idx': 'penny-docs-v1', 'd': 'h1'}


def test_image_transcription_request_and_receipt_doc(backfill, monkeypatch):
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = {'body': io.BytesIO(json.dumps(
        {'content': [{'text': 'ABC 1.00'}], 'stop_reason': 'end_turn'}).encode())}
    items = [{'entryId': 'b', 'fileHash': 'h2', 'fileKey': 'uploads/r.jpg', 'createdAt': '2'}]
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/r.jpg': b'\xff\xd8jpeg'}, {},
                          ['--include-images'], bedrock)
    body = json.loads(bedrock.invoke_model.call_args.kwargs['body'])
    assert body['messages'][0]['content'][0]['source']['media_type'] == 'image/jpeg'
    doc = json.loads(s3.put_object.call_args.kwargs['Body'])
    assert doc['docType'] == 'receipt' and doc['pages'] == [{'page': 1, 'text': 'ABC 1.00', 'extractor': 'claude'}]


def test_one_failing_document_does_not_stop_the_run(backfill, monkeypatch, capsys):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/gone.pdf', 'createdAt': '1'},
             {'entryId': 'b', 'fileHash': 'h2', 'fileKey': 'uploads/b.pdf', 'createdAt': '2'}]
    files = {'uploads/b.pdf': TEXT_PDF}
    stats, s3 = _run_main(backfill, monkeypatch, items, files, {}, [])   # gone.pdf -> KeyError in fake S3
    assert stats['failed'] == 1 and stats['written'] == 1
    assert 'failed' in capsys.readouterr().out


def test_mixed_pdf_with_empty_scanned_pages_can_be_forced(backfill, monkeypatch):
    mixed = {'docId': 'h1', 'pages': [{'page': 1, 'text': 'x' * 30, 'extractor': 'pypdf'},
                                      {'page': 2, 'text': '', 'extractor': 'claude'}]}
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'}]
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/a.pdf': TEXT_PDF},
                          {'text/h1.json': mixed}, ['--force'])
    assert stats['written'] == 1 and stats['reindexed'] == 0


def test_force_without_existing_doc_writes(backfill, monkeypatch):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'}]
    stats, _ = _run_main(backfill, monkeypatch, items, {'uploads/a.pdf': TEXT_PDF}, {}, ['--force'])
    assert stats['written'] == 1


def test_limit_caps_writes(backfill, monkeypatch):
    items = [{'entryId': str(i), 'fileHash': f'h{i}', 'fileKey': f'uploads/{i}.pdf', 'createdAt': str(i)} for i in range(3)]
    files = {f'uploads/{i}.pdf': TEXT_PDF for i in range(3)}
    stats, s3 = _run_main(backfill, monkeypatch, items, files, {}, ['--limit', '2'])
    assert stats['written'] == 2 and s3.put_object.call_count == 2


def test_entries_without_created_at_do_not_win_earliest(backfill):
    table = MagicMock()
    table.scan.return_value = {'Items': [
        {'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/undated.pdf'},
        {'entryId': 'b', 'fileHash': 'h1', 'fileKey': 'uploads/dated.pdf', 'createdAt': '2026-03-01'},
    ]}
    [doc] = backfill.list_documents(table)
    assert doc['fileKey'] == 'uploads/dated.pdf' and doc['uploadedAt'] == '2026-03-01'


def test_force_keeps_existing_doc_when_rebuild_is_impossible(backfill, monkeypatch):
    existing_doc = {'docId': 'h1', 'pages': [{'page': 1, 'text': 'x' * 30, 'extractor': 'pypdf'}]}
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'}]
    stats, s3 = _run_main(backfill, monkeypatch, items, {'uploads/a.pdf': b'now corrupt'},
                          {'text/h1.json': existing_doc}, ['--force'])
    assert stats['reindexed'] == 1
    assert json.loads(s3.put_object.call_args.kwargs['Body']) == existing_doc


def test_failure_detail_includes_aws_error_code(backfill, monkeypatch, capsys):
    items = [{'entryId': 'a', 'fileHash': 'h1', 'fileKey': 'uploads/a.pdf', 'createdAt': '1'}]
    s3 = MagicMock()
    s3.get_object.side_effect = ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')
    table = MagicMock()
    table.scan.return_value = {'Items': items}
    monkeypatch.setattr(backfill.boto3, 'client', lambda name, **kw: s3 if name == 's3' else MagicMock())
    monkeypatch.setattr(backfill.boto3, 'resource', lambda name, **kw: MagicMock(Table=lambda n: table))
    assert backfill.main(['--bucket', 'app'])['failed'] == 1
    assert 'ClientError AccessDenied' in capsys.readouterr().out
