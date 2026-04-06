# Personal Finance & Accounting App — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Approach:** Standalone AWS CDK project at `/Users/jiaxin/ClaudeProjects/PersonalFinanceApp`, following the same architecture patterns as `AWSStoragePlatform` (CDK TypeScript + Lambda + DynamoDB + API Gateway + CloudFront + EventBridge)

---

## Overview

A single-user personal finance web app built on top of the existing AWS CDK platform. Uses double-entry bookkeeping as the core accounting model. Users can upload bank statements (PDF) or receipt photos, and Claude AI automatically parses and categorizes transactions into double-entry journal entries. Manual entry is also supported for cash and other non-digital expenses.

Delivered as a web app (new `/finance` path on the existing CloudFront distribution). PWA-ready for mobile home-screen install.

---

## Architecture

A standalone AWS CDK TypeScript project in its own folder, following the same architecture patterns as `AWSStoragePlatform`. Uses the same tech stack (CloudFront + S3 for frontend, API Gateway + Lambda for backend, DynamoDB for storage, EventBridge for scheduling, SNS for notifications) but as an independent CDK app — not an extension of the storage platform.

```
Frontend (CloudFront + S3, existing)
  └── /finance/*  ← new pages

Upload flow:
  Frontend → presigned URL → S3 finance-uploads/
    └── S3 event → ParseLambda (Python)
          ├── Duplicate detection (hash check vs DynamoDB)
          ├── Claude API (PDF/image → double-entry JSON)
          └── DynamoDB (status: PENDING)
  Frontend confirmation UI → API → ConfirmLambda → status: CONFIRMED

Manual entry:
  Frontend form → API Gateway → ManualEntryLambda → DynamoDB

Query & display:
  Frontend → API Gateway /finance/* → QueryLambda → DynamoDB

Alerts (Phase 2):
  EventBridge monthly cron → BudgetCheckLambda → SNS → email

AI Q&A (Phase 2):
  Frontend chat → API Gateway → AskLambda → Claude API (with DynamoDB context) → response
```

**AWS resources:**

| Resource | Purpose |
|----------|---------|
| S3 prefix `finance-uploads/` | Store uploaded PDFs and images |
| DynamoDB `finance-accounts` | Chart of accounts |
| DynamoDB `finance-journal-entries` | Journal entry headers |
| DynamoDB `finance-journal-lines` | Debit/credit lines per entry |
| DynamoDB `finance-budgets` | Alert rules (Phase 2) |
| Lambda `ParseLambda` (Python) | Call Claude API to parse files |
| Lambda `ManualEntryLambda` | Handle manual transaction entry |
| Lambda `QueryLambda` | Aggregations, reports, summaries |
| Lambda `ConfirmLambda` | Confirm/edit pending entries |
| Lambda `ExportLambda` | Generate CSV exports |
| Lambda `BudgetCheckLambda` | Monthly alert check (Phase 2) |
| Lambda `AskLambda` | AI Q&A over financial data (Phase 2) |
| API Gateway routes `/finance/*` | REST endpoints for frontend |

---

## Data Model

### `finance-accounts` — Chart of Accounts

```
PK: accountId  (e.g. "5100")
─────────────────────────────
name:       "餐饮"
type:       ASSET | LIABILITY | EQUITY | INCOME | EXPENSE
parentId:   "5000"        // parent account, null for root
isSystem:   boolean       // true = preset, false = user-created
currency:   "CNY"         // default currency for this account
```

**Preset account tree (user can add sub-accounts under any node):**
```
1000 资产 (ASSET)
  1100 银行存款
  1200 现金 / 支付宝 / 微信
  1300 投资
2000 负债 (LIABILITY)
  2100 信用卡
  2200 贷款
3000 权益 (EQUITY)
  3100 期初权益
4000 收入 (INCOME)
  4100 工资
  4200 副业
  4300 投资收益
  4400 其他收入
5000 支出 (EXPENSE)
  5100 餐饮
  5200 交通
  5300 购物
  5400 住房
  5500 医疗
  5600 娱乐
  5700 教育
  5800 其他支出
```

---

### `finance-journal-entries` — Journal Entry Headers

```
PK: entryId  (UUID)
─────────────────────────────
date:        "2026-04-01"
description: "招行4月账单"
source:      PDF | RECEIPT | MANUAL
status:      PENDING | CONFIRMED
fileKey:     "finance-uploads/abc.pdf"   // S3 key, null for MANUAL
fileHash:    "d41d8cd98f00b204..."       // MD5 of uploaded file, null for MANUAL; used for duplicate detection
tags:        ["春节旅行"]                 // Phase 2
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
amountCNY:   50.00           // Phase 2: converted amount
note:         "星巴克"
```

**Invariant:** for every confirmed entry, `SUM(DEBIT lines) == SUM(CREDIT lines)`. Enforced by ConfirmLambda before writing status CONFIRMED.

---

### `finance-budgets` — Alert Rules (Phase 2)

```
PK: budgetId
─────────────────────────────
accountId:     "5100"
name:          "餐饮月度预警"
windowMonths:  6
thresholdPct:  10
active:        boolean
```

---

## AI Parsing Flow

1. Frontend requests presigned S3 URL via `POST /finance/upload`
2. Frontend uploads file directly to S3
3. S3 event triggers `ParseLambda`
4. ParseLambda computes file hash (MD5); checks DynamoDB for duplicate — if found, rejects with error `DUPLICATE_FILE`
5. ParseLambda fetches full account tree from `finance-accounts`
6. ParseLambda sends file + account tree to Claude API with structured prompt:

