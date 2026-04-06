# Personal Finance & Accounting App — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Approach:** Standalone AWS CDK project at `/Users/jiaxin/ClaudeProjects/PersonalFinanceApp`, following the same architecture patterns as `AWSStoragePlatform` (CDK TypeScript + Lambda + DynamoDB + API Gateway + CloudFront + EventBridge)

**Language:** All UI, code, prompts, and documentation are in English. Uploaded documents (bank statements, receipts) may be in any language — Claude handles them regardless.

---

## Overview

A single-user personal finance web app built with AWS CDK. Uses double-entry bookkeeping as the core accounting model. Users can upload bank statements (PDF) or receipt photos, and Claude AI automatically parses and categorizes transactions into double-entry journal entries. Manual entry is also supported for cash and other non-digital expenses.

Delivered as a web app served via CloudFront + S3. PWA-ready for mobile home-screen install.

---

## Architecture

A standalone AWS CDK TypeScript project in its own folder, following the same architecture patterns as `AWSStoragePlatform`. Uses the same tech stack (CloudFront + S3 for frontend, API Gateway + Lambda for backend, DynamoDB for storage, EventBridge for scheduling, SNS for notifications) but as an independent CDK app.

```
Frontend (CloudFront + S3)
  ├── /              → Dashboard
  ├── /upload        → Upload & Confirm
  ├── /transactions  → Transaction list
  ├── /new           → Manual entry
  ├── /reports       → Financial reports
  ├── /settings      → Account management
  ├── /budgets       → Alert rules (Phase 2)
  └── /ask           → AI Q&A (Phase 2)

Upload flow:
  Frontend → presigned URL → S3 uploads/
    └── S3 event → ParseLambda (Python)
          ├── Duplicate detection (MD5 hash check vs DynamoDB)
          ├── Claude API (PDF/image → double-entry JSON)
          └── DynamoDB (status: PENDING)
  Frontend confirmation UI → API → ConfirmLambda → status: CONFIRMED

Manual entry:
  Frontend form → API Gateway → ManualEntryLambda → DynamoDB

Query & display:
  Frontend → API Gateway /api/* → QueryLambda → DynamoDB

Alerts (Phase 2):
  EventBridge monthly cron → BudgetCheckLambda → SNS → email

AI Q&A (Phase 2):
  Frontend chat → API Gateway → AskLambda → Claude API (with DynamoDB context) → response
```

**AWS resources:**

| Resource | Purpose |
|----------|---------|
| S3 bucket `finance-app` | Frontend static files + uploaded statements/receipts |
| DynamoDB `finance-accounts` | Chart of accounts |
| DynamoDB `finance-journal-entries` | Journal entry headers |
| DynamoDB `finance-journal-lines` | Debit/credit lines per entry |
| DynamoDB `finance-budgets` | Alert rules (Phase 2) |
| Lambda `ParseLambda` (Python) | Call Claude API to parse uploaded files |
| Lambda `ManualEntryLambda` | Handle manual entry and account CRUD |
| Lambda `QueryLambda` | Aggregations, reports, summaries |
| Lambda `ConfirmLambda` | Validate balance and confirm pending entries |
| Lambda `ExportLambda` | Generate CSV exports |
| Lambda `BudgetCheckLambda` | Monthly alert check (Phase 2) |
| Lambda `AskLambda` | AI Q&A over financial data (Phase 2) |
| API Gateway | REST endpoints under `/api/*` |
| CloudFront | CDN for frontend + API routing |
| Secrets Manager | Store Claude API key |

---

## Data Model

### `finance-accounts` — Chart of Accounts

```
PK: accountId  (e.g. "5100")
─────────────────────────────
name:       "Dining"
type:       ASSET | LIABILITY | EQUITY | INCOME | EXPENSE
parentId:   "5000"        // parent account, null for root
isSystem:   boolean       // true = preset, false = user-created
currency:   "CNY"         // default currency for this account
```

