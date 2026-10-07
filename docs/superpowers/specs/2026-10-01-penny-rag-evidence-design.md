# Penny RAG & Evidence Linking — Design Spec

**Date:** 2026-10-01
**Status:** Approved in brainstorming, pending written-spec review
**Goal:** Add citation-backed retrieval over users' original financial documents and per-transaction source evidence ("vouching"), while keeping Penny's serverless cost profile near $0. Low cost is a first-class feature of this project.

---

## 1. Scope

### In scope (v1)
1. **Evidence linking (vouching):** every parsed journal entry links to the exact line in the original document it came from.
2. **Document RAG:** original uploaded documents (bank statement PDFs, receipt images) are extracted, chunked, embedded, and stored in S3 Vectors.
3. **Tool-calling advisor:** the existing AdvisorLambda becomes a Bedrock Converse tool-use agent with three typed, read-only tools.
4. **Citation validation:** deterministic server-side check that citations reference real tool results.
5. **Evidence API + UI:** "View source evidence" on transaction detail; clickable citations in advisor answers.
6. **Evaluation:** deterministic metrics on a versioned synthetic dataset, including cost-per-question comparison.
7. **CI:** GitHub Actions running pytest, jest, and `cdk synth`.

### Out of scope (v1)
- LLM-generated SQL or any free-form query language.
- LLM-as-judge evaluation.
- Multi-user auth / tenant isolation (Penny is currently single-user; noted as a known limitation).
- Audit-style risk memo generation (candidate for a follow-up spec that builds on this one).
- Calibrated confidence scores on evidence.
- A `finance-documents` table with parse/index status (v1 derives document identity from `fileHash`; revisit if document-level status is needed).
- Splitting `POST /api/upload` (presigned URL) out of ParseLambda into its own UploadLambda — a worthwhile cleanup, tracked separately.
- A user-facing "delete document" feature (none exists today).
- Fixing float arithmetic in Lambdas not touched by this spec (`confirm`, `export`) — tracked separately.

---

## 2. Architecture

```
Upload ──► S3 uploads/ ──► ParseLambda (existing, modified)
                            ├─ pypdf text extraction per page (text-based PDFs)
                            ├─ Claude call (single call, as today) returns:
                            │    · entries (existing schema)
                            │    · per-entry evidence: {page, text}
                            │    · transcript for images and scanned PDF pages
                            ├─ validate evidence text against pypdf text (text PDFs only)
                            ├─ write S3 text/{docId}.json
                            └─ write entries to DynamoDB (with evidence[])
                                      │
                           S3 event (prefix text/)
                                      ▼
                            IndexLambda (new)
                            ├─ read text/{docId}.json
                            ├─ chunk → mask → embed (Titan Text Embeddings V2, 512 dims)
                            ├─ PutVectors → S3 Vectors index "penny-docs-v1"
                            ├─ write manifests/{docId}.json (all vector keys)
                            └─ backfill evidence[].chunkKey on entries (DynamoDB UpdateItem)
                            failures → 2 async retries → SQS DLQ

Ask ──► POST /api/advisor ──► AdvisorLambda (rewritten as agent)
                            ├─ Bedrock Converse API tool-use loop (max 4 rounds)
                            ├─ tools: search_documents | get_spending_summary | find_transactions
                            └─ citation validator → response

Detail ──► GET /api/entries/{id}/evidence ──► QueryLambda
                            └─ evidence[] each with presigned fileUrl + page
```

### Key design decisions
- **Parse and index are decoupled.** ParseLambda only writes text to S3; indexing is triggered by an S3 event. An indexing failure never blocks bookkeeping, and indexing can be re-run independently.
- **Vouching does not use the vector store.** Evidence is captured deterministically at parse time from the same Claude call that already reads the document — zero extra model calls.
- **Semantic retrieval is only for open-ended questions.** Deterministic links for audit evidence; similarity search for exploration.
- **Write-prefix isolation prevents trigger loops.** ParseLambda writes to `text/{docId}.json`. IndexLambda reads from `text/{docId}.json` but never writes to the `text/` prefix. IndexLambda writes only to `manifests/` (no event trigger), S3 Vectors, and DynamoDB.
- **No custom CloudWatch metrics.** Each costs ~$0.30/metric/month, which conflicts with the cost goal. Structured logs + Logs Insights only.

