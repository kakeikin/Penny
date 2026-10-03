import json
import os
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from penny_common.chunking import chunk_document
from penny_common.textnorm import contains_normalized
from penny_common.vectors import delete_keys, manifest_keys, put_vectors, read_manifest, write_manifest

_retry = Config(retries={'max_attempts': 8, 'mode': 'adaptive'})
s3        = boto3.client('s3')
bedrock   = boto3.client('bedrock-runtime', region_name='us-east-1', config=_retry)
s3vectors = boto3.client('s3vectors', region_name='us-east-1', config=_retry)
dynamodb  = boto3.resource('dynamodb')

APP_BUCKET    = os.environ.get('APP_BUCKET', '')
VECTOR_BUCKET = os.environ.get('VECTOR_BUCKET', '')
VECTOR_INDEX  = os.environ.get('VECTOR_INDEX', 'penny-docs-v1')
ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')

EMBED_MODEL_ID   = 'amazon.titan-embed-text-v2:0'
EMBED_DIMENSIONS = 512


def embed(text: str) -> tuple:
    """Return (embedding, input_token_count) from Titan Text Embeddings V2."""
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=json.dumps({'inputText': text, 'dimensions': EMBED_DIMENSIONS, 'normalize': True}),
    )
    out = json.loads(resp['body'].read())
    embedding = out['embedding']
    if len(embedding) != EMBED_DIMENSIONS:
        raise ValueError(f'expected {EMBED_DIMENSIONS}-dim embedding, got {len(embedding)}')
    return embedding, out.get('inputTextTokenCount', 0)


def build_vector(chunk: dict, doc: dict, embedding: list) -> dict:
    # Metadata limits: filterable <= 2 KB, total <= 40 KB. Chunk text is bounded by
    # chunking.MAX_SINGLE_CHUNK (8000 chars), well under the total even for CJK text.
    return {
        'key': chunk['key'],
        'data': {'float32': embedding},
        'metadata': {
            # filterable
            'docId':     doc['docId'],
            'docType':   doc['docType'],
            'yearMonth': chunk['yearMonth'],
            'sessionId': doc.get('sessionId') or 'owner',
            # non-filterable (configured on the index)
            'text':      chunk['text'],
            'page':      chunk['page'],
            'fileKey':   doc['fileKey'],
            'fileName':  doc['fileName'],
        },
    }


def find_chunk_key(chunks: list, page: int, evidence_text: str):
    for c in chunks:
        if c['page'] == page and contains_normalized(c['text'], evidence_text):
            return c['key']
    return None


def backfill_chunk_keys(doc: dict, chunks: list) -> int:
    """Set evidence[0].chunkKey (v1 entries carry one evidence item). Idempotent on retry.

    An entry deleted since parsing (DELETE /api/entries/{id}) is skipped, not fatal.
    """
    table = dynamodb.Table(ENTRIES_TABLE)
    filled = 0
    for e in doc.get('entries', []):
        key = find_chunk_key(chunks, e['page'], e['evidenceText'])
        if key is None:
            print(json.dumps({'event': 'chunk_key_not_found', 'docId': doc['docId'], 'entryId': e['entryId']}))
            continue
        try:
            table.update_item(
                Key={'entryId': e['entryId']},
                UpdateExpression='SET evidence[0].chunkKey = :k',
                ConditionExpression='attribute_exists(evidence[0])',
                ExpressionAttributeValues={':k': key},
            )
        except ClientError as err:
            if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
                raise
            print(json.dumps({'event': 'chunk_key_entry_missing', 'docId': doc['docId'], 'entryId': e['entryId']}))
            continue
        filled += 1
    return filled


def index_document(doc: dict) -> dict:
    """Embed and store a document's chunks; keep the manifest a superset of indexed keys at every step.

    Manifest writes re-read and merge, so a concurrent invocation for the same docId (e.g. a
    backfill racing a live upload) usually just over-lists keys (harmless; the next re-index
    trims them). A narrow race can still drop a key; S3 conditional writes would close it.
    """
    doc_id = doc['docId']
    chunks = chunk_document(doc)
    new_keys = [c['key'] for c in chunks]

    # Embed first: Bedrock is the most likely failure and must not touch the manifest.
    vectors, tokens = [], 0
    for c in chunks:
        emb, n = embed(c['text'])
        tokens += n
        vectors.append(build_vector(c, doc, emb))

    old_keys = set(manifest_keys(read_manifest(s3, APP_BUCKET, doc_id)))
    # Write-ahead: if we crash after put_vectors, the manifest already lists the new keys.
    if not set(new_keys) <= old_keys:
        write_manifest(s3, APP_BUCKET, doc_id, sorted(old_keys | set(new_keys)))
    put_vectors(s3vectors, VECTOR_BUCKET, VECTOR_INDEX, vectors)

    stale = sorted(old_keys - set(new_keys))
    delete_keys(s3vectors, VECTOR_BUCKET, VECTOR_INDEX, stale)
    fresh = set(manifest_keys(read_manifest(s3, APP_BUCKET, doc_id)))
    write_manifest(s3, APP_BUCKET, doc_id, sorted((fresh | set(new_keys)) - set(stale)))

    filled = backfill_chunk_keys(doc, chunks)
    summary = {'event': 'indexed' if chunks else 'indexed_empty', 'docId': doc_id, 'chunks': len(chunks),
               'staleDeleted': len(stale), 'chunkKeysFilled': filled, 'embedTokens': tokens}
    print(json.dumps(summary))
    return summary


def handler(event, context):
    # Reads text/ only; never writes to text/ (that would re-trigger this Lambda).
    failures = []
    for n, record in enumerate(event.get('Records', [])):
        key = None
        try:
            key = unquote_plus(record['s3']['object']['key'])
            obj = s3.get_object(Bucket=record['s3']['bucket']['name'], Key=key)
            doc = json.loads(obj['Body'].read())
            if key != f"text/{doc.get('docId')}.json":
                raise ValueError('text doc key does not match its docId')
            index_document(doc)
        except Exception as e:
            # key is text/{docId}.json: a hash or demo id, not document content.
            print(json.dumps({'event': 'index_failed', 'record': n, 'key': key, 'errorType': type(e).__name__}))
            failures.append(e)
    if failures:
        raise failures[0]   # async retry, then DLQ
