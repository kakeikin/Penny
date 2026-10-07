# Penny RAG — Operations Runbook

Operator procedures for the evidence layer and the S3 Vectors index. The design is in
`docs/superpowers/specs/2026-10-01-penny-rag-evidence-design.md`. Every command below runs with
operator credentials in `us-east-1`, which is the stack's pinned region (`bin/app.ts`).

Stack outputs used below (`npx cdk deploy` prints them, or run
`aws cloudformation describe-stacks --stack-name FinanceStack`):
`BucketName`, `VectorBucketName`, `VectorIndexName`, `IndexDlqUrl`.

## Deploy

1. Start Docker, then run `./scripts/build-layer.sh`. **This is required**, and `cdk synth/deploy`
   refuses to run if the layer is stale (`lib/layer-check.ts`). A stale layer makes every Lambda
   that imports `penny_common` fail at init, and QueryLambda serves every GET route.
2. Run `npx cdk deploy`.

## Re-index a single document

Copying a `text/` object onto itself re-fires IndexLambda. Indexing is idempotent, because keys
are deterministic and a manifest diff removes stale chunks.

```bash
aws s3 cp s3://<BucketName>/text/<docId>.json s3://<BucketName>/text/<docId>.json --metadata-directive REPLACE
```

`docId` is the file's MD5 for owner uploads, or `demo-{sessionId}-{md5}` for demo uploads.

## Replay the IndexLambda DLQ

1. Inspect messages: `aws sqs receive-message --queue-url <IndexDlqUrl> --max-number-of-messages 10`.
   Each body is the original S3 event, so the object key gives you the docId.
2. Fix the cause. Check the IndexLambda logs for `index_failed` and its `errorType`.
3. Re-index each affected document as above, then delete the messages you handled.

## Change the vector index (dimension, metadata keys, distance)

Every `AWS::S3Vectors::Index` property forces a replacement, so the index name carries a version.

1. In `lib/finance-stack.ts`, bump `VECTOR_INDEX_NAME` (for example `penny-docs-v2`) and change the
   property, such as `EMBED_DIMENSIONS`. Both flow to IndexLambda as environment variables, so
   the code and the index cannot disagree.
2. Rebuild the layer and deploy. CloudFormation creates the new, empty index, switches IndexLambda
   to it, and deletes the old index.
3. Repopulate:
   ```bash
   python scripts/backfill_index.py --bucket <BucketName> --force --dry-run
   ```
   ```bash
   python scripts/backfill_index.py --bucket <BucketName> --force
   ```
   - pypdf documents are regenerated.
   - Documents with Claude transcripts, and `text/` documents whose entries were all deleted,
     are re-uploaded unchanged. No model calls are made.
4. Watch the IndexLambda logs and confirm `IndexDlqUrl` stays empty.
5. From now on, pass the new name to cleanup: `scripts/delete_document_vectors.py --index penny-docs-v2 ...`.

To verify on first use: confirm that CloudFormation can delete the old *non-empty* index during
replacement cleanup. If it cannot, empty the old index with the delete script, then retry the stack update.

## Delete one document's vectors

```bash
python scripts/delete_document_vectors.py --bucket <BucketName> --vector-bucket <VectorBucketName> \
    --index <VectorIndexName> --doc-id <docId>
```

This leaves `text/<docId>.json` in place, so a later `--force` backfill re-indexes the document.

## Guardrails

- Don't run `backfill_index.py --force` while live uploads of the same files are in flight (spec §12).
- `--include-images` makes one paid Sonnet call per image receipt. Use `--dry-run` and `--limit` first.
- The public deployment must contain synthetic data only, because there is no auth (spec §12).