```
你是一个专业会计。分析这份银行账单或小票，输出复式记账 JSON。
科目必须从以下列表中选择：[account tree JSON]
对每笔交易输出一个 journal entry，借贷必须平衡。
如不确定分类，在 note 中注明。

输出格式：
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

All pages under `/finance` path on existing CloudFront distribution.

### 1. Dashboard `/finance`
- Monthly income vs expense summary cards
- Expense breakdown pie chart (by top-level account)
- 6-month income/expense trend line chart
- Asset account balance cards (bank accounts, cash)
- Net worth figure (assets − liabilities)

### 2. Upload & Confirm `/finance/upload`
- Drag-and-drop or camera upload (PDF or image)
- Upload progress indicator
- Duplicate file warning if detected
- Parsed entries list: date, description, accounts, amount — all editable
- Batch confirm or line-by-line confirm

### 3. Transactions `/finance/transactions`
- Paginated list of all CONFIRMED journal entries, sorted by date
- Filter by account, date range, tag, amount range
- Expand row to see debit/credit lines
- Edit or delete individual entries

### 4. Manual Entry `/finance/new`
- Date, description fields
- Dynamic debit/credit line builder (add/remove rows)
- Account selector (searchable account tree)
- Real-time debit/credit balance indicator — must be zero to submit
- Optional: apply tags (Phase 2)

### 5. Alerts `/finance/budgets` (Phase 2)
- List of alert rules (account, window, threshold)
- Create / edit / delete rules
- Per-rule sparkline showing last 6 months + last month status

### 6. Reports `/finance/reports`
- Income Statement (损益表): revenue − expenses for selected period
- Balance Sheet (资产负债表): assets = liabilities + equity at a point in time
- Net Worth Timeline: monthly chart of (total assets − total liabilities)
- CSV export button (downloads all CONFIRMED entries for selected period)

### 7. Settings `/finance/settings`
- Account tree viewer
- Add custom sub-account under any preset account
- (Phase 2) Tag management

### 8. AI Q&A `/finance/ask` (Phase 2)
- Chat interface
- Ask natural language questions: "今年餐饮总花费？" "哪个月支出最高？"
- AskLambda fetches relevant aggregations from DynamoDB and passes to Claude for answer

---

## API Routes

New routes added to existing API Gateway under `/finance` prefix:

| Method | Path | Lambda | Description |
|--------|------|--------|-------------|
| POST | `/finance/upload` | ParseLambda | Get presigned URL; triggers async parse |
| GET | `/finance/entries` | QueryLambda | List journal entries (filter params) |
| POST | `/finance/entries` | ManualEntryLambda | Create manual journal entry |
| PUT | `/finance/entries/{id}/confirm` | ConfirmLambda | Confirm pending entry |
| PUT | `/finance/entries/{id}` | ManualEntryLambda | Edit existing entry |
| DELETE | `/finance/entries/{id}` | ManualEntryLambda | Delete entry |
| GET | `/finance/accounts` | QueryLambda | Get full account tree |
| POST | `/finance/accounts` | ManualEntryLambda | Create custom sub-account |
| GET | `/finance/summary` | QueryLambda | Aggregated stats for dashboard |
| GET | `/finance/reports/income-statement` | QueryLambda | Income statement for period |
| GET | `/finance/reports/balance-sheet` | QueryLambda | Balance sheet at date |
| GET | `/finance/reports/net-worth` | QueryLambda | Monthly net worth series |
| GET | `/finance/export/csv` | ExportLambda | Download CSV |
| GET | `/finance/budgets` | QueryLambda | List alert rules (Phase 2) |
| POST | `/finance/budgets` | ManualEntryLambda | Create alert rule (Phase 2) |
| PUT | `/finance/budgets/{id}` | ManualEntryLambda | Update alert rule (Phase 2) |
| DELETE | `/finance/budgets/{id}` | ManualEntryLambda | Delete alert rule (Phase 2) |
| POST | `/finance/ask` | AskLambda | AI Q&A (Phase 2) |

---

## Phase Plan

### Phase 1 — Core (Implement First)
- FinanceStack CDK setup (S3 prefix, 3 DynamoDB tables, API Gateway routes)
- Preset account tree seed script
- ParseLambda: duplicate detection + Claude API call + PENDING write
- ConfirmLambda: balance validation + CONFIRMED write
- ManualEntryLambda: manual entry + account CRUD
- QueryLambda: entries list, dashboard summary, reports (income statement, balance sheet, net worth)
- ExportLambda: CSV generation
- Frontend pages: Dashboard, Upload & Confirm, Transactions, Manual Entry, Reports, Settings

### Phase 2 — Enhanced (After Phase 1 ships)
- Custom tags on journal entries (tag field, filter by tag)
- Foreign currency fields (currency, exchangeRate, amountCNY on journal lines)
- Budget alert rules: `finance-budgets` table, BudgetCheckLambda, EventBridge monthly cron, SNS email
- AI Q&A: AskLambda + `/finance/ask` frontend page

---

## Key Constraints

- **Single user, no auth** for MVP — no login required, all data in shared tables
- **Debit/credit balance enforced** at ConfirmLambda — malformed entries cannot be confirmed
- **Claude API key** stored in AWS Secrets Manager, accessed by ParseLambda and AskLambda
- **PENDING entries** are never included in reports or balance calculations — only CONFIRMED
- **Duplicate detection** by file MD5 hash stored on the journal entry; same file cannot be uploaded twice