**Preset account tree (user can add sub-accounts under any node):**
```
1000 Assets (ASSET)
  1100 Bank Accounts
  1200 Cash / Alipay / WeChat Pay
  1300 Investments
2000 Liabilities (LIABILITY)
  2100 Credit Cards
  2200 Loans
3000 Equity (EQUITY)
  3100 Opening Equity
4000 Income (INCOME)
  4100 Salary
  4200 Side Income
  4300 Investment Returns
  4400 Other Income
5000 Expenses (EXPENSE)
  5100 Dining
  5200 Transportation
  5300 Shopping
  5400 Housing
  5500 Healthcare
  5600 Entertainment
  5700 Education
  5800 Other Expenses
```

---

### `finance-journal-entries` — Journal Entry Headers

```
PK: entryId  (UUID)
─────────────────────────────
date:        "2026-04-01"
description: "Bank of China - April Statement"
source:      PDF | RECEIPT | MANUAL
status:      PENDING | CONFIRMED
fileKey:     "uploads/abc.pdf"             // S3 key, null for MANUAL
fileHash:    "d41d8cd98f00b204..."         // MD5 of uploaded file, null for MANUAL; used for duplicate detection
tags:        ["Japan Trip 2026"]           // Phase 2
createdAt:   timestamp
```

---

### `finance-journal-lines` — Debit/Credit Lines

```
PK: entryId   SK: lineId
─────────────────────────────
accountId:    "5100"
direction:    DEBIT | CREDIT
amount:       50.00
currency:     "CNY"          // Phase 2: original currency
exchangeRate: 1.0            // Phase 2: rate to CNY
amountCNY:    50.00          // Phase 2: converted amount
note:         "Starbucks"
```

**Invariant:** for every confirmed entry, `SUM(DEBIT lines) == SUM(CREDIT lines)`. Enforced by ConfirmLambda before setting status to CONFIRMED.

---

### `finance-budgets` — Alert Rules (Phase 2)

```
PK: budgetId
─────────────────────────────
accountId:     "5100"
name:          "Dining Monthly Alert"
windowMonths:  6
thresholdPct:  10
active:        boolean
```

---

## AI Parsing Flow

1. Frontend requests presigned S3 URL via `POST /api/upload`
2. Frontend uploads file directly to S3
3. S3 event triggers `ParseLambda`
4. ParseLambda computes file MD5 hash; checks `finance-journal-entries` for duplicate — if found, rejects with error `DUPLICATE_FILE`
5. ParseLambda fetches full account tree from `finance-accounts`
6. ParseLambda sends file + account tree to Claude API with structured prompt:

```
You are a professional accountant. Analyze this bank statement or receipt and output
double-entry bookkeeping journal entries in JSON format.

Accounts must be selected from the following list: [account tree JSON]

For each transaction, produce one journal entry with balanced debit and credit lines.
If classification is uncertain, add a note.

Output format:
{
  "entries": [
    {
      "date": "YYYY-MM-DD",
      "description": "...",
      "lines": [
        { "accountId": "...", "direction": "DEBIT|CREDIT", "amount": 0.00, "note": "..." }
      ]
    }
  ]
}
```

7. ParseLambda validates debit/credit balance for each entry; writes to DynamoDB with `status: PENDING`
8. Frontend shows pending entries; user reviews, edits accounts/amounts, then confirms
9. `ConfirmLambda` re-validates balance, sets `status: CONFIRMED`

---

## Frontend Pages

### 1. Dashboard `/`
- Monthly income vs expense summary cards
- Expense breakdown pie chart (by top-level account)
- 6-month income/expense trend line chart
- Asset account balance cards (bank accounts, cash)
- Net worth figure (assets − liabilities)

### 2. Upload & Confirm `/upload`
- Drag-and-drop or camera upload (PDF or image)
- Upload progress indicator
- Duplicate file warning if detected
- Parsed entries list: date, description, accounts, amount — all editable
- Batch confirm or line-by-line confirm

### 3. Transactions `/transactions`
- Paginated list of all CONFIRMED journal entries, sorted by date
- Filter by account, date range, tag, amount range
- Expand row to see debit/credit lines
- Edit or delete individual entries

### 4. Manual Entry `/new`
- Date, description fields
- Dynamic debit/credit line builder (add/remove rows)
- Account selector (searchable account tree)
- Real-time debit/credit balance indicator — must be zero to submit
- Optional: apply tags (Phase 2)

