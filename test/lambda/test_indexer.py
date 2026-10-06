import io
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def idx(lambda_module, monkeypatch):
    index = lambda_module('indexer')
    monkeypatch.setattr(index, 'VECTOR_BUCKET', 'vb')
    monkeypatch.setattr(index, 'APP_BUCKET', 'app')
    monkeypatch.setattr(index, 's3', MagicMock())
    monkeypatch.setattr(index, 's3vectors', MagicMock())
    monkeypatch.setattr(index, 'dynamodb', MagicMock())
    monkeypatch.setattr(index, 'embed', lambda text: ([0.1] * 512, 7))
    return index


DOC = {'docId': 'd1', 'docType': 'bank_statement', 'fileName': 'mar.pdf', 'fileKey': 'uploads/u-mar.pdf',
       'uploadedAt': '2026-04-20T00:00:00Z', 'statementPeriod': None, 'sessionId': None,
       'pages': [{'page': 1, 'text': '03/14 ABC UTILITIES -120.00\n03/15 COFFEE -4.50', 'extractor': 'pypdf'}],
       'entries': [{'entryId': 'e1', 'page': 1, 'evidenceText': '03/14 ABC UTILITIES -120.00'},
                   {'entryId': 'e2', 'page': 1, 'evidenceText': 'not in doc'}]}


def _no_manifest(idx):
    idx.s3.get_object.side_effect = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')


def _manifest(idx, keys):
    """Every get_object returns a fresh stream with this manifest (it is read more than once)."""
    idx.s3.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(json.dumps({'keys': keys}).encode())}


def test_build_vector_metadata(idx):
    v = idx.build_vector({'key': 'd1#p1#c0', 'page': 1, 'yearMonth': '2026-03', 'text': 't'}, DOC, [0.5] * 512)
    assert v['metadata']['sessionId'] == 'owner'
    assert v['metadata']['docId'] == 'd1'
    assert len(v['data']['float32']) == 512


def test_index_document_puts_vectors_writes_manifest_backfills(idx):
    _no_manifest(idx)
    summary = idx.index_document(DOC)
    assert summary['chunks'] == 1 and summary['embedTokens'] == 7
    put = idx.s3vectors.put_vectors.call_args.kwargs
    assert put['indexName'] == 'penny-docs-v1' and put['vectors'][0]['key'] == 'd1#p1#c0'
    idx.s3vectors.delete_vectors.assert_not_called()
    assert json.loads(idx.s3.put_object.call_args.kwargs['Body'])['keys'] == ['d1#p1#c0']
    upd = idx.dynamodb.Table.return_value.update_item
    upd.assert_called_once()
    assert upd.call_args.kwargs['Key'] == {'entryId': 'e1'}
    assert upd.call_args.kwargs['ExpressionAttributeValues'] == {':k': 'd1#p1#c0'}
    assert summary['chunkKeysFilled'] == 1


def test_reindex_deletes_stale_keys(idx):
    _manifest(idx, ['d1#p1#c0', 'd1#p1#c1', 'd1#p2#c0'])
    summary = idx.index_document(DOC)
    idx.s3vectors.delete_vectors.assert_called_once_with(
        vectorBucketName='vb', indexName='penny-docs-v1', keys=['d1#p1#c1', 'd1#p2#c0'])
    assert summary['staleDeleted'] == 2


def test_demo_session_metadata(idx):
    _no_manifest(idx)
    idx.index_document({**DOC, 'sessionId': 'abc'})
    assert idx.s3vectors.put_vectors.call_args.kwargs['vectors'][0]['metadata']['sessionId'] == 'abc'


def test_handler_reads_text_and_never_writes_text_prefix(idx):
    calls = []

    def get_object(Bucket, Key):
        calls.append(Key)
        if Key.startswith('manifests/'):
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(json.dumps(DOC).encode())}

    idx.s3.get_object.side_effect = get_object
    idx.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'text/d1.json'}}}]}, None)
    assert calls[0] == 'text/d1.json'
    written = [c.kwargs['Key'] for c in idx.s3.put_object.call_args_list]
    assert written and all(k == 'manifests/d1.json' for k in written)   # never text/


