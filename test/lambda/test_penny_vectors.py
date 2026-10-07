import io
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from penny_common import vectors


def _client_error(code):
    return ClientError({'Error': {'Code': code}}, 'GetObject')


def _manifest_body(keys):
    return {'Body': io.BytesIO(json.dumps({'docId': 'd', 'keys': keys}).encode())}


def test_read_manifest_missing_returns_none():
    s3 = MagicMock()
    s3.get_object.side_effect = _client_error('NoSuchKey')
    assert vectors.read_manifest(s3, 'b', 'd') is None


def test_read_manifest_reraises_other_errors():
    s3 = MagicMock()
    s3.get_object.side_effect = _client_error('AccessDenied')
    with pytest.raises(ClientError):
        vectors.read_manifest(s3, 'b', 'd')


def test_read_manifest_success_and_tolerant_keys():
    s3 = MagicMock()
    s3.get_object.return_value = _manifest_body(['d#p1#c0'])
    m = vectors.read_manifest(s3, 'b', 'd')
    assert vectors.manifest_keys(m) == ['d#p1#c0']
    assert vectors.manifest_keys({'docId': 'd'}) == [] and vectors.manifest_keys(None) == []


def test_write_manifest_body():
    s3 = MagicMock()
    vectors.write_manifest(s3, 'b', 'd', ['d#p1#c0'])
    kw = s3.put_object.call_args.kwargs
    assert kw['Key'] == 'manifests/d.json' and kw['ContentType'] == 'application/json'
    assert json.loads(kw['Body']) == {'docId': 'd', 'chunkerVersion': '1', 'keys': ['d#p1#c0']}


def test_put_and_delete_batch_by_500_and_skip_empty():
    sv = MagicMock()
    vectors.put_vectors(sv, 'vb', 'idx', [{'key': str(i)} for i in range(1201)])
    assert [len(c.kwargs['vectors']) for c in sv.put_vectors.call_args_list] == [500, 500, 201]
    vectors.delete_keys(sv, 'vb', 'idx', [str(i) for i in range(1001)])
    assert [len(c.kwargs['keys']) for c in sv.delete_vectors.call_args_list] == [500, 500, 1]
    sv.reset_mock()
    vectors.put_vectors(sv, 'vb', 'idx', [])
    vectors.delete_keys(sv, 'vb', 'idx', [])
    sv.put_vectors.assert_not_called()
    sv.delete_vectors.assert_not_called()


def test_delete_document_vectors_uses_manifest_keys():
    s3 = MagicMock()
    s3.get_object.return_value = _manifest_body(['d#p1#c0', 'd#p1#c1'])
    sv = MagicMock()
    assert vectors.delete_document_vectors(s3, sv, 'b', 'vb', 'idx', 'd') == 2
    sv.delete_vectors.assert_called_once_with(vectorBucketName='vb', indexName='idx', keys=['d#p1#c0', 'd#p1#c1'])
    sv.list_vectors.assert_not_called()
    s3.delete_object.assert_called_once_with(Bucket='b', Key='manifests/d.json')


def test_delete_document_vectors_falls_back_to_paginated_listing(capsys):
    s3 = MagicMock()
    s3.get_object.side_effect = _client_error('NoSuchKey')
    sv = MagicMock()
    sv.list_vectors.side_effect = [
        {'vectors': [{'key': 'd#p1#c0', 'metadata': {'docId': 'd'}},
                     {'key': 'x#p1#c0', 'metadata': {'docId': 'x'}},
                     {'key': 'n#p1#c0', 'metadata': None},
                     {'key': 'm#p1#c0'}], 'nextToken': 't'},
        {'vectors': [{'key': 'd#p2#c0', 'metadata': {'docId': 'd'}}]},
    ]
    assert vectors.delete_document_vectors(s3, sv, 'b', 'vb', 'idx', 'd') == 2
    assert 'nextToken' not in sv.list_vectors.call_args_list[0].kwargs
    assert sv.list_vectors.call_args_list[1].kwargs['nextToken'] == 't'
    sv.delete_vectors.assert_called_once_with(vectorBucketName='vb', indexName='idx', keys=['d#p1#c0', 'd#p2#c0'])
    s3.delete_object.assert_not_called()
    assert 'manifest_missing_full_scan' in capsys.readouterr().out
