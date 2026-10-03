#!/usr/bin/env python3
"""Delete all vectors (and the manifest) for one document. Runs with operator credentials.

Usage:
  python scripts/delete_document_vectors.py --bucket <BucketName> --vector-bucket <VectorBucketName> --doc-id <docId>

docId is the file hash for owner uploads, or demo-{sessionId}-{fileHash} for demo sessions.

Side effects to know about:
  - text/{docId}.json is left in place, so a later `backfill_index.py --force` re-indexes it.
  - Entries keep evidence[0].chunkKey values that now point at deleted vectors.
  - If the manifest is missing, the whole index is scanned (logged as manifest_missing_full_scan).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lambda', 'common'))

import boto3  # noqa: E402

from penny_common.vectors import delete_document_vectors  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bucket', required=True)
    ap.add_argument('--vector-bucket', required=True)
    ap.add_argument('--index', default='penny-docs-v1')
    ap.add_argument('--doc-id', required=True)
    args = ap.parse_args(argv)
    n = delete_document_vectors(boto3.client('s3'), boto3.client('s3vectors', region_name='us-east-1'),
                                args.bucket, args.vector_bucket, args.index, args.doc_id)
    print(f'Deleted {n} vectors for {args.doc_id}')
    return n


if __name__ == '__main__':
    main()