def test_manifest_is_written_ahead_of_vectors(idx):
    """A crash after put_vectors must leave a manifest that already lists the new keys."""
    _manifest(idx, ['d1#p9#c0'])
    idx.s3vectors.put_vectors.side_effect = RuntimeError('crash after write')
    with pytest.raises(RuntimeError):
        idx.index_document(DOC)
    written = json.loads(idx.s3.put_object.call_args_list[0].kwargs['Body'])['keys']
    assert written == ['d1#p1#c0', 'd1#p9#c0']        # old ∪ new, before any vector write
    idx.s3vectors.delete_vectors.assert_not_called()


def test_write_order_superset_put_delete_narrow(idx):
    calls = []
    _manifest(idx, ['d1#p9#c0'])
    idx.s3.put_object.side_effect = lambda **kw: calls.append(('manifest', json.loads(kw['Body'])['keys']))
    idx.s3vectors.put_vectors.side_effect = lambda **kw: calls.append(('put', None))
    idx.s3vectors.delete_vectors.side_effect = lambda **kw: calls.append(('delete', kw['keys']))
    idx.index_document(DOC)
    assert calls == [('manifest', ['d1#p1#c0', 'd1#p9#c0']), ('put', None),
                     ('delete', ['d1#p9#c0']), ('manifest', ['d1#p1#c0'])]


def test_reindex_with_same_keys_skips_write_ahead(idx):
    _manifest(idx, ['d1#p1#c0'])
    idx.index_document(DOC)
    assert idx.s3.put_object.call_count == 1      # only the final manifest write


def test_handler_failure_in_one_record_still_processes_others(idx, monkeypatch):
    seen = []

    def fake_index(doc):
        seen.append(doc['docId'])
        if doc['docId'] == 'bad':
            raise ValueError('boom')

    monkeypatch.setattr(idx, 'index_document', fake_index)
    bodies = {'text/bad.json': {'docId': 'bad'}, 'text/ok.json': {'docId': 'ok'}}
    idx.s3.get_object.side_effect = lambda Bucket, Key: {'Body': io.BytesIO(json.dumps(bodies[Key]).encode())}
    records = [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': k}}} for k in bodies]
    with pytest.raises(ValueError):
        idx.handler({'Records': records}, None)
    assert seen == ['bad', 'ok']


def test_demo_doc_ids_and_url_encoded_keys(idx):
    calls = []

    def get_object(Bucket, Key):
        calls.append(Key)
        if Key.startswith('manifests/'):
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(json.dumps({**DOC, 'docId': 'demo-abc-d1', 'sessionId': 'abc'}).encode())}

    idx.s3.get_object.side_effect = get_object
    idx.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'text/demo-abc-d1.json'}}}]}, None)
    vec = idx.s3vectors.put_vectors.call_args.kwargs['vectors'][0]
    assert vec['key'] == 'demo-abc-d1#p1#c0' and vec['metadata']['sessionId'] == 'abc'


def test_embed_request_contract(lambda_module, monkeypatch):
    index = lambda_module('indexer')
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = {'body': io.BytesIO(json.dumps({'embedding': [0.0] * 512,
                                                                        'inputTextTokenCount': 3}).encode())}
    monkeypatch.setattr(index, 'bedrock', bedrock)
    emb, n = index.embed('hello')
    assert len(emb) == 512 and n == 3
    kw = bedrock.invoke_model.call_args.kwargs
    assert kw['modelId'] == 'amazon.titan-embed-text-v2:0'
    assert json.loads(kw['body']) == {'inputText': 'hello', 'dimensions': 512, 'normalize': True}


def test_embed_rejects_wrong_dimension(lambda_module, monkeypatch):
    index = lambda_module('indexer')
    bedrock = MagicMock()
    bedrock.invoke_model.return_value = {'body': io.BytesIO(json.dumps({'embedding': [0.0] * 1024}).encode())}
    monkeypatch.setattr(index, 'bedrock', bedrock)
    with pytest.raises(ValueError):
        index.embed('hello')