### Removed CDK resources
- `ClaudeApiKey` secret (`finance/claude-api-key`): defined but read by no Lambda, since Claude is invoked via Bedrock with IAM. Removing it saves ~$0.40/month and removes a misleading resource.

### New CDK resources
- S3 Vector bucket + index `penny-docs-v1` (L1 constructs), dimension 512, cosine distance. The name is versioned because every index property forces replacement: a schema change means bumping to `-v2` and running the backfill.
- IndexLambda (Python), S3 notification on `text/` prefix only.
- SQS DLQ for IndexLambda async failures.
- New API route `GET /api/entries/{id}/evidence` on QueryLambda.
- Least-privilege IAM: IndexLambda gets `s3vectors:PutVectors`, `s3vectors:DeleteVectors`, read on `text/`, write on `manifests/`, `bedrock:InvokeModel` on the Titan embedding model only, `dynamodb:UpdateItem` on entries. AdvisorLambda gets `s3vectors:QueryVectors` + `s3vectors:GetVectors` (required by S3 Vectors whenever a query uses a metadata filter or returns metadata) and Titan invoke.

---

## 3. Data Model

### docId
`docId` = `doc_id_for(fileHash, sessionId)`: the file's MD5 for owner uploads, and `demo-{sid}-{fileHash}` for demo sessions. This means a visitor uploading the same file as the owner can never overwrite the owner's text doc or vectors. All entries parsed from one upload share the same `docId`. Duplicate detection (file and entry) is scoped to the session and pages through the full Scan. The previous `Limit=1` + `FilterExpression` only checked one item, which made it ineffective.

### Demo-session isolation
Penny already isolates demo visitors: demo uploads land under `uploads/demo-{sid}/`, their entries carry `sessionId`, and QueryLambda filters by the `X-Session-Id` header (owner data has no `sessionId`). RAG must preserve this:
- `text/{docId}.json` records `sessionId` (null for owner).
- Every vector carries filterable metadata `sessionId` = the demo sid, or the literal `"owner"`.
- `search_documents` always filters on the caller's `sessionId`; there is no unfiltered query path.
- The evidence API returns 404 when the entry's `sessionId` does not match the caller's.

### Money representation
- DynamoDB already stores line amounts as decimal strings (`str(amount)` in ParseLambda/ConfirmLambda). This stays.
- ParseLambda parses Claude's JSON with `json.loads(..., parse_float=Decimal)` so amounts never pass through float.
- All code added or modified by this spec uses `Decimal` for money and serializes money as strings in API responses. No `float` for money.

### Entry: new `evidence` field
The entries table already has a `source` field (upload vs. manual), so the new field is named `evidence`. It is always an array, even in v1 where it typically holds one item. No migration: entries without the field are treated as "no evidence" (e.g. manual entries, pre-existing data).

```json
"evidence": [
  {
    "docId": "<fileHash>",
    "sourceType": "bank_statement",
    "page": 2,
    "text": "03/14 ABC UTILITIES  -120.00",
    "chunkKey": null
  }
]
```

