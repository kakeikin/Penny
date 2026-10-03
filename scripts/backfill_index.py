#!/usr/bin/env python3
"""Backfill text/{docId}.json for uploads, which triggers IndexLambda to (re)index them.

Usage:
  python scripts/backfill_index.py --bucket <AppBucket output> [--force] [--include-images]
                                   [--limit N] [--dry-run]

Modes:
  default   write a text doc only for uploads that don't have one yet.
  --force   re-index everything (e.g. after bumping the vector index to -v2). Docs whose
            text came from pypdf are regenerated from the source file; docs that hold Claude
            transcripts are re-uploaded unchanged (no model call, transcripts never lost).

Notes:
  - PDFs use pypdf only (free). Image receipts need a Claude transcription (paid, ~1 Sonnet
    call each) and are skipped unless --include-images; --dry-run never calls Claude.
  - Documents are discovered through the entries table, so an upload with no entries is
    not found. Pre-RAG entries have no evidence, so they never get a chunkKey.
  - Every written text doc fires one async IndexLambda run; check the IndexDlq afterwards.
  - Do not run --force while live uploads of the same files are in flight (spec §12).
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lambda', 'common'))

import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from penny_common.pdftext import PdfTextError, extract_pdf_pages  # noqa: E402
from penny_common.textdoc import build_text_doc, display_name, doc_id_for, text_doc_key  # noqa: E402

TRANSCRIBE_MODEL_ID = 'us.anthropic.claude-sonnet-4-6'
IMAGE_TYPES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}


def _evidence_link(item: dict):
    """{entryId, page, evidenceText} from an entry that already carries evidence, else None."""
    evidence = item.get('evidence') or []
    try:
        first = evidence[0]
        return {'entryId': item['entryId'], 'page': int(first['page']), 'evidenceText': first['text']}
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _age_key(created: str):
    """Sort key where entries without createdAt count as newest, not oldest."""
    return (created == '', created)


def list_documents(entries_table) -> list:
    """One record per uploaded file (per session), from the earliest entry that references it."""
    docs = {}
    kwargs = {'ProjectionExpression': 'entryId, fileHash, fileKey, sessionId, createdAt, evidence'}
    while True:
        resp = entries_table.scan(**kwargs)
        for item in resp.get('Items', []):
            file_hash, file_key = item.get('fileHash'), item.get('fileKey')
            if not file_hash or not file_key:
                continue  # manual entries have no source file
            doc_id = doc_id_for(file_hash, item.get('sessionId'))
            created = item.get('createdAt') or ''
            doc = docs.get(doc_id)
            if doc is None:
                doc = docs[doc_id] = {'docId': doc_id, 'fileKey': file_key, 'sessionId': item.get('sessionId'),
                                      'uploadedAt': created, 'entries': []}
            elif _age_key(created) < _age_key(doc['uploadedAt']):
                doc.update(fileKey=file_key, uploadedAt=created)
            link = _evidence_link(item)
            if link:
                doc['entries'].append(link)
        if 'LastEvaluatedKey' not in resp:
            for doc in docs.values():
                doc['entries'].sort(key=lambda e: e['entryId'])
            return sorted(docs.values(), key=lambda d: d['docId'])
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']


def read_text_doc(s3, bucket: str, doc_id: str):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=text_doc_key(doc_id))['Body'].read())
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
            return None
        raise


def has_transcripts(text_doc: dict) -> bool:
    """True if the doc holds Claude transcripts that a pypdf-only rebuild would lose."""
    return any(p.get('extractor') == 'claude' and (p.get('text') or '').strip()
               for p in text_doc.get('pages', []))


def is_image(file_key: str) -> bool:
    return file_key.rsplit('.', 1)[-1].lower() in IMAGE_TYPES


def transcribe_image(bedrock, data: bytes, media_type: str) -> str:
    body = json.dumps({
        'anthropic_version': 'bedrock-2023-05-31',
        'max_tokens': 4096,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type,
                                         'data': base64.b64encode(data).decode('utf-8')}},
            {'type': 'text', 'text': 'Transcribe all visible text in this receipt line by line. Output only the text.'},
        ]}],
    })
    result = json.loads(bedrock.invoke_model(modelId=TRANSCRIBE_MODEL_ID, body=body)['body'].read())
    if result.get('stop_reason') == 'max_tokens':
        print(json.dumps({'event': 'transcript_truncated'}))
    return result['content'][0]['text']


def build_pages(s3, bedrock, bucket: str, file_key: str, include_images: bool):
    """Return (doc_type, pages) or (None, reason) when the file should be skipped."""
    ext = file_key.rsplit('.', 1)[-1].lower()
    if ext != 'pdf' and (ext not in IMAGE_TYPES or not include_images):
        return None, 'unsupported_or_images_disabled'
    data = s3.get_object(Bucket=bucket, Key=file_key)['Body'].read()
    if ext == 'pdf':
        try:
            pages = extract_pdf_pages(data)
        except PdfTextError as e:
            return None, f'pdf_{e.reason}'
        if not any(p['extractor'] == 'pypdf' for p in pages):
            return None, 'no_text_layer'
        return 'bank_statement', pages
    text = transcribe_image(bedrock, data, IMAGE_TYPES[ext])
    return 'receipt', [{'page': 1, 'text': text, 'extractor': 'claude'}]


def _put_text_doc(s3, bucket: str, doc: dict) -> None:
    s3.put_object(Bucket=bucket, Key=text_doc_key(doc['docId']),
                  Body=json.dumps(doc, ensure_ascii=False).encode('utf-8'),
                  ContentType='application/json')


def process(doc, args, s3, bedrock) -> tuple:
    """Handle one document; return (stat, detail)."""
    existing = read_text_doc(s3, args.bucket, doc['docId'])
    if existing is not None and not args.force:
        return 'exists', None
    if existing is not None and has_transcripts(existing):
        # Re-upload unchanged: re-fires IndexLambda without regenerating (or paying for) text.
        if not args.dry_run:
            _put_text_doc(s3, args.bucket, existing)
        return 'reindexed', 'kept_transcripts'
    if args.dry_run and is_image(doc['fileKey']) and args.include_images:
        return 'written', 'would_transcribe'   # dry-run never calls Claude
    doc_type, pages = build_pages(s3, bedrock, args.bucket, doc['fileKey'], args.include_images)
    if doc_type is None:
        if existing is not None:
            # --force rebuild impossible (e.g. source now unreadable): keep the doc in the index.
            if not args.dry_run:
                _put_text_doc(s3, args.bucket, existing)
            return 'reindexed', f'kept_existing ({pages})'
        return 'skipped', pages
    if not args.dry_run:
        _put_text_doc(s3, args.bucket, build_text_doc(doc['docId'], doc['fileKey'], doc_type, pages,
                                                      doc['entries'], doc['sessionId'], doc['uploadedAt']))
    return 'written', f'{len(pages)} pages'


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bucket', required=True, help='App bucket name (CDK output BucketName)')
    ap.add_argument('--table', default='finance-journal-entries', help='Journal entries table name')
    ap.add_argument('--force', action='store_true', help='Re-index docs that already have a text doc')
    ap.add_argument('--include-images', action='store_true',
                    help='Transcribe image receipts with Claude (one paid Sonnet call per image)')
    ap.add_argument('--limit', type=int, default=None, help='Stop after writing/reindexing N documents')
    ap.add_argument('--dry-run', action='store_true', help='Report what would happen; write nothing, call no model')
    args = ap.parse_args(argv)

    s3 = boto3.client('s3')
    # Region pinned to match the Lambdas (stack is pinned to us-east-1 in bin/app.ts).
    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1',
                           config=Config(retries={'max_attempts': 8, 'mode': 'adaptive'}))
    table = boto3.resource('dynamodb').Table(args.table)

    stats = {'written': 0, 'reindexed': 0, 'exists': 0, 'skipped': 0, 'failed': 0}
    docs = list_documents(table)
    for i, doc in enumerate(docs, start=1):
        if args.limit is not None and stats['written'] + stats['reindexed'] >= args.limit:
            print(f'Reached --limit {args.limit}; stopping.')
            break
        name = display_name(doc['fileKey'])   # local operator terminal, not CloudWatch
        try:
            stat, detail = process(doc, args, s3, bedrock)
        except ClientError as e:
            stat, detail = 'failed', f"ClientError {e.response.get('Error', {}).get('Code')}"
        except Exception as e:
            stat, detail = 'failed', type(e).__name__
        stats[stat] += 1
        if stat != 'exists':
            prefix = '[dry-run] ' if args.dry_run else ''
            print(f"{prefix}[{i}/{len(docs)}] {stat:<9} {name} ({doc['docId']}){' - ' + detail if detail else ''}")
    print(json.dumps(stats))
    return stats


if __name__ == '__main__':
    sys.exit(1 if main()['failed'] else 0)