def test_embed_failure_leaves_manifest_untouched(idx, monkeypatch):
    _manifest(idx, ['d1#p9#c0'])
    monkeypatch.setattr(idx, 'embed', MagicMock(side_effect=RuntimeError('bedrock throttled')))
    with pytest.raises(RuntimeError):
        idx.index_document(DOC)
    idx.s3.put_object.assert_not_called()
    idx.s3vectors.put_vectors.assert_not_called()


def test_deleted_entry_is_skipped_not_fatal(idx, capsys):
    _no_manifest(idx)
    doc = {**DOC, 'entries': [{'entryId': 'gone', 'page': 1, 'evidenceText': '03/14 ABC UTILITIES -120.00'},
                              {'entryId': 'e3', 'page': 1, 'evidenceText': '03/15 COFFEE -4.50'}]}
    upd = idx.dynamodb.Table.return_value.update_item
    upd.side_effect = [ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem'), {}]
    summary = idx.index_document(doc)
    assert summary['chunkKeysFilled'] == 1
    assert upd.call_args_list[0].kwargs['ConditionExpression'] == 'attribute_exists(evidence[0])'
    assert 'chunk_key_entry_missing' in capsys.readouterr().out


def test_other_update_errors_still_raise(idx):
    _no_manifest(idx)
    idx.dynamodb.Table.return_value.update_item.side_effect = ClientError(
        {'Error': {'Code': 'ProvisionedThroughputExceededException'}}, 'UpdateItem')
    with pytest.raises(ClientError):
        idx.index_document(DOC)


def test_empty_document_clears_old_vectors(idx, capsys):
    _manifest(idx, ['d1#p1#c0'])
    summary = idx.index_document({**DOC, 'pages': [{'page': 1, 'text': '', 'extractor': 'claude'}], 'entries': []})
    idx.s3vectors.put_vectors.assert_not_called()
    idx.s3vectors.delete_vectors.assert_called_once_with(vectorBucketName='vb', indexName='penny-docs-v1', keys=['d1#p1#c0'])
    assert json.loads(idx.s3.put_object.call_args.kwargs['Body'])['keys'] == []
    assert summary['event'] == 'indexed_empty'


def test_final_manifest_merges_concurrent_writer_keys(idx):
    """Another invocation added k_other between our reads; we must not drop it."""
    reads = iter([['d1#p9#c0'], ['d1#p1#c0', 'd1#p9#c0', 'd1#p5#c0']])
    idx.s3.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(json.dumps({'keys': next(reads)}).encode())}
    idx.index_document(DOC)
    final = json.loads(idx.s3.put_object.call_args.kwargs['Body'])['keys']
    assert final == ['d1#p1#c0', 'd1#p5#c0']      # p9 deleted by us, p5 kept from the other writer


def test_handler_decodes_key_and_rejects_mismatched_doc(idx, capsys):
    seen = []

    def get_object(Bucket, Key):
        seen.append(Key)
        if Key.startswith('manifests/'):
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(json.dumps({**DOC, 'docId': 'demo-a+b-d1', 'sessionId': 'a+b'}).encode())}

    idx.s3.get_object.side_effect = get_object
    idx.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'text/demo-a%2Bb-d1.json'}}}]}, None)
    assert seen[0] == 'text/demo-a+b-d1.json'
    with pytest.raises(ValueError):
        idx.handler({'Records': [{'s3': {'bucket': {'name': 'app'}, 'object': {'key': 'text/other.json'}}}]}, None)
    assert '"key": "text/other.json"' in capsys.readouterr().out


def test_file_name_metadata_is_masked(idx):
    v = idx.build_vector({'key': 'd1#p1#c0', 'page': 1, 'yearMonth': '2026-03', 'text': 't'},
                         {**DOC, 'fileName': 'acct_1234567890_mar.pdf'}, [0.5] * 512)
    assert v['metadata']['fileName'] == 'acct_****7890_mar.pdf'


def test_embed_dimension_comes_from_env(lambda_module, monkeypatch):
    monkeypatch.setenv('EMBED_DIMENSIONS', '1024')
    assert lambda_module('indexer').EMBED_DIMENSIONS == 1024
