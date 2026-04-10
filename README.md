# Penny — AI-Powered Personal Finance App

A serverless personal finance web app built on AWS CDK. Uses **double-entry bookkeeping** as its core accounting model and **Claude AI** to automatically parse bank statements and receipts into structured journal entries.

Built for Northeastern University CS6620 Cloud Computing (Spring 2026).

---

## Features

- **AI Receipt & Statement Parsing** — Upload a PDF bank statement or receipt photo; Claude extracts and categorizes every transaction into balanced debit/credit entries
- **Duplicate Detection** — MD5 hash check prevents re-importing the same file twice
- **Double-Entry Bookkeeping** — Every transaction is a balanced journal entry; enforced by `ConfirmLambda` before persistence
- **Manual Entry** — Add cash or non-digital transactions via a human-friendly form
- **Foreign Currency Support** — Records original currency and exchange rate alongside CNY amounts
- **Custom Tags & Filters** — Tag entries (e.g. "Japan Trip 2026") and filter by account, date, or tag
- **Budget Alerts** — Monthly EventBridge cron checks spending against user-defined limits and sends SNS email alerts *(Phase 2)*
- **AI Financial Advisor** — Natural-language Q&A over your own financial data powered by Claude *(Phase 2)*
- **CSV Export** — Download transactions for any date range
- **PWA-ready** — Installable on mobile home screen via CloudFront-served static frontend

---

## Architecture

```
Frontend (CloudFront + S3)
  ├── /              → Dashboard
  ├── /upload        → Upload & Confirm
  ├── /transactions  → Transaction list
  ├── /new           → Manual entry
  ├── /reports       → Financial reports
  ├── /budgets       → Alert rules (Phase 2)
  └── /ask           → AI Q&A (Phase 2)

Upload flow:
  Frontend → presigned URL → S3 uploads/
    └── S3 event → ParseLambda (Python)
          ├── Duplicate detection (MD5 hash vs DynamoDB)
          ├── Claude API (PDF/image → double-entry JSON)
          └── DynamoDB (status: PENDING)
  Confirmation UI → API → ConfirmLambda → status: CONFIRMED

Query & display:
  Frontend → API Gateway /api/* → QueryLambda → DynamoDB
```

### AWS Resources

| Resource | Purpose |
|---|---|
| S3 | Frontend static files + uploaded statements/receipts |
| DynamoDB `finance-accounts` | Chart of accounts |
| DynamoDB `finance-journal-entries` | Journal entry headers |
| DynamoDB `finance-journal-lines` | Debit/credit lines per entry |
| DynamoDB `finance-budgets` | Budget alert rules (Phase 2) |
| Lambda `ParseLambda` (Python) | Call Claude API to parse uploaded files |
| Lambda `ConfirmLambda` | Validate balance and confirm pending entries |
| Lambda `ManualEntryLambda` | Manual entry and account CRUD |
| Lambda `QueryLambda` | Aggregations, reports, summaries |
| Lambda `ExportLambda` | CSV export |
| Lambda `BudgetCheckLambda` | Monthly alert check (Phase 2) |
| Lambda `AskLambda` | AI Q&A over financial data (Phase 2) |
| API Gateway | REST endpoints under `/api/*` |
| CloudFront | CDN for frontend + API routing |
| Secrets Manager | Claude API key |

---

## Data Model

### Chart of Accounts (`finance-accounts`)

Preset double-entry account tree — users can add sub-accounts under any node:

```
1000 Assets
  1100 Bank Accounts
  1200 Cash / Alipay / WeChat Pay
2000 Liabilities
  2100 Credit Cards
3000 Equity
4000 Income
  4100 Salary / 4200 Side Income / 4300 Investment Returns
5000 Expenses
  5100 Dining / 5200 Transportation / 5300 Shopping ...
```

### Journal Entries

Every confirmed entry satisfies: `SUM(DEBIT lines) == SUM(CREDIT lines)` — enforced server-side by `ConfirmLambda`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Infrastructure | AWS CDK (TypeScript) |
| Backend | AWS Lambda (Python), API Gateway |
| Storage | DynamoDB, S3 |
| AI | Claude API (Anthropic) |
| Frontend | Vanilla JS / PWA, served via CloudFront |
| Scheduling | EventBridge |
| Notifications | SNS |

---

## Academic Poster

The showcase poster (`docs/poster.html`) was presented at the Northeastern University CS6620 Cloud Computing end-of-semester showcase.

---

## Authors

Jiaxin Jia · Advised by Professor Zhuoqun Cheng  
Khoury College of Computer Sciences, Northeastern University
