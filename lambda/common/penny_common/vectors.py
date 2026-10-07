"""S3 Vectors helpers: batched writes/deletes and per-document manifests.

The manifest (manifests/{docId}.json) is the source of truth for which vector keys a
document owns. IndexLambda keeps it a superset of the keys in the index at every step
(write-ahead), so delete_document_vectors can trust it.
"""
import json

from botocore.exceptions import ClientError

from penny_common.chunking import CHUNKER_VERSION

BATCH_SIZE = 500  # PutVectors / DeleteVectors limit


def manifest_key(doc_id: str) -> str:
    return f'manifests/{doc_id}.json'


def read_manifest(s3, bucket: str, doc_id: str):
    """Return the manifest dict, or None if it doesn't exist.

    get_object returns NoSuchKey for a missing key only if the caller has s3:ListBucket;
    without it S3 answers AccessDenied, which is re-raised (grant ListBucket).
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=manifest_key(doc_id))
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
            return None
        raise
    return json.loads(obj['Body'].read())


def manifest_keys(manifest) -> list:
    return list((manifest or {}).get('keys') or [])


def write_manifest(s3, bucket: str, doc_id: str, keys: list) -> None:
    body = {'docId': doc_id, 'chunkerVersion': CHUNKER_VERSION, 'keys': list(keys)}
    s3.put_object(Bucket=bucket, Key=manifest_key(doc_id),
                  Body=json.dumps(body).encode(), ContentType='application/json')


def _batches(items: list, size: int = BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def put_vectors(s3vectors, vector_bucket: str, index: str, vectors: list) -> None:
    for batch in _batches(vectors):
        s3vectors.put_vectors(vectorBucketName=vector_bucket, indexName=index, vectors=batch)


def delete_keys(s3vectors, vector_bucket: str, index: str, keys: list) -> None:
    for batch in _batches(keys):
        s3vectors.delete_vectors(vectorBucketName=vector_bucket, indexName=index, keys=batch)


def list_keys_for_doc(s3vectors, vector_bucket: str, index: str, doc_id: str) -> list:
    """Fallback when a manifest is missing: scan the whole index (ListVectors has no filter)."""
    print(json.dumps({'event': 'manifest_missing_full_scan', 'docId': doc_id}))
    keys, token = [], None
    while True:
        kwargs = {'vectorBucketName': vector_bucket, 'indexName': index,
                  'maxResults': 1000, 'returnMetadata': True}
        if token:
            kwargs['nextToken'] = token
        resp = s3vectors.list_vectors(**kwargs)
        keys += [v['key'] for v in resp.get('vectors', [])
                 if (v.get('metadata') or {}).get('docId') == doc_id]
        token = resp.get('nextToken')
        if not token:
            return keys


def delete_document_vectors(s3, s3vectors, bucket: str, vector_bucket: str, index: str, doc_id: str) -> int:
    """Delete every vector for doc_id using its manifest; returns the number of keys deleted."""
    manifest = read_manifest(s3, bucket, doc_id)
    if manifest is None:
        keys = list_keys_for_doc(s3vectors, vector_bucket, index, doc_id)
    else:
        keys = manifest_keys(manifest)
    delete_keys(s3vectors, vector_bucket, index, keys)
    if manifest is not None:
        s3.delete_object(Bucket=bucket, Key=manifest_key(doc_id))
    return len(keys)