- `sourceType`: `bank_statement` | `receipt`.
- `chunkKey`: nullable. Null at parse time; backfilled by IndexLambda once chunking completes.
- Evidence holds only source-document material. Model-generated explanations (e.g. why a category was chosen) are not evidence and are not stored here.
- No `confidence` field in v1: a model-reported confidence is uncalibrated and indefensible. A v2 option is a deterministic `matched` flag (evidence text contains the entry's exact amount and date).

### S3 `text/{docId}.json` (written by ParseLambda only)
```json
{
  "docId": "<fileHash>",
  "fileKey": "uploads/....pdf",
  "fileName": "bank_statement_mar.pdf",
  "docType": "bank_statement",
  "uploadedAt": "2026-03-20T10:00:00Z",
  "statementPeriod": { "start": "2026-03-15", "end": "2026-04-14" },
  "pages": [
    { "page": 1, "text": "...", "extractor": "pypdf" },
    { "page": 2, "text": "...", "extractor": "claude" }
  ],
  "entries": [
    { "entryId": "...", "evidenceText": "03/14 ABC UTILITIES  -120.00", "page": 2 }
  ]
}
```
`statementPeriod` is optional (null for receipts or when not found). `entries` lets IndexLambda backfill `chunkKey` without scanning the entries table.

### S3 `manifests/{docId}.json` (written by IndexLambda only)
```json
{ "docId": "<fileHash>", "chunkerVersion": "1", "keys": ["<docId>#p1#c0", "<docId>#p2#c0", "<docId>#p2#c1"] }
```

### S3 Vectors record
- **key:** `{docId}#p{page}#c{n}` — deterministic, so re-indexing overwrites instead of duplicating (idempotent).
- **vector:** Titan Text Embeddings V2, 512 dimensions. Chosen over 1024 because storage and query cost scale with dimension; the eval includes a 512 vs 1024 ablation to justify this.
- **filterable metadata:** `docId`, `docType`, `yearMonth`, `sessionId`.
- **non-filterable metadata:** `text`, `page`, `fileKey`, `fileName` — so query results return source text without a second S3 read.

---

## 4. Ingestion Details

### Text extraction (ParseLambda)
1. Before calling Claude, run pypdf on each PDF page.
2. A page with fewer than 20 non-whitespace characters is treated as scanned. Scanned pages and all image receipts are transcribed by Claude **in the same parse call** (`extractor: "claude"`).
3. Claude's response adds, per entry, `evidence: {page, text}` — the verbatim line(s) from the document.

### Evidence validation (ParseLambda)
- This validation applies to text-based PDFs only (pages with `extractor: "pypdf"`).
- Normalize both strings (collapse whitespace, lowercase) and require the evidence text to appear as a substring of that page's pypdf text. On mismatch, drop that evidence item and log `evidence_rejected`.
- For scanned PDFs and image receipts, evidence validation is limited because the transcript itself comes from Claude. This is a documented limitation.

### Chunking (IndexLambda)
Structure-aware, not fixed-size:
- **Receipts:** one chunk per page (keeps `page` meaningful for citations); a page longer than ~8000 chars is chunked like a statement so no chunk exceeds the embedding input limit.
- **Bank statements:** per page, split on line boundaries into ~500-token chunks with a 2-line overlap. A transaction line is never split across chunks. A single line longer than the chunk budget (pypdf sometimes returns a page without newlines) is wrapped at whitespace.
- **Context header:** each chunk is prefixed with `[<fileName> | p<page> | <yearMonth>]` so numeric-only chunks remain retrievable.
- Chunker output is deterministic given the same input and `chunkerVersion`.

### yearMonth per chunk (priority order)
1. Majority month among transaction dates found in the chunk (ties → earliest month). A transaction date is a line-leading two-digit `NN/NN[/YY[YY]]` that forms a real calendar date, or a full ISO date anywhere. Full dates count only within 400 days before / 31 days after the reference date. A page is parsed as DD/MM when any line-leading date has a first field > 12 (UK/EU statements).
2. End month of `statementPeriod` (for chunks without transaction lines, e.g. a summary page).
3. Month of `uploadedAt`.

Rationale: a single statement can span two months, so a document-level period cannot assign a chunk to one month.

### Masking (IndexLambda, before embedding and storage)
- Runs of 8 or more consecutive digits (no `/`, `-`, `.`, or spaces inside) are treated as account/card identifiers.
- Preserve only the last 4 digits for long numeric identifiers: `1234567890123456` → `****3456`.
- Dates (`2026-03-14`, `03/14/2026`) and amounts (`1234.56`, `1,234.56`) are not affected.
- One shared `mask()` function is used by both Lambdas (shipped in the existing Python layer).
- **Order matters:** ParseLambda validates evidence against raw pypdf text first, then masks evidence text before writing it to entries and to `text/{docId}.json` `entries[]`. IndexLambda masks chunk text before embedding and storage. The chunkKey backfill therefore compares masked evidence text with masked chunk text.
- `text/{docId}.json` `pages[].text` stays unmasked: it is a derivative of the original file, which already sits unmasked in the same private bucket.

### chunkKey backfill (IndexLambda)
For each `entries[]` item in the text file, find the first chunk on the same page whose normalized, masked text contains the normalized (already masked) `evidenceText`, then `UpdateItem` that entry's `evidence[i].chunkKey`. No match → leave null and log.

### Vector deletion
Penny has no user-facing document deletion today, so v1 provides `delete_document_vectors(docId)` (in `penny_common.vectors`), used by `scripts/delete_document_vectors.py`; a future delete-document feature calls the same function. Re-indexing (`scripts/backfill_index.py --force`) needs no explicit delete: IndexLambda diffs the new keys against the old manifest and deletes stale keys. It reads `manifests/{docId}.json` and calls `DeleteVectors` with the exact keys in batches. If the manifest is missing, it falls back to listing the index and deleting keys whose `docId` metadata matches. It deletes the manifest afterwards. Re-indexing embeds first, then writes the manifest ahead as old ∪ new, puts the vectors, deletes the stale keys (old − new), and finally narrows the manifest to new. The manifest therefore lists every key in the index at every step. Both manifest writes re-read and merge, which shrinks the window for a concurrent re-index of the same document to a narrow race. Over-listed keys are harmless and get trimmed by the next re-index. S3 conditional writes would close the race fully.

### Backfill script
`scripts/backfill_index.py` writes `text/{docId}.json` for uploads that don't have one, which triggers indexing. It discovers documents through the entries table and groups them by `doc_id_for(fileHash, sessionId)`. Evidence links from existing entries are carried over so chunkKeys refill.
- PDFs use pypdf only, at no model cost. Unreadable PDFs and PDFs with no text layer at all are skipped.
- Image receipts need a Claude transcription. They are skipped unless `--include-images` is set, and `--dry-run` never calls Claude.
- `--force` re-indexes everything. This is the runbook after bumping the vector index to `-v2`.
  - Docs whose text came from pypdf are regenerated.
  - Docs that hold Claude transcripts are re-uploaded unchanged, so transcripts are never lost and no model is called.
  - Docs whose rebuild fails (for example the source is no longer readable) are also re-uploaded unchanged.
- `--limit N` caps one run.
- Failures are isolated per document: they are counted and the script exits non-zero.

## 5. Agent & API

### Agent loop (AdvisorLambda)
- Replace `invoke_model` with the Bedrock Converse API using tool use.
- Max 4 tool rounds. On exceeding, stop and return the best available answer with `truncated: true`.
- Per-call Bedrock timeout plus a total time budget under API Gateway's 29s limit.
- Model: keep `us.anthropic.claude-sonnet-4-6`; the eval compares Haiku 4.5 to decide on switching.
- The current approach of injecting 100 recent transactions into every prompt is removed.

### Tools (all read-only, typed parameters, no LLM-written queries)

| Tool | Parameters | Returns (each item carries a `ref`) |
|---|---|---|
| `search_documents` | `query`, `yearMonth?`, `docType?`, `topK` (1–8, default 5) | `{ref: "D1", type: "document", fileName, page, text, score, chunkKey}` |
| `get_spending_summary` | `startMonth`, `endMonth`, `groupBy: "month" \| "account"` | `{ref: "S1", type: "summary", groupBy, month \| accountName, amount}` |
| `find_transactions` | `startDate`, `endDate`, `accountId?`, `minAmount?`, `keyword?`, `limit` (1–20, default 10) | `{ref: "T1", type: "transaction", entryId, date, description, amount, evidence}` |

- **Ref prefixes:** `D` = document chunk, `T` = transaction entry, `S` = spending summary. Numbering is per request and unique across rounds.
- All money is computed with `Decimal` inside tools and returned as strings. The existing float arithmetic in the advisor is replaced.
- Invalid parameters (out-of-range limits, malformed dates) return a tool error, not an exception.

### Citation validator (deterministic, server-side)
1. Parse all `[ref]` markers in the answer.
2. Any ref not returned by a tool in this request is removed from the answer and counted in `invalidCitations`.
3. Money detection: a token counts as money if it has a currency symbol or code (`$ ¥ € £ USD CNY EUR GBP JPY`), two decimal places (`120.00`, `-120.00`, `1,234.56`), or accounting parentheses (`(120.00)`, `($120.00)`). Bare integers are not treated as money (`3 transactions`, `March 2026`).
4. If the answer contains money and zero valid citations → `evidenceStatus: "unsupported"`; otherwise `"supported"`.
5. The validator checks citation presence, not numeric correctness; numeric correctness is measured by the eval. Its false-negative rate is reported in the eval; a keyword-based rule (e.g. "total", "paid") is a v2 option if that rate is high.

### `POST /api/advisor` (existing route, extended response)
```json
{
  "answer": "March utilities were $120.00 [T1], matching the statement line [D1].",
  "citations": [
    { "ref": "T1", "type": "transaction", "entryId": "...", "text": "..." },
    { "ref": "D1", "type": "document", "fileName": "bank_statement_mar.pdf", "page": 2, "chunkKey": "...", "text": "..." }
  ],
  "toolsUsed": ["find_transactions", "search_documents"],
  "evidenceStatus": "supported",
  "truncated": false,
  "invalidCitations": 0,
  "usage": { "inputTokens": 1830, "outputTokens": 410, "estCostUsd": "0.0116" }
}
```
`estCostUsd` is computed with `Decimal` from a per-model price table in code and returned as a string.

### `GET /api/entries/{id}/evidence` (new, QueryLambda)
```json
{
  "evidence": [
    {
      "docId": "...",
      "sourceType": "bank_statement",
      "page": 2,
      "chunkKey": "...",
      "text": "03/14 ABC UTILITIES  -120.00",
      "fileUrl": "https://...presigned... (5 min)"
    }
  ]
}
```
`fileUrl` is per evidence item; presigning is deduplicated per `docId`. Entries without evidence return `{"evidence": []}`.

### Frontend
- Transaction detail: "View source evidence" shows evidence text and opens `fileUrl#page=N` in a new tab.
- Advisor answers: citations render as clickable chips reusing the same viewer.

---

## 6. Error Handling

| Situation | Behavior |
|---|---|
| Evidence text not found in pypdf page text | Drop that evidence item, log `evidence_rejected`, keep the entry |
| Malformed evidence or transcripts from Claude (bad page, non-string text, null lists, >500 chars) | Ignore that item and log the reason. Never raises, so bookkeeping always proceeds |
| Claude output truncated (`stop_reason == max_tokens`) while transcripts were requested | Retry once without transcripts (pre-RAG behaviour), log `transcripts_truncated` |
| PDF unreadable by pypdf (`PdfTextError`) | Claude still parses it for bookkeeping. No evidence, no text doc |
| `text/` write fails after entries are saved | Log `text_doc_write_failed`. Bookkeeping stands, and `backfill_index.py` can recover |
| One S3 record in a batch fails | Other records still process, and the first error is re-raised for async retry |
| IndexLambda failure | 2 async retries, then SQS DLQ; re-run is safe (deterministic keys) |
| Titan throttling | Exponential backoff; v1 embeds one chunk per call |
| No chunk contains an entry's evidence text | `chunkKey` stays null, log |
| Tool-level recoverable errors (no results, bad params, retrieval failure) | Return `toolResult` with `status: "error"` to the model |
| System-level Bedrock throttling / timeouts | Return HTTP 503 to the client |
| Tool rounds exceed 4 | Return answer with `truncated: true` |

---

## 7. Cost & Observability

### Cost model
The spec's cost table is filled with prices verified against AWS pricing pages during planning. Expected magnitude:
- **Per document (incremental):** pypdf free; Titan V2 embeddings (~$0.00002 / 1K tokens); S3 Vectors put + storage. Target: well under $0.001 per document on top of today's parse cost.
- **Per question:** dominated by Claude tokens. The eval measures old advisor (full context injection) vs. new agent (on-demand tools).
- **Fixed monthly cost:** target $0 beyond existing usage — no custom metrics, no always-on databases.

### Structured log (one line per advisor request)
```json
{"requestId":"...","rounds":2,"toolsUsed":["find_transactions","search_documents"],
 "retrievalMs":140,"topScore":"0.81","inputTokens":1830,"outputTokens":410,
 "estCostUsd":"0.0116","invalidCitations":0,"evidenceStatus":"supported","truncated":false,
 "questionLength":42}
```
Never log question text, document text, or evidence text. Saved Logs Insights queries live in `docs/`.

---

## 8. Testing

Follow existing conventions: `test/lambda/test_*.py` with pytest + `unittest.mock`; CDK tests in `test/finance-stack.test.ts` with jest.

| Unit | Key cases |
|---|---|
| Chunker | transaction lines never split; 2-line overlap; context header present; deterministic output |
| yearMonth | explicit transaction majority; mixed months → most frequent; tie → earliest; no transaction dates → statement period end month; nothing → upload month |
| Masking | 16-digit card → `****3456`; dates and amounts untouched; 7-digit runs untouched |
| Evidence validation | normalized match passes; mismatch dropped; scanned pages skipped |
| Keys / deletion | key format deterministic; deletion uses manifest keys; missing manifest → fallback path |
| Citation validator | unknown refs removed and counted; `$ ¥ € £`, `-120.00`, `(120.00)` detected; bare integers ignored; money without citations → unsupported |
| Tools | Decimal sums exact (e.g. `0.10 + 0.20 == 0.30`); out-of-range params rejected |
| Agent loop | mocked Converse: >4 rounds → truncated; tool error → toolResult error; Bedrock throttle → 503 |
| Evidence API | array response; presign deduped per docId; no evidence → empty array |
| Parse money | Claude JSON amounts load as `Decimal`; balance check exact without float tolerance |
| CDK | vector index exists (512, cosine); IndexLambda triggered only by `text/`; DLQ attached; IAM actions scoped as in §2; `ClaudeApiKey` secret no longer present |

---

## 9. Evaluation

Located in `eval/`. Runs against the deployed stack (expected < $1 per run). Not part of CI (needs AWS credentials, incurs cost).

### Dataset
- Synthetic documents only; real statements are never committed.
- Synthetic data is deterministic and versioned so repeated runs are comparable: a fixed-seed generator script produces the files, and the generated files are committed.
- Corpus: 3 text-based bank statement PDFs, 1 scanned PDF, 5 receipt images.
- `eval/queries.json`: 30 questions, each with gold `docId`/`page` (retrieval), expected tools, and exact expected numeric answers.

### Metrics (all deterministic)
- **Retrieval:** hit@3, hit@5, MRR. Ablations: 512 vs 1024 dimensions; context header vs none.
- **Evidence linking:** page + text accuracy of `evidence` vs gold; count of rejected fabricated evidence.
- **Agent:** tool-selection accuracy; numeric exactness (answer numbers vs gold, compared as `Decimal`); citation validity rate; unsupported rate; citation-validator false-negative rate.
- **Cost & latency:** tokens and `estCostUsd` per question, old advisor vs new agent; p50/p95 latency; Sonnet 4.6 vs Haiku 4.5.

### Output
`docs/evaluation-results.md`, recording model IDs, embedding dimension, `chunkerVersion`, and dataset version with each run. Resume figures come only from this file.

---

## 10. CI

New GitHub Actions workflow: pytest (Lambda tests), jest (CDK tests), `cdk synth`. Eval is run manually.

---

## 11. Assumptions to Verify During Planning

Verified 2026-10-01:
1. ✅ CDK L1 `aws_s3vectors.CfnVectorBucket` / `CfnIndex` exist in aws-cdk-lib 2.248 (`dataType`, `dimension`, `distanceMetric`, `metadataConfiguration.nonFilterableMetadataKeys`, `attrIndexArn`).
2. ✅ Decision: pin boto3 1.43.x in the layer rather than rely on the runtime's bundled version. The layer is built in the AWS SAM Python 3.12 Docker image, because the current locally built layer contains macOS binaries and `http_ece` has no Linux wheel.
3. ✅ `PutVectors`/`DeleteVectors`: max 500 per call. `ListVectors` has no prefix/metadata filter. Filter syntax is Mongo-style (`{"$and": [{"sessionId": "owner"}, {"yearMonth": "2026-03"}]}`). Filterable metadata ≤ 2 KB, total ≤ 40 KB, ≤ 10 non-filterable keys per index.

Still to verify:
4. Current prices for Titan Text Embeddings V2, S3 Vectors, Sonnet 4.6, and Haiku 4.5 (for the cost table and `estCostUsd`).
5. Titan V2 supports 512-dimension output via request parameter.

## 12. Known Limitations (v1)
- Single owner plus anonymous demo sessions: no auth; isolation is by the unauthenticated `X-Session-Id` header, which a caller can spoof. The API is public and S3 CORS allows `*`.
- The CloudFront default behavior serves the whole app bucket via OAC, so `uploads/`, `text/`, and `manifests/` objects are reachable by anyone who knows the key. Keys contain UUIDs or MD5 hashes and are not enumerable, but moving the frontend to its own prefix/bucket is a recommended follow-up.
- **Deployment constraint:** because there is no auth, the public deployment (CloudFront live demo) must only contain synthetic data. Real financial documents must never be uploaded to it. Presigned evidence URLs expire after 5 minutes.
- Evidence for image receipts and scanned PDF pages is not independently validated.
- Citation validator checks presence, not correctness, of citations.
- yearMonth parsing does not understand textual months (`Mar 16`), CJK dates (`2026年3月14日`), or `YYYY/MM/DD`; such chunks use the statement-period or upload-month fallback. An all-ambiguous DD/MM page (every day ≤ 12) is read as MM/DD. Single-digit `M/D` dates and dates that don't start a line (`Posted 03/16`) are ignored on purpose, so footers like `Page 1/3` aren't misread.
- ParseLambda validates and prepares every entry before writing, so malformed model output can never leave a partially booked document. A *transient* DynamoDB failure in the middle of the write pass still can, and the retry is then skipped as a duplicate file. A completion marker with deterministic entry IDs is the follow-up fix.
- Duplicate checks Scan the whole entries table (paginated) for every upload and every entry. That is fine at personal scale. A GSI on `fileHash` / `entryHash` is the follow-up.
- Concurrent IndexLambda runs for the same docId use re-read-and-merge rather than S3 conditional writes. The manifest stays a superset of the index, but the vector *content* of a shared key is last-writer-wins. Avoid running `backfill_index.py --force` while live uploads of the same files are in flight.
- Deleting stale keys assumes `DeleteVectors` ignores keys that don't exist. Verify this in the Task 14 smoke test.
- Masking misses identifiers printed with internal spaces or dashes (`4111 1111 1111 1111`); compact `YYYYMMDD` dates are masked as identifiers by design.
- Masking is context-dependent (an 8-digit run followed by `.90` is treated as an amount), so in rare cases masked evidence text and masked chunk text differ and the chunkKey backfill leaves `chunkKey` null. This fails safe.