### 5. Reports `/reports`
- Income Statement: revenue − expenses for selected period
- Balance Sheet: assets = liabilities + equity at a point in time
- Net Worth Timeline: monthly chart of (total assets − total liabilities)
- CSV export button (downloads all CONFIRMED entries for selected period)

### 6. Settings `/settings`
- Account tree viewer
- Add custom sub-account under any preset account
- (Phase 2) Tag management

### 7. Alerts `/budgets` (Phase 2)
- List of alert rules (account, window, threshold)
- Create / edit / delete rules
- Per-rule sparkline showing last 6 months + last month status

### 8. AI Q&A `/ask` (Phase 2)
- Chat interface
- Ask natural language questions: "How much did I spend on dining this year?" "Which month had the highest expenses?"
- AskLambda fetches relevant aggregations from DynamoDB and passes to Claude for answer

---

## API Routes

| Method | Path | Lambda | Description |
|--------|------|--------|-------------|
| POST | `/api/upload` | ParseLambda | Get presigned URL; triggers async parse |
| GET | `/api/entries` | QueryLambda | List journal entries (filter params) |
| POST | `/api/entries` | ManualEntryLambda | Create manual journal entry |
| PUT | `/api/entries/{id}/confirm` | ConfirmLambda | Confirm pending entry |
| PUT | `/api/entries/{id}` | ManualEntryLambda | Edit existing entry |
| DELETE | `/api/entries/{id}` | ManualEntryLambda | Delete entry |
| GET | `/api/accounts` | QueryLambda | Get full account tree |
| POST | `/api/accounts` | ManualEntryLambda | Create custom sub-account |
| GET | `/api/summary` | QueryLambda | Aggregated stats for dashboard |
| GET | `/api/reports/income-statement` | QueryLambda | Income statement for period |
| GET | `/api/reports/balance-sheet` | QueryLambda | Balance sheet at date |
| GET | `/api/reports/net-worth` | QueryLambda | Monthly net worth series |
| GET | `/api/export/csv` | ExportLambda | Download CSV |
| GET | `/api/budgets` | QueryLambda | List alert rules (Phase 2) |
| POST | `/api/budgets` | ManualEntryLambda | Create alert rule (Phase 2) |
| PUT | `/api/budgets/{id}` | ManualEntryLambda | Update alert rule (Phase 2) |
| DELETE | `/api/budgets/{id}` | ManualEntryLambda | Delete alert rule (Phase 2) |
| POST | `/api/ask` | AskLambda | AI Q&A (Phase 2) |

---

## Phase Plan

### Phase 1 — Core (Implement First)
- CDK stack setup (S3, 3 DynamoDB tables, API Gateway, CloudFront)
- Preset account tree seed script
- ParseLambda: duplicate detection + Claude API call + PENDING write
- ConfirmLambda: balance validation + CONFIRMED write
- ManualEntryLambda: manual entry + account CRUD
- QueryLambda: entries list, dashboard summary, reports (income statement, balance sheet, net worth)
- ExportLambda: CSV generation
- Frontend: Dashboard, Upload & Confirm, Transactions, Manual Entry, Reports, Settings

### Phase 2 — Enhanced (After Phase 1 ships)
- Custom tags on journal entries (tag field, filter by tag)
- Foreign currency fields (currency, exchangeRate, amountCNY on journal lines)
- Budget alert rules: `finance-budgets` table, BudgetCheckLambda, EventBridge monthly cron, SNS email
- AI Q&A: AskLambda + `/ask` frontend page

---

## Key Constraints

- **Single user, no auth** for MVP — no login required, all data in shared tables
- **All UI and code in English** — uploaded documents may be in any language; Claude parses them regardless
- **Debit/credit balance enforced** at ConfirmLambda — malformed entries cannot be confirmed
- **Claude API key** stored in AWS Secrets Manager, accessed by ParseLambda and AskLambda
- **PENDING entries** are never included in reports or balance calculations — only CONFIRMED
- **Duplicate detection** by file MD5 hash stored on the journal entry; same file cannot be uploaded twice
