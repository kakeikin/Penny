# Personal Finance App — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working single-user personal finance web app with double-entry bookkeeping, PDF/receipt upload, Claude AI parsing, manual entry, and financial reports.

**Architecture:** Standalone AWS CDK TypeScript project. Python 3.12 Lambdas handle all backend logic. S3 event triggers ParseLambda on file upload. All other operations go through API Gateway REST. Frontend is vanilla JS + Tailwind CSS served via CloudFront + S3.

**Tech Stack:** AWS CDK v2 (TypeScript), Python 3.12 Lambdas, DynamoDB, S3, API Gateway, CloudFront, Secrets Manager, Anthropic Claude API (`claude-sonnet-4-6`), Chart.js, Tailwind CSS (CDN).

---

## File Structure

```
PersonalFinanceApp/
├── bin/app.ts                          # CDK app entry point
├── lib/finance-stack.ts                # All AWS resources in one stack
├── lambda/
│   ├── parse/
│   │   ├── index.py                    # ParseLambda: S3 trigger → Claude → DynamoDB
│   │   └── requirements.txt
│   ├── confirm/
│   │   ├── index.py                    # ConfirmLambda: validate balance + CONFIRMED
│   │   └── requirements.txt
│   ├── manual-entry/
│   │   ├── index.py                    # ManualEntryLambda: CRUD for entries + accounts
│   │   └── requirements.txt
│   ├── query/
│   │   ├── index.py                    # QueryLambda: list, summary, reports
│   │   └── requirements.txt
│   └── export/
│       ├── index.py                    # ExportLambda: CSV download
│       └── requirements.txt
├── scripts/seed-accounts.ts            # Seed preset chart of accounts
├── frontend/
│   ├── index.html                      # SPA shell + router
│   ├── config.js                       # API_BASE_URL (injected at deploy)
│   ├── api.js                          # fetch wrapper for all API calls
│   └── pages/
│       ├── dashboard.js
│       ├── upload.js
│       ├── transactions.js
│       ├── manual-entry.js
│       ├── reports.js
│       └── settings.js
├── test/
│   ├── finance-stack.test.ts
│   └── lambda/
│       ├── test_parse.py
│       ├── test_confirm.py
│       ├── test_manual_entry.py
│       └── test_query.py
├── package.json
├── tsconfig.json
├── cdk.json
└── jest.config.js
```

---

## Task 1: Project Initialization

**Files:**
- Create: `package.json`
- Create: `tsconfig.json`
- Create: `cdk.json`
- Create: `jest.config.js`
- Create: `bin/app.ts`

- [ ] **Step 1: Initialize project and install dependencies**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp
npm init -y
npm install aws-cdk-lib constructs
npm install -D aws-cdk typescript ts-node ts-jest jest @types/jest @types/node aws-sdk-client-mock
mkdir -p bin lib lambda/parse lambda/confirm lambda/manual-entry lambda/query lambda/export frontend/pages scripts test/lambda
```

- [ ] **Step 2: Create `package.json`**

```json
{
  "name": "personal-finance-app",
  "version": "0.1.0",
  "scripts": {
    "build": "tsc",
    "test": "jest",
    "cdk": "cdk"
  },
  "dependencies": {
    "aws-cdk-lib": "^2.242.0",
    "constructs": "^10.5.0"
  },
  "devDependencies": {
    "@types/jest": "^30",
    "@types/node": "^24.0.0",
    "aws-cdk": "^2.242.0",
    "aws-sdk-client-mock": "^4.1.0",
    "jest": "^30",
    "ts-jest": "^29",
    "ts-node": "^10.9.2",
    "typescript": "~5.9.3"
  }
}
```

- [ ] **Step 3: Create `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "lib": ["es2022"],
    "declaration": true,
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "noImplicitReturns": true,
    "inlineSourceMap": true,
    "inlineSources": true,
    "experimentalDecorators": true,
    "strictPropertyInitialization": false,
    "skipLibCheck": true
  },
  "exclude": ["node_modules", "cdk.out"]
}
```

- [ ] **Step 4: Create `cdk.json`**

```json
{
  "app": "npx ts-node --prefer-ts-exts bin/app.ts",
  "context": {
    "@aws-cdk/aws-apigateway:disableCloudWatchRole": true,
    "@aws-cdk/aws-s3:publicAccessBlockedByDefault": true
  }
}
```

- [ ] **Step 5: Create `jest.config.js`**

```js
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: { '^.+\\.tsx?$': 'ts-jest' },
};
```

- [ ] **Step 6: Create `bin/app.ts`**

```typescript
import * as cdk from 'aws-cdk-lib';
import { FinanceStack } from '../lib/finance-stack';

const app = new cdk.App();
new FinanceStack(app, 'FinanceStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
  },
});
```

- [ ] **Step 7: Commit**

```bash
git add .
git commit -m "chore: initialize CDK TypeScript project"
```

---

## Task 2: CDK Stack — Infrastructure

**Files:**
- Create: `lib/finance-stack.ts`
- Create: `test/finance-stack.test.ts`

- [ ] **Step 1: Write the failing CDK test**

```typescript
// test/finance-stack.test.ts
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { FinanceStack } from '../lib/finance-stack';

let template: Template;
beforeAll(() => {
  const app = new cdk.App();
  const stack = new FinanceStack(app, 'TestStack');
  template = Template.fromStack(stack);
});

test('creates three DynamoDB tables', () => {
  template.resourceCountIs('AWS::DynamoDB::Table', 3);
});

test('creates S3 bucket', () => {
  template.resourceCountIs('AWS::S3::Bucket', 2); // app bucket + access logs
});

test('creates API Gateway', () => {
  template.resourceCountIs('AWS::ApiGateway::RestApi', 1);
});

test('creates Secrets Manager secret for Claude API key', () => {
  template.hasResourceProperties('AWS::SecretsManager::Secret', {
    Name: 'finance/claude-api-key',
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx jest test/finance-stack.test.ts
```
Expected: FAIL — `Cannot find module '../lib/finance-stack'`

- [ ] **Step 3: Create `lib/finance-stack.ts`**

```typescript
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as path from 'path';

export class FinanceStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ── Secrets ──────────────────────────────────────────────
    const claudeSecret = new secretsmanager.Secret(this, 'ClaudeApiKey', {
      secretName: 'finance/claude-api-key',
      description: 'Anthropic Claude API key for finance app',
    });

    // ── DynamoDB Tables ───────────────────────────────────────
    const accountsTable = new dynamodb.Table(this, 'AccountsTable', {
      tableName: 'finance-accounts',
      partitionKey: { name: 'accountId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const entriesTable = new dynamodb.Table(this, 'EntriesTable', {
      tableName: 'finance-journal-entries',
      partitionKey: { name: 'entryId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    entriesTable.addGlobalSecondaryIndex({
      indexName: 'date-index',
      partitionKey: { name: 'yearMonth', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'date', type: dynamodb.AttributeType.STRING },
    });

    const linesTable = new dynamodb.Table(this, 'LinesTable', {
      tableName: 'finance-journal-lines',
      partitionKey: { name: 'entryId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'lineId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ── S3 Bucket ─────────────────────────────────────────────
    const appBucket = new s3.Bucket(this, 'AppBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      cors: [{
        allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.GET],
        allowedOrigins: ['*'],
        allowedHeaders: ['*'],
      }],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ── Lambda environment shared vars ────────────────────────
    const lambdaEnv = {
      ACCOUNTS_TABLE: accountsTable.tableName,
      ENTRIES_TABLE: entriesTable.tableName,
      LINES_TABLE: linesTable.tableName,
      APP_BUCKET: appBucket.bucketName,
      CLAUDE_SECRET_ARN: claudeSecret.secretArn,
    };

    // ── Lambda Layer: Python dependencies ─────────────────────
    const pyLayer = new lambda.LayerVersion(this, 'PyDepsLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/layer')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'anthropic + boto3',
    });

    // ── Lambda Functions ──────────────────────────────────────
    const parseFn = new lambda.Function(this, 'ParseLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/parse')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
    });

    const confirmFn = new lambda.Function(this, 'ConfirmLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/confirm')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(10),
    });

    const manualEntryFn = new lambda.Function(this, 'ManualEntryLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/manual-entry')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(10),
    });

    const queryFn = new lambda.Function(this, 'QueryLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/query')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(30),
    });

    const exportFn = new lambda.Function(this, 'ExportLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/export')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(30),
    });

    // ── Permissions ───────────────────────────────────────────
    [parseFn, confirmFn, manualEntryFn, queryFn, exportFn].forEach(fn => {
      accountsTable.grantReadWriteData(fn);
      entriesTable.grantReadWriteData(fn);
      linesTable.grantReadWriteData(fn);
      appBucket.grantReadWrite(fn);
      claudeSecret.grantRead(fn);
    });

    // S3 triggers ParseLambda on uploads/ prefix
    appBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(parseFn),
      { prefix: 'uploads/' },
    );

    // ── API Gateway ───────────────────────────────────────────
    const api = new apigw.RestApi(this, 'FinanceApi', {
      restApiName: 'finance-api',
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
    });

    const apiRoot = api.root.addResource('api');

    // POST /api/upload  → parseFn (returns presigned URL)
    const uploadRes = apiRoot.addResource('upload');
    uploadRes.addMethod('POST', new apigw.LambdaIntegration(parseFn));

    // /api/entries
    const entriesRes = apiRoot.addResource('entries');
    entriesRes.addMethod('GET', new apigw.LambdaIntegration(queryFn));
    entriesRes.addMethod('POST', new apigw.LambdaIntegration(manualEntryFn));

    const entryRes = entriesRes.addResource('{id}');
    entryRes.addMethod('PUT', new apigw.LambdaIntegration(manualEntryFn));
    entryRes.addMethod('DELETE', new apigw.LambdaIntegration(manualEntryFn));

    const confirmRes = entryRes.addResource('confirm');
    confirmRes.addMethod('PUT', new apigw.LambdaIntegration(confirmFn));

    // /api/accounts
    const accountsRes = apiRoot.addResource('accounts');
    accountsRes.addMethod('GET', new apigw.LambdaIntegration(queryFn));
    accountsRes.addMethod('POST', new apigw.LambdaIntegration(manualEntryFn));

    // /api/summary, /api/reports/*, /api/export/csv
    apiRoot.addResource('summary').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    const reportsRes = apiRoot.addResource('reports');
    reportsRes.addResource('income-statement').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    reportsRes.addResource('balance-sheet').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    reportsRes.addResource('net-worth').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    apiRoot.addResource('export').addResource('csv').addMethod('GET', new apigw.LambdaIntegration(exportFn));

    // ── CloudFront ────────────────────────────────────────────
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(appBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(api),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
      defaultRootObject: 'index.html',
      errorResponses: [{ httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' }],
    });

    // ── Outputs ───────────────────────────────────────────────
    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${distribution.distributionDomainName}` });
    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
    new cdk.CfnOutput(this, 'BucketName', { value: appBucket.bucketName });
    new cdk.CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
  }
}
```

- [ ] **Step 4: Run CDK test**

```bash
npx jest test/finance-stack.test.ts
```
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add lib/finance-stack.ts bin/app.ts test/finance-stack.test.ts
git commit -m "feat: add FinanceStack CDK infrastructure"
```

---

## Task 3: Lambda Layer (Python dependencies)

**Files:**
- Create: `lambda/layer/python/` (directory, not committed — built locally)
- Create: `scripts/build-layer.sh`

- [ ] **Step 1: Create build script**

```bash
# scripts/build-layer.sh
#!/bin/bash
set -e
echo "Building Python Lambda layer..."
rm -rf lambda/layer
mkdir -p lambda/layer/python
pip install anthropic boto3 -t lambda/layer/python --quiet
echo "Layer built at lambda/layer/python/"
```

- [ ] **Step 2: Build the layer**

```bash
chmod +x scripts/build-layer.sh
./scripts/build-layer.sh
```
Expected: `lambda/layer/python/` contains `anthropic/`, `boto3/`, etc.

- [ ] **Step 3: Add layer to .gitignore**

```bash
echo "lambda/layer/" >> .gitignore
```

- [ ] **Step 4: Commit**

```bash
git add scripts/build-layer.sh .gitignore
git commit -m "chore: add Lambda layer build script for Python deps"
```

---

## Task 4: Seed Script — Chart of Accounts

**Files:**
- Create: `scripts/seed-accounts.ts`

- [ ] **Step 1: Create seed script**

```typescript
// scripts/seed-accounts.ts
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, PutCommand } from '@aws-sdk/lib-dynamodb';

const client = DynamoDBDocumentClient.from(new DynamoDBClient({ region: 'us-east-1' }));
const TABLE = 'finance-accounts';

const accounts = [
  { accountId: '1000', name: 'Assets', type: 'ASSET', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '1100', name: 'Bank Accounts', type: 'ASSET', parentId: '1000', isSystem: true, currency: 'CNY' },
  { accountId: '1200', name: 'Cash / Alipay / WeChat Pay', type: 'ASSET', parentId: '1000', isSystem: true, currency: 'CNY' },
  { accountId: '1300', name: 'Investments', type: 'ASSET', parentId: '1000', isSystem: true, currency: 'CNY' },
  { accountId: '2000', name: 'Liabilities', type: 'LIABILITY', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '2100', name: 'Credit Cards', type: 'LIABILITY', parentId: '2000', isSystem: true, currency: 'CNY' },
  { accountId: '2200', name: 'Loans', type: 'LIABILITY', parentId: '2000', isSystem: true, currency: 'CNY' },
  { accountId: '3000', name: 'Equity', type: 'EQUITY', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '3100', name: 'Opening Equity', type: 'EQUITY', parentId: '3000', isSystem: true, currency: 'CNY' },
  { accountId: '4000', name: 'Income', type: 'INCOME', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '4100', name: 'Salary', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '4200', name: 'Side Income', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '4300', name: 'Investment Returns', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '4400', name: 'Other Income', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '5000', name: 'Expenses', type: 'EXPENSE', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '5100', name: 'Dining', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5200', name: 'Transportation', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5300', name: 'Shopping', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5400', name: 'Housing', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5500', name: 'Healthcare', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5600', name: 'Entertainment', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5700', name: 'Education', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5800', name: 'Other Expenses', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
];

async function seed() {
  for (const account of accounts) {
    await client.send(new PutCommand({ TableName: TABLE, Item: account }));
    console.log(`Seeded: ${account.accountId} ${account.name}`);
  }
  console.log('Done.');
}

seed().catch(console.error);
```

- [ ] **Step 2: Add run script to package.json**

Add to `package.json` scripts:
```json
"seed": "ts-node --prefer-ts-exts scripts/seed-accounts.ts"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/seed-accounts.ts package.json
git commit -m "feat: add chart of accounts seed script"
```

---

## Task 5: ParseLambda

**Files:**
- Create: `lambda/parse/index.py`
- Create: `lambda/parse/requirements.txt`
- Create: `test/lambda/test_parse.py`

- [ ] **Step 1: Create `lambda/parse/requirements.txt`**

```
anthropic
boto3
```

- [ ] **Step 2: Write failing tests**

```python
# test/lambda/test_parse.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/parse'))

import pytest
from unittest.mock import patch, MagicMock

# ── helpers ──────────────────────────────────────────────────

def make_s3_event(key='uploads/test.pdf'):
    return {'Records': [{'s3': {'bucket': {'name': 'test-bucket'}, 'object': {'key': key}}}]}

# ── tests ─────────────────────────────────────────────────────

def test_compute_md5():
    from index import compute_md5
    data = b'hello world'
    assert compute_md5(data) == '5eb63bbbe01eeed093cb22bb8f5acdc3'

def test_validate_balance_passes():
    from index import validate_balance
    lines = [
        {'direction': 'DEBIT', 'amount': 50.0},
        {'direction': 'CREDIT', 'amount': 50.0},
    ]
    assert validate_balance(lines) is True

def test_validate_balance_fails():
    from index import validate_balance
    lines = [
        {'direction': 'DEBIT', 'amount': 100.0},
        {'direction': 'CREDIT', 'amount': 50.0},
    ]
    assert validate_balance(lines) is False
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp
python -m pytest test/lambda/test_parse.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'index'`

- [ ] **Step 4: Create `lambda/parse/index.py`**

```python
import boto3
import json
import os
import hashlib
import uuid
from datetime import datetime, timezone

# Add layer to path when running in Lambda
import sys
sys.path.insert(0, '/opt/python')

import anthropic

dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
secrets_client = boto3.client('secretsmanager')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')
APP_BUCKET     = os.environ.get('APP_BUCKET', '')
CLAUDE_SECRET_ARN = os.environ.get('CLAUDE_SECRET_ARN', '')


def compute_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def validate_balance(lines: list) -> bool:
    debit  = sum(l['amount'] for l in lines if l['direction'] == 'DEBIT')
    credit = sum(l['amount'] for l in lines if l['direction'] == 'CREDIT')
    return abs(debit - credit) < 0.01


def get_claude_client() -> anthropic.Anthropic:
    secret = secrets_client.get_secret_value(SecretId=CLAUDE_SECRET_ARN)
    api_key = json.loads(secret['SecretString'])['CLAUDE_API_KEY']
    return anthropic.Anthropic(api_key=api_key)


def get_accounts() -> list:
    table = dynamodb.Table(ACCOUNTS_TABLE)
    result = table.scan()
    return result.get('Items', [])


def parse_with_claude(file_data: bytes, media_type: str, accounts: list) -> list:
    """Send file to Claude, return list of journal entry dicts."""
    client = get_claude_client()
    account_list = [{'accountId': a['accountId'], 'name': a['name'], 'type': a['type']} for a in accounts]

    import base64
    encoded = base64.standard_b64encode(file_data).decode('utf-8')

    prompt = f"""You are a professional accountant. Analyze this bank statement or receipt and output double-entry bookkeeping journal entries in JSON format.

Accounts must be selected from this list only:
{json.dumps(account_list, ensure_ascii=False)}

For each transaction, produce one journal entry with balanced debit and credit lines (total debits must equal total credits).
If classification is uncertain, add a note explaining why.

Return ONLY valid JSON with this exact structure:
{{
  "entries": [
    {{
      "date": "YYYY-MM-DD",
      "description": "merchant or transaction description",
      "lines": [
        {{"accountId": "...", "direction": "DEBIT", "amount": 0.00, "note": ""}},
        {{"accountId": "...", "direction": "CREDIT", "amount": 0.00, "note": ""}}
      ]
    }}
  ]
}}"""

    message = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4096,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'document' if media_type == 'application/pdf' else 'image',
                    'source': {'type': 'base64', 'media_type': media_type, 'data': encoded},
                },
                {'type': 'text', 'text': prompt},
            ],
        }],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code blocks if present
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(raw)['entries']


def save_pending_entries(entries: list, file_key: str, file_hash: str, source: str):
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    for entry in entries:
        if not validate_balance(entry['lines']):
            continue  # skip unbalanced entries from Claude

        entry_id  = str(uuid.uuid4())
        date      = entry['date']
        year_month = date[:7]  # 'YYYY-MM'

        entries_table.put_item(Item={
            'entryId':     entry_id,
            'date':        date,
            'yearMonth':   year_month,
            'description': entry.get('description', ''),
            'source':      source,
            'status':      'PENDING',
            'fileKey':     file_key,
            'fileHash':    file_hash,
            'createdAt':   datetime.now(timezone.utc).isoformat(),
        })

        for i, line in enumerate(entry['lines']):
            lines_table.put_item(Item={
                'entryId':   entry_id,
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),  # DynamoDB decimal-safe
                'note':      line.get('note', ''),
            })


def is_duplicate(file_hash: str) -> bool:
    table = dynamodb.Table(ENTRIES_TABLE)
    result = table.scan(
        FilterExpression='fileHash = :h',
        ExpressionAttributeValues={':h': file_hash},
        Limit=1,
    )
    return len(result.get('Items', [])) > 0


def handler(event, context):
    # ── Presigned URL request (POST /api/upload) ──────────────
    if event.get('httpMethod') == 'POST' and '/upload' in event.get('path', ''):
        body = json.loads(event.get('body', '{}'))
        filename = body.get('filename', 'upload')
        content_type = body.get('contentType', 'application/pdf')
        key = f'uploads/{uuid.uuid4()}-{filename}'

        url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': APP_BUCKET, 'Key': key, 'ContentType': content_type},
            ExpiresIn=300,
        )
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'},
            'body': json.dumps({'uploadUrl': url, 'key': key}),
        }

    # ── S3 event trigger ──────────────────────────────────────
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key    = record['s3']['object']['key']

        file_obj = s3_client.get_object(Bucket=bucket, Key=key)
        file_data = file_obj['Body'].read()
        file_hash = compute_md5(file_data)

        if is_duplicate(file_hash):
            print(f'Duplicate file skipped: {key}')
            continue

        ext = key.rsplit('.', 1)[-1].lower()
        if ext == 'pdf':
            media_type = 'application/pdf'
            source = 'PDF'
        elif ext in ('jpg', 'jpeg'):
            media_type = 'image/jpeg'
            source = 'RECEIPT'
        else:
            media_type = 'image/png'
            source = 'RECEIPT'

        accounts = get_accounts()
        entries  = parse_with_claude(file_data, media_type, accounts)
        save_pending_entries(entries, key, file_hash, source)
        print(f'Parsed {len(entries)} entries from {key}')
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest test/lambda/test_parse.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add lambda/parse/ test/lambda/test_parse.py
git commit -m "feat: add ParseLambda with duplicate detection and Claude integration"
```

---

## Task 6: ConfirmLambda

**Files:**
- Create: `lambda/confirm/index.py`
- Create: `lambda/confirm/requirements.txt`
- Create: `test/lambda/test_confirm.py`

- [ ] **Step 1: Create `lambda/confirm/requirements.txt`**

```
boto3
```

- [ ] **Step 2: Write failing tests**

```python
# test/lambda/test_confirm.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/confirm'))

from unittest.mock import patch, MagicMock

def test_lines_balance():
    from index import lines_balance
    assert lines_balance([
        {'direction': 'DEBIT', 'amount': '100.00'},
        {'direction': 'CREDIT', 'amount': '100.00'},
    ]) is True

def test_lines_unbalanced():
    from index import lines_balance
    assert lines_balance([
        {'direction': 'DEBIT', 'amount': '100.00'},
        {'direction': 'CREDIT', 'amount': '50.00'},
    ]) is False

def test_handler_returns_400_if_unbalanced():
    from index import handler
    mock_lines_table = MagicMock()
    mock_lines_table.query.return_value = {'Items': [
        {'lineId': '000', 'direction': 'DEBIT', 'amount': '100.00'},
        {'lineId': '001', 'direction': 'CREDIT', 'amount': '50.00'},
    ]}
    mock_entries_table = MagicMock()
    mock_entries_table.get_item.return_value = {'Item': {'entryId': 'e1', 'status': 'PENDING'}}

    with patch('index.dynamodb') as mock_db:
        mock_db.Table.side_effect = lambda name: {
            'finance-journal-entries': mock_entries_table,
            'finance-journal-lines': mock_lines_table,
        }[name]
        event = {'pathParameters': {'id': 'e1'}, 'httpMethod': 'PUT'}
        resp = handler(event, {})
    assert resp['statusCode'] == 400
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest test/lambda/test_confirm.py -v
```
Expected: FAIL

- [ ] **Step 4: Create `lambda/confirm/index.py`**

```python
import boto3
import json
import os

dynamodb = boto3.resource('dynamodb')

ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE   = os.environ.get('LINES_TABLE', 'finance-journal-lines')


def lines_balance(lines: list) -> bool:
    debit  = sum(float(l['amount']) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(float(l['amount']) for l in lines if l['direction'] == 'CREDIT')
    return abs(debit - credit) < 0.01


def handler(event, context):
    entry_id = event['pathParameters']['id']
    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)

    entry_resp = entries_table.get_item(Key={'entryId': entry_id})
    entry = entry_resp.get('Item')
    if not entry:
        return {'statusCode': 404, 'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Entry not found'})}

    if entry.get('status') == 'CONFIRMED':
        return {'statusCode': 200, 'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'message': 'Already confirmed'})}

    lines_resp = lines_table.query(
        KeyConditionExpression='entryId = :e',
        ExpressionAttributeValues={':e': entry_id},
    )
    lines = lines_resp.get('Items', [])

    if not lines_balance(lines):
        return {'statusCode': 400, 'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Debit/credit lines do not balance'})}

    # Apply any edits from request body
    body = json.loads(event.get('body') or '{}')
    if 'lines' in body:
        # Delete existing lines and write updated ones
        for line in lines:
            lines_table.delete_item(Key={'entryId': entry_id, 'lineId': line['lineId']})
        for i, line in enumerate(body['lines']):
            lines_table.put_item(Item={
                'entryId':   entry_id,
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),
                'note':      line.get('note', ''),
            })

    if 'description' in body:
        entries_table.update_item(
            Key={'entryId': entry_id},
            UpdateExpression='SET description = :d',
            ExpressionAttributeValues={':d': body['description']},
        )

    entries_table.update_item(
        Key={'entryId': entry_id},
        UpdateExpression='SET #s = :s',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'CONFIRMED'},
    )

    return {
        'statusCode': 200,
        'headers': {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'},
        'body': json.dumps({'entryId': entry_id, 'status': 'CONFIRMED'}),
    }
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest test/lambda/test_confirm.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add lambda/confirm/ test/lambda/test_confirm.py
git commit -m "feat: add ConfirmLambda with balance validation"
```

---

## Task 7: ManualEntryLambda

**Files:**
- Create: `lambda/manual-entry/index.py`
- Create: `lambda/manual-entry/requirements.txt`
- Create: `test/lambda/test_manual_entry.py`

- [ ] **Step 1: Create `lambda/manual-entry/requirements.txt`**

```
boto3
```

- [ ] **Step 2: Write failing tests**

```python
# test/lambda/test_manual_entry.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/manual-entry'))

from unittest.mock import patch, MagicMock

def _event(method, path, body=None, path_params=None):
    return {
        'httpMethod': method,
        'path': path,
        'pathParameters': path_params or {},
        'body': json.dumps(body) if body else None,
    }

def test_post_entry_returns_201():
    from index import handler
    body = {
        'date': '2026-04-01',
        'description': 'Coffee',
        'lines': [
            {'accountId': '5100', 'direction': 'DEBIT', 'amount': 38.00, 'note': ''},
            {'accountId': '1100', 'direction': 'CREDIT', 'amount': 38.00, 'note': ''},
        ],
    }
    with patch('index.dynamodb') as mock_db:
        mock_db.Table.return_value.put_item.return_value = {}
        resp = handler(_event('POST', '/api/entries', body), {})
    assert resp['statusCode'] == 201
    data = json.loads(resp['body'])
    assert 'entryId' in data

def test_post_entry_rejects_unbalanced():
    from index import handler
    body = {
        'date': '2026-04-01',
        'description': 'Bad entry',
        'lines': [
            {'accountId': '5100', 'direction': 'DEBIT', 'amount': 100.00, 'note': ''},
        ],
    }
    with patch('index.dynamodb'):
        resp = handler(_event('POST', '/api/entries', body), {})
    assert resp['statusCode'] == 400
```

- [ ] **Step 3: Run to verify failure**

```bash
python -m pytest test/lambda/test_manual_entry.py -v
```

- [ ] **Step 4: Create `lambda/manual-entry/index.py`**

```python
import boto3
import json
import os
import uuid
from datetime import datetime, timezone

dynamodb = boto3.resource('dynamodb')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}


def lines_balance(lines):
    debit  = sum(float(l['amount']) for l in lines if l['direction'] == 'DEBIT')
    credit = sum(float(l['amount']) for l in lines if l['direction'] == 'CREDIT')
    return abs(debit - credit) < 0.01


def handler(event, context):
    method = event.get('httpMethod', '')
    path   = event.get('path', '')
    params = event.get('pathParameters') or {}
    body   = json.loads(event.get('body') or '{}')

    entries_table = dynamodb.Table(ENTRIES_TABLE)
    lines_table   = dynamodb.Table(LINES_TABLE)
    accounts_table = dynamodb.Table(ACCOUNTS_TABLE)

    # ── POST /api/entries (manual entry) ─────────────────────
    if method == 'POST' and path.endswith('/entries'):
        lines = body.get('lines', [])
        if not lines_balance(lines):
            return {'statusCode': 400, 'headers': CORS,
                    'body': json.dumps({'error': 'Debit and credit lines must balance'})}

        entry_id   = str(uuid.uuid4())
        date       = body['date']
        year_month = date[:7]

        entries_table.put_item(Item={
            'entryId':     entry_id,
            'date':        date,
            'yearMonth':   year_month,
            'description': body.get('description', ''),
            'source':      'MANUAL',
            'status':      'CONFIRMED',
            'fileKey':     None,
            'fileHash':    None,
            'createdAt':   datetime.now(timezone.utc).isoformat(),
        })

        for i, line in enumerate(lines):
            lines_table.put_item(Item={
                'entryId':   entry_id,
                'lineId':    f'{i:03d}',
                'accountId': line['accountId'],
                'direction': line['direction'],
                'amount':    str(line['amount']),
                'note':      line.get('note', ''),
            })

        return {'statusCode': 201, 'headers': CORS, 'body': json.dumps({'entryId': entry_id})}

    # ── PUT /api/entries/{id} ─────────────────────────────────
    if method == 'PUT' and 'id' in params and 'confirm' not in path:
        entry_id = params['id']
        update_expr = []
        expr_vals   = {}

        if 'description' in body:
            update_expr.append('description = :d')
            expr_vals[':d'] = body['description']
        if 'date' in body:
            update_expr.append('#dt = :dt, yearMonth = :ym')
            expr_vals[':dt'] = body['date']
            expr_vals[':ym'] = body['date'][:7]

        if update_expr:
            entries_table.update_item(
                Key={'entryId': entry_id},
                UpdateExpression='SET ' + ', '.join(update_expr),
                ExpressionAttributeNames={'#dt': 'date'} if 'date' in body else {},
                ExpressionAttributeValues=expr_vals,
            )

        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'updated': True})}

    # ── DELETE /api/entries/{id} ──────────────────────────────
    if method == 'DELETE' and 'id' in params:
        entry_id = params['id']
        lines_resp = lines_table.query(
            KeyConditionExpression='entryId = :e',
            ExpressionAttributeValues={':e': entry_id},
        )
        for line in lines_resp.get('Items', []):
            lines_table.delete_item(Key={'entryId': entry_id, 'lineId': line['lineId']})
        entries_table.delete_item(Key={'entryId': entry_id})
        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'deleted': True})}

    # ── POST /api/accounts (create custom sub-account) ───────
    if method == 'POST' and path.endswith('/accounts'):
        account_id = body.get('accountId', str(uuid.uuid4())[:8])
        accounts_table.put_item(Item={
            'accountId': account_id,
            'name':      body['name'],
            'type':      body['type'],
            'parentId':  body.get('parentId'),
            'isSystem':  False,
            'currency':  body.get('currency', 'CNY'),
        })
        return {'statusCode': 201, 'headers': CORS, 'body': json.dumps({'accountId': account_id})}

    return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'Not found'})}
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest test/lambda/test_manual_entry.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lambda/manual-entry/ test/lambda/test_manual_entry.py
git commit -m "feat: add ManualEntryLambda for CRUD and custom accounts"
```

---

## Task 8: QueryLambda

**Files:**
- Create: `lambda/query/index.py`
- Create: `lambda/query/requirements.txt`
- Create: `test/lambda/test_query.py`

- [ ] **Step 1: Create `lambda/query/requirements.txt`**

```
boto3
```

- [ ] **Step 2: Write failing tests**

```python
# test/lambda/test_query.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/query'))

from unittest.mock import patch, MagicMock

def test_build_account_tree():
    from index import build_account_tree
    accounts = [
        {'accountId': '1000', 'name': 'Assets', 'type': 'ASSET', 'parentId': None},
        {'accountId': '1100', 'name': 'Bank', 'type': 'ASSET', 'parentId': '1000'},
    ]
    tree = build_account_tree(accounts)
    assert len(tree) == 1
    assert tree[0]['accountId'] == '1000'
    assert len(tree[0]['children']) == 1
    assert tree[0]['children'][0]['accountId'] == '1100'

def test_income_statement_sums():
    from index import compute_income_statement
    entries = [
        {'entryId': 'e1', 'date': '2026-04-01', 'status': 'CONFIRMED'},
    ]
    lines_by_entry = {
        'e1': [
            {'accountId': '4100', 'direction': 'CREDIT', 'amount': '5000.00'},
            {'accountId': '1100', 'direction': 'DEBIT',  'amount': '5000.00'},
        ]
    }
    accounts = {
        '4100': {'accountId': '4100', 'name': 'Salary', 'type': 'INCOME'},
        '1100': {'accountId': '1100', 'name': 'Bank', 'type': 'ASSET'},
    }
    result = compute_income_statement(entries, lines_by_entry, accounts)
    assert result['totalIncome'] == 5000.0
    assert result['totalExpenses'] == 0.0
    assert result['netIncome'] == 5000.0
```

- [ ] **Step 3: Run to verify failure**

```bash
python -m pytest test/lambda/test_query.py -v
```

- [ ] **Step 4: Create `lambda/query/index.py`**

```python
import boto3
import json
import os
from collections import defaultdict
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'}


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def build_account_tree(accounts: list) -> list:
    by_id = {a['accountId']: dict(a, children=[]) for a in accounts}
    roots = []
    for a in by_id.values():
        parent_id = a.get('parentId')
        if parent_id and parent_id in by_id:
            by_id[parent_id]['children'].append(a)
        else:
            roots.append(a)
    return roots


def get_all_accounts() -> dict:
    table = dynamodb.Table(ACCOUNTS_TABLE)
    items = table.scan()['Items']
    return {a['accountId']: a for a in items}


def get_confirmed_entries(start_date=None, end_date=None) -> list:
    table = dynamodb.Table(ENTRIES_TABLE)
    fe = '#s = :confirmed'
    ea_names = {'#s': 'status'}
    ea_vals  = {':confirmed': 'CONFIRMED'}
    if start_date:
        fe += ' AND #d >= :start'
        ea_names['#d'] = 'date'
        ea_vals[':start'] = start_date
    if end_date:
        fe += ' AND #d <= :end'
        ea_names['#d'] = 'date'
        ea_vals[':end'] = end_date
    return table.scan(
        FilterExpression=fe,
        ExpressionAttributeNames=ea_names,
        ExpressionAttributeValues=ea_vals,
    )['Items']


def get_lines_for_entries(entry_ids: list) -> dict:
    lines_table = dynamodb.Table(LINES_TABLE)
    result = defaultdict(list)
    for eid in entry_ids:
        resp = lines_table.query(
            KeyConditionExpression='entryId = :e',
            ExpressionAttributeValues={':e': eid},
        )
        result[eid] = resp['Items']
    return result


def compute_income_statement(entries, lines_by_entry, accounts):
    income_by_account   = defaultdict(float)
    expenses_by_account = defaultdict(float)

    for entry in entries:
        for line in lines_by_entry.get(entry['entryId'], []):
            acct = accounts.get(line['accountId'], {})
            amt  = float(line['amount'])
            if acct.get('type') == 'INCOME' and line['direction'] == 'CREDIT':
                income_by_account[line['accountId']] += amt
            elif acct.get('type') == 'EXPENSE' and line['direction'] == 'DEBIT':
                expenses_by_account[line['accountId']] += amt

    total_income   = sum(income_by_account.values())
    total_expenses = sum(expenses_by_account.values())

    def enrich(d):
        return [{'accountId': k, 'name': accounts.get(k, {}).get('name', k), 'amount': v}
                for k, v in sorted(d.items())]

    return {
        'income':        enrich(income_by_account),
        'expenses':      enrich(expenses_by_account),
        'totalIncome':   total_income,
        'totalExpenses': total_expenses,
        'netIncome':     total_income - total_expenses,
    }


def compute_balance_sheet(accounts_dict, lines_by_entry, all_entries):
    balances = defaultdict(float)
    for entry in all_entries:
        for line in lines_by_entry.get(entry['entryId'], []):
            acct = accounts_dict.get(line['accountId'], {})
            amt  = float(line['amount'])
            t    = acct.get('type')
            if t == 'ASSET':
                balances[line['accountId']] += amt if line['direction'] == 'DEBIT' else -amt
            elif t in ('LIABILITY', 'EQUITY'):
                balances[line['accountId']] += amt if line['direction'] == 'CREDIT' else -amt

    assets = {k: v for k, v in balances.items()
              if accounts_dict.get(k, {}).get('type') == 'ASSET'}
    liabilities = {k: v for k, v in balances.items()
                   if accounts_dict.get(k, {}).get('type') == 'LIABILITY'}
    equity = {k: v for k, v in balances.items()
              if accounts_dict.get(k, {}).get('type') == 'EQUITY'}

    def enrich(d):
        return [{'accountId': k, 'name': accounts_dict.get(k, {}).get('name', k), 'balance': v}
                for k, v in d.items()]

    return {
        'assets':         enrich(assets),
        'liabilities':    enrich(liabilities),
        'equity':         enrich(equity),
        'totalAssets':    sum(assets.values()),
        'totalLiabilities': sum(liabilities.values()),
        'totalEquity':    sum(equity.values()),
    }


def handler(event, context):
    path   = event.get('path', '')
    params = event.get('queryStringParameters') or {}

    accounts_dict = get_all_accounts()

    # ── GET /api/accounts ─────────────────────────────────────
    if path.endswith('/accounts'):
        tree = build_account_tree(list(accounts_dict.values()))
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(tree, cls=DecimalEncoder)}

    # ── GET /api/entries ──────────────────────────────────────
    if path.endswith('/entries'):
        entries = get_confirmed_entries(
            start_date=params.get('startDate'),
            end_date=params.get('endDate'),
        )
        if params.get('accountId'):
            lines_table = dynamodb.Table(LINES_TABLE)
            matching_ids = set()
            for entry in entries:
                resp = lines_table.query(
                    KeyConditionExpression='entryId = :e',
                    ExpressionAttributeValues={':e': entry['entryId']},
                )
                for line in resp['Items']:
                    if line['accountId'] == params['accountId']:
                        matching_ids.add(entry['entryId'])
            entries = [e for e in entries if e['entryId'] in matching_ids]

        # Attach lines
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        for entry in entries:
            entry['lines'] = lines_by_entry.get(entry['entryId'], [])

        entries.sort(key=lambda e: e['date'], reverse=True)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(entries, cls=DecimalEncoder)}

    # ── GET /api/summary ──────────────────────────────────────
    if path.endswith('/summary'):
        from datetime import datetime
        now = datetime.utcnow()
        month_start = f"{now.year}-{now.month:02d}-01"
        entries = get_confirmed_entries(start_date=month_start)
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        stmt = compute_income_statement(entries, lines_by_entry, accounts_dict)

        # Asset balances
        all_entries = get_confirmed_entries()
        all_lines   = get_lines_for_entries([e['entryId'] for e in all_entries])
        bs = compute_balance_sheet(accounts_dict, all_lines, all_entries)

        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({
            'thisMonth': stmt,
            'balanceSheet': bs,
            'netWorth': bs['totalAssets'] - bs['totalLiabilities'],
        }, cls=DecimalEncoder)}

    # ── GET /api/reports/income-statement ─────────────────────
    if 'income-statement' in path:
        entries = get_confirmed_entries(
            start_date=params.get('startDate'),
            end_date=params.get('endDate'),
        )
        lines_by_entry = get_lines_for_entries([e['entryId'] for e in entries])
        result = compute_income_statement(entries, lines_by_entry, accounts_dict)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(result, cls=DecimalEncoder)}

    # ── GET /api/reports/balance-sheet ────────────────────────
    if 'balance-sheet' in path:
        all_entries = get_confirmed_entries(end_date=params.get('asOf'))
        all_lines   = get_lines_for_entries([e['entryId'] for e in all_entries])
        result = compute_balance_sheet(accounts_dict, all_lines, all_entries)
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(result, cls=DecimalEncoder)}

    # ── GET /api/reports/net-worth ────────────────────────────
    if 'net-worth' in path:
        from datetime import datetime
        now = datetime.utcnow()
        monthly = []
        for i in range(6):
            m = (now.month - i - 1) % 12 + 1
            y = now.year if now.month - i > 0 else now.year - 1
            end = f'{y}-{m:02d}-31'
            entries = get_confirmed_entries(end_date=end)
            lines   = get_lines_for_entries([e['entryId'] for e in entries])
            bs = compute_balance_sheet(accounts_dict, lines, entries)
            monthly.append({
                'month': f'{y}-{m:02d}',
                'netWorth': bs['totalAssets'] - bs['totalLiabilities'],
            })
        monthly.reverse()
        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps(monthly, cls=DecimalEncoder)}

    return {'statusCode': 404, 'headers': CORS, 'body': json.dumps({'error': 'Not found'})}
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest test/lambda/test_query.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add lambda/query/ test/lambda/test_query.py
git commit -m "feat: add QueryLambda for accounts, entries, and financial reports"
```

---

## Task 9: ExportLambda

**Files:**
- Create: `lambda/export/index.py`
- Create: `lambda/export/requirements.txt`

- [ ] **Step 1: Create `lambda/export/requirements.txt`**

```
boto3
```

- [ ] **Step 2: Create `lambda/export/index.py`**

```python
import boto3
import json
import os
import csv
import io
from collections import defaultdict
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')

ENTRIES_TABLE = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE   = os.environ.get('LINES_TABLE', 'finance-journal-lines')
ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')

CORS = {'Access-Control-Allow-Origin': '*'}


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        return super().default(o)


def handler(event, context):
    params = event.get('queryStringParameters') or {}
    start_date = params.get('startDate')
    end_date   = params.get('endDate')

    entries_table  = dynamodb.Table(ENTRIES_TABLE)
    lines_table    = dynamodb.Table(LINES_TABLE)
    accounts_table = dynamodb.Table(ACCOUNTS_TABLE)

    # Fetch accounts for names
    accounts = {a['accountId']: a['name']
                for a in accounts_table.scan()['Items']}

    # Fetch confirmed entries
    fe = '#s = :confirmed'
    ea_names = {'#s': 'status'}
    ea_vals  = {':confirmed': 'CONFIRMED'}
    if start_date:
        fe += ' AND #d >= :start'
        ea_names['#d'] = 'date'
        ea_vals[':start'] = start_date
    if end_date:
        fe += ' AND #d <= :end'
        ea_names.setdefault('#d', 'date')
        ea_vals[':end'] = end_date

    entries = entries_table.scan(
        FilterExpression=fe,
        ExpressionAttributeNames=ea_names,
        ExpressionAttributeValues=ea_vals,
    )['Items']
    entries.sort(key=lambda e: e['date'])

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Description', 'Source', 'Account', 'Direction', 'Amount', 'Note'])

    for entry in entries:
        lines = lines_table.query(
            KeyConditionExpression='entryId = :e',
            ExpressionAttributeValues={':e': entry['entryId']},
        )['Items']
        for line in lines:
            writer.writerow([
                entry['date'],
                entry['description'],
                entry['source'],
                accounts.get(line['accountId'], line['accountId']),
                line['direction'],
                float(line['amount']),
                line.get('note', ''),
            ])

    csv_content = output.getvalue()

    return {
        'statusCode': 200,
        'headers': {
            **CORS,
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename="transactions.csv"',
        },
        'body': csv_content,
    }
```

- [ ] **Step 3: Commit**

```bash
git add lambda/export/
git commit -m "feat: add ExportLambda for CSV download"
```

---

## Task 10: Frontend Base

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/config.js`
- Create: `frontend/api.js`

- [ ] **Step 1: Create `frontend/config.js`**

```javascript
// Replaced at deploy time by CDK with actual API URL
window.API_BASE = window.API_BASE || 'http://localhost:3000';
```

- [ ] **Step 2: Create `frontend/api.js`**

```javascript
const API = {
  async request(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`${window.API_BASE}${path}`, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || 'Request failed');
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },

  get:    (path)        => API.request('GET', path),
  post:   (path, body)  => API.request('POST', path, body),
  put:    (path, body)  => API.request('PUT', path, body),
  delete: (path)        => API.request('DELETE', path),

  async uploadFile(filename, contentType, file) {
    const { uploadUrl, key } = await API.post('/api/upload', { filename, contentType });
    await fetch(uploadUrl, { method: 'PUT', body: file, headers: { 'Content-Type': contentType } });
    return key;
  },
};
```

- [ ] **Step 3: Create `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Finance — Personal Accounting</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="config.js"></script>
  <script src="api.js"></script>
  <style>
    .card  { @apply bg-white rounded-xl shadow-sm border border-gray-100 p-6; }
    .btn   { @apply px-4 py-2 rounded-lg text-sm font-medium transition; }
    .btn-primary { @apply btn bg-blue-600 text-white hover:bg-blue-700; }
    .btn-ghost   { @apply btn text-gray-600 hover:bg-gray-100; }
    .badge-debit  { @apply inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-700; }
    .badge-credit { @apply inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700; }
    .badge-pending   { @apply inline-flex items-center px-2 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700; }
    .badge-confirmed { @apply inline-flex items-center px-2 py-0.5 rounded text-xs bg-green-100 text-green-700; }
  </style>
</head>
<body class="bg-gray-50 min-h-screen font-sans">

  <!-- Sidebar nav -->
  <div class="flex h-screen overflow-hidden">
    <nav class="w-56 bg-white border-r border-gray-200 flex flex-col p-4 gap-1 shrink-0">
      <div class="flex items-center gap-2 mb-6 px-2">
        <div class="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold text-sm">F</div>
        <span class="font-bold text-gray-900">Finance</span>
      </div>
      <a href="#dashboard"     class="nav-link" data-page="dashboard">Dashboard</a>
      <a href="#upload"        class="nav-link" data-page="upload">Upload</a>
      <a href="#transactions"  class="nav-link" data-page="transactions">Transactions</a>
      <a href="#new"           class="nav-link" data-page="new">Manual Entry</a>
      <a href="#reports"       class="nav-link" data-page="reports">Reports</a>
      <a href="#settings"      class="nav-link" data-page="settings">Settings</a>
    </nav>

    <!-- Main content -->
    <main id="app" class="flex-1 overflow-y-auto p-8"></main>
  </div>

  <script src="pages/dashboard.js"></script>
  <script src="pages/upload.js"></script>
  <script src="pages/transactions.js"></script>
  <script src="pages/manual-entry.js"></script>
  <script src="pages/reports.js"></script>
  <script src="pages/settings.js"></script>

  <script>
    const PAGES = { dashboard, upload, transactions, 'new': manualEntry, reports, settings };

    function setActive(page) {
      document.querySelectorAll('.nav-link').forEach(a => {
        const active = a.dataset.page === page;
        a.className = active
          ? 'nav-link block px-3 py-2 rounded-lg text-sm font-medium bg-blue-50 text-blue-700'
          : 'nav-link block px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-50';
      });
    }

    function route() {
      const page = location.hash.replace('#', '') || 'dashboard';
      const render = PAGES[page] || PAGES.dashboard;
      setActive(page);
      document.getElementById('app').innerHTML = '';
      render(document.getElementById('app'));
    }

    window.addEventListener('hashchange', route);
    route();
  </script>
</body>
</html>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/config.js frontend/api.js
git commit -m "feat: add frontend SPA shell with sidebar nav and router"
```

---

## Task 11: Frontend — Dashboard Page

**Files:**
- Create: `frontend/pages/dashboard.js`

- [ ] **Step 1: Create `frontend/pages/dashboard.js`**

```javascript
async function dashboard(app) {
  app.innerHTML = `
    <div class="max-w-5xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>
      <div id="dash-loading" class="text-gray-400">Loading...</div>
      <div id="dash-content" class="hidden space-y-6">
        <!-- Summary cards -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4" id="summary-cards"></div>
        <!-- Charts row -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="card">
            <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Expenses by Category</h2>
            <canvas id="pieChart" height="200"></canvas>
          </div>
          <div class="card">
            <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">6-Month Trend</h2>
            <canvas id="trendChart" height="200"></canvas>
          </div>
        </div>
      </div>
    </div>`;

  try {
    const data = await API.get('/api/summary');
    document.getElementById('dash-loading').remove();
    document.getElementById('dash-content').classList.remove('hidden');

    const { thisMonth, netWorth } = data;
    const cards = [
      { label: 'This Month Income',   value: fmt(thisMonth.totalIncome),   color: 'text-green-600' },
      { label: 'This Month Expenses', value: fmt(thisMonth.totalExpenses),  color: 'text-red-600' },
      { label: 'Net Income',          value: fmt(thisMonth.netIncome),      color: thisMonth.netIncome >= 0 ? 'text-green-600' : 'text-red-600' },
      { label: 'Net Worth',           value: fmt(netWorth),                 color: 'text-blue-600' },
    ];
    document.getElementById('summary-cards').innerHTML = cards.map(c => `
      <div class="card">
        <p class="text-xs font-medium text-gray-500 uppercase tracking-wide">${c.label}</p>
        <p class="text-2xl font-bold mt-1 ${c.color}">${c.value}</p>
      </div>`).join('');

    // Pie chart — expenses by category
    const expLabels = thisMonth.expenses.map(e => e.name);
    const expData   = thisMonth.expenses.map(e => e.amount);
    new Chart(document.getElementById('pieChart'), {
      type: 'doughnut',
      data: { labels: expLabels, datasets: [{ data: expData, backgroundColor: PALETTE }] },
      options: { plugins: { legend: { position: 'right' } } },
    });

    // Trend chart — net worth over 6 months
    const nwData = await API.get('/api/reports/net-worth');
    new Chart(document.getElementById('trendChart'), {
      type: 'line',
      data: {
        labels: nwData.map(d => d.month),
        datasets: [{
          label: 'Net Worth',
          data: nwData.map(d => d.netWorth),
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.3,
        }],
      },
      options: { scales: { y: { ticks: { callback: v => '¥' + v.toLocaleString() } } } },
    });
  } catch (e) {
    document.getElementById('dash-loading').textContent = 'Error: ' + e.message;
  }
}

const PALETTE = ['#3b82f6','#ef4444','#f59e0b','#10b981','#8b5cf6','#ec4899','#06b6d4','#84cc16'];
const fmt = v => '¥' + Number(v).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
```

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/dashboard.js
git commit -m "feat: add Dashboard page with summary cards and charts"
```

---

## Task 12: Frontend — Upload Page

**Files:**
- Create: `frontend/pages/upload.js`

- [ ] **Step 1: Create `frontend/pages/upload.js`**

```javascript
async function upload(app) {
  app.innerHTML = `
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Upload Statement or Receipt</h1>

      <!-- Drop zone -->
      <div id="drop-zone" class="card border-2 border-dashed border-gray-300 text-center cursor-pointer hover:border-blue-400 transition mb-6">
        <div class="py-10">
          <p class="text-gray-500 mb-2">Drag & drop a PDF or image here</p>
          <p class="text-gray-400 text-sm mb-4">or</p>
          <label class="btn-primary cursor-pointer">
            Choose File
            <input type="file" id="file-input" accept=".pdf,.jpg,.jpeg,.png" class="hidden" />
          </label>
        </div>
      </div>

      <div id="status" class="hidden card mb-6"></div>

      <!-- Pending entries table -->
      <div id="pending-section" class="hidden">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-gray-800">Review Parsed Entries</h2>
          <button id="confirm-all" class="btn-primary">Confirm All</button>
        </div>
        <div id="pending-list" class="space-y-3"></div>
      </div>
    </div>`;

  const dropZone  = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const status    = document.getElementById('status');

  function showStatus(msg, color = 'text-gray-700') {
    status.className = `card mb-6 ${color}`;
    status.textContent = msg;
    status.classList.remove('hidden');
  }

  async function handleFile(file) {
    showStatus(`Uploading ${file.name}…`);
    try {
      const key = await API.uploadFile(file.name, file.type, file);
      showStatus('File uploaded. Parsing with Claude AI… this may take 20–30 seconds.', 'text-blue-600');
      // Poll for pending entries
      await new Promise(r => setTimeout(r, 15000));
      await loadPending();
      showStatus('Parsing complete. Review entries below.', 'text-green-600');
    } catch (e) {
      if (e.message.includes('DUPLICATE')) {
        showStatus('This file has already been uploaded.', 'text-yellow-600');
      } else {
        showStatus('Error: ' + e.message, 'text-red-600');
      }
    }
  }

  async function loadPending() {
    const entries = await API.get('/api/entries?status=PENDING');
    const pending = entries.filter(e => e.status === 'PENDING');
    if (!pending.length) return;

    document.getElementById('pending-section').classList.remove('hidden');
    const list = document.getElementById('pending-list');
    list.innerHTML = pending.map(e => `
      <div class="card" id="entry-${e.entryId}">
        <div class="flex justify-between items-start mb-3">
          <div>
            <p class="font-medium text-gray-800">${e.date} — ${e.description}</p>
            <span class="badge-pending">${e.source}</span>
          </div>
          <button onclick="confirmEntry('${e.entryId}')" class="btn-primary text-xs">Confirm</button>
        </div>
        <table class="w-full text-sm">
          <thead><tr class="text-gray-400 text-xs uppercase">
            <th class="text-left pb-1">Account</th>
            <th class="text-left pb-1">Direction</th>
            <th class="text-right pb-1">Amount</th>
            <th class="text-left pb-1">Note</th>
          </tr></thead>
          <tbody>
            ${(e.lines || []).map(l => `
            <tr class="border-t border-gray-50">
              <td class="py-1">${l.accountId}</td>
              <td class="py-1"><span class="${l.direction === 'DEBIT' ? 'badge-debit' : 'badge-credit'}">${l.direction}</span></td>
              <td class="py-1 text-right">¥${parseFloat(l.amount).toFixed(2)}</td>
              <td class="py-1 text-gray-400">${l.note || ''}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`).join('');
  }

  window.confirmEntry = async (id) => {
    await API.put(`/api/entries/${id}/confirm`);
    document.getElementById(`entry-${id}`)?.remove();
  };

  document.getElementById('confirm-all').onclick = async () => {
    const cards = document.querySelectorAll('[id^="entry-"]');
    for (const card of cards) {
      const id = card.id.replace('entry-', '');
      await API.put(`/api/entries/${id}/confirm`);
      card.remove();
    }
    showStatus('All entries confirmed.', 'text-green-600');
  };

  dropZone.ondragover  = e => { e.preventDefault(); dropZone.classList.add('border-blue-400'); };
  dropZone.ondragleave = () => dropZone.classList.remove('border-blue-400');
  dropZone.ondrop = e => { e.preventDefault(); handleFile(e.dataTransfer.files[0]); };
  fileInput.onchange   = () => fileInput.files[0] && handleFile(fileInput.files[0]);

  await loadPending();
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/upload.js
git commit -m "feat: add Upload page with drag-and-drop and entry confirmation"
```

---

## Task 13: Frontend — Transactions, Manual Entry, Reports, Settings Pages

**Files:**
- Create: `frontend/pages/transactions.js`
- Create: `frontend/pages/manual-entry.js`
- Create: `frontend/pages/reports.js`
- Create: `frontend/pages/settings.js`

- [ ] **Step 1: Create `frontend/pages/transactions.js`**

```javascript
async function transactions(app) {
  app.innerHTML = `
    <div class="max-w-5xl mx-auto">
      <div class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-bold text-gray-900">Transactions</h1>
        <a href="#new" class="btn-primary">+ New Entry</a>
      </div>
      <div class="card mb-4 flex gap-4 flex-wrap">
        <input id="filter-start" type="date" class="border rounded-lg px-3 py-1.5 text-sm" placeholder="From" />
        <input id="filter-end"   type="date" class="border rounded-lg px-3 py-1.5 text-sm" placeholder="To" />
        <button onclick="loadEntries()" class="btn-primary text-sm">Filter</button>
        <a id="csv-link" class="btn-ghost text-sm ml-auto">Export CSV</a>
      </div>
      <div id="entries-list" class="space-y-2"></div>
    </div>`;

  async function loadEntries() {
    const start = document.getElementById('filter-start').value;
    const end   = document.getElementById('filter-end').value;
    let url = '/api/entries';
    const q = [];
    if (start) q.push(`startDate=${start}`);
    if (end)   q.push(`endDate=${end}`);
    if (q.length) url += '?' + q.join('&');

    const csvQ = q.join('&');
    document.getElementById('csv-link').href = `${window.API_BASE}/api/export/csv${csvQ ? '?' + csvQ : ''}`;

    const entries = await API.get(url).catch(e => { alert(e.message); return []; });
    const list = document.getElementById('entries-list');
    if (!entries.length) { list.innerHTML = '<p class="text-gray-400 text-center py-10">No transactions found.</p>'; return; }

    list.innerHTML = entries.map(e => `
      <div class="card">
        <div class="flex justify-between items-start">
          <div>
            <p class="font-medium text-gray-800">${e.date} — ${e.description}</p>
            <span class="text-xs text-gray-400">${e.source}</span>
          </div>
          <div class="flex gap-2">
            <button onclick="deleteEntry('${e.entryId}')" class="text-xs text-red-500 hover:underline">Delete</button>
          </div>
        </div>
        <div class="mt-3 border-t pt-3 grid grid-cols-4 gap-1 text-xs text-gray-500 font-medium uppercase">
          <span>Account</span><span>Direction</span><span class="text-right">Amount</span><span>Note</span>
        </div>
        ${(e.lines || []).map(l => `
        <div class="grid grid-cols-4 gap-1 text-sm py-1 border-t border-gray-50">
          <span class="text-gray-700">${l.accountId}</span>
          <span><span class="${l.direction === 'DEBIT' ? 'badge-debit' : 'badge-credit'}">${l.direction}</span></span>
          <span class="text-right text-gray-800">¥${parseFloat(l.amount).toFixed(2)}</span>
          <span class="text-gray-400">${l.note || ''}</span>
        </div>`).join('')}
      </div>`).join('');
  }

  window.deleteEntry = async (id) => {
    if (!confirm('Delete this entry?')) return;
    await API.delete(`/api/entries/${id}`);
    loadEntries();
  };

  loadEntries();
}
```

- [ ] **Step 2: Create `frontend/pages/manual-entry.js`**

```javascript
let accounts = [];

async function manualEntry(app) {
  accounts = await API.get('/api/accounts').then(flattenTree).catch(() => []);

  app.innerHTML = `
    <div class="max-w-2xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Manual Entry</h1>
      <div class="card">
        <div class="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label class="text-sm text-gray-600">Date</label>
            <input id="me-date" type="date" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              value="${new Date().toISOString().slice(0,10)}" />
          </div>
          <div>
            <label class="text-sm text-gray-600">Description</label>
            <input id="me-desc" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. Coffee at Starbucks" />
          </div>
        </div>

        <div class="mb-2 flex items-center justify-between">
          <span class="text-sm font-medium text-gray-700">Journal Lines</span>
          <span id="balance-indicator" class="text-xs text-gray-400">Balance: ¥0.00</span>
        </div>
        <div id="lines-container" class="space-y-2 mb-4"></div>
        <button onclick="addLine()" class="btn-ghost text-sm w-full border border-dashed border-gray-300">+ Add Line</button>

        <div class="mt-6 flex justify-end">
          <button onclick="submitEntry()" class="btn-primary">Save Entry</button>
        </div>
        <p id="me-error" class="mt-2 text-sm text-red-500 hidden"></p>
      </div>
    </div>`;

  addLine('DEBIT');
  addLine('CREDIT');
}

function flattenTree(nodes, result = []) {
  for (const n of nodes) { result.push(n); if (n.children) flattenTree(n.children, result); }
  return result;
}

function accountOptions() {
  return accounts.map(a => `<option value="${a.accountId}">${a.accountId} ${a.name}</option>`).join('');
}

let lineCount = 0;
window.addLine = function(direction = 'DEBIT') {
  const id = ++lineCount;
  const el = document.createElement('div');
  el.id = `line-${id}`;
  el.className = 'flex gap-2 items-center';
  el.innerHTML = `
    <select class="line-dir border rounded px-2 py-1.5 text-sm">
      <option ${direction === 'DEBIT' ? 'selected' : ''}>DEBIT</option>
      <option ${direction === 'CREDIT' ? 'selected' : ''}>CREDIT</option>
    </select>
    <select class="line-acct border rounded px-2 py-1.5 text-sm flex-1">${accountOptions()}</select>
    <input class="line-amt border rounded px-2 py-1.5 text-sm w-28" type="number" step="0.01" placeholder="0.00" oninput="updateBalance()" />
    <input class="line-note border rounded px-2 py-1.5 text-sm flex-1" type="text" placeholder="Note" />
    <button onclick="document.getElementById('line-${id}').remove(); updateBalance()" class="text-gray-400 hover:text-red-500">✕</button>`;
  document.getElementById('lines-container').appendChild(el);
};

window.updateBalance = function() {
  let debit = 0, credit = 0;
  document.querySelectorAll('#lines-container > div').forEach(row => {
    const dir = row.querySelector('.line-dir').value;
    const amt = parseFloat(row.querySelector('.line-amt').value) || 0;
    if (dir === 'DEBIT') debit += amt; else credit += amt;
  });
  const diff = debit - credit;
  const el   = document.getElementById('balance-indicator');
  el.textContent = `Balance: ¥${Math.abs(diff).toFixed(2)} ${diff === 0 ? '✓' : diff > 0 ? '(debit heavy)' : '(credit heavy)'}`;
  el.className = `text-xs ${Math.abs(diff) < 0.01 ? 'text-green-600' : 'text-red-500'}`;
};

window.submitEntry = async function() {
  const lines = [];
  document.querySelectorAll('#lines-container > div').forEach(row => {
    lines.push({
      direction: row.querySelector('.line-dir').value,
      accountId: row.querySelector('.line-acct').value,
      amount:    parseFloat(row.querySelector('.line-amt').value) || 0,
      note:      row.querySelector('.line-note').value,
    });
  });

  const err = document.getElementById('me-error');
  const debit  = lines.filter(l => l.direction === 'DEBIT').reduce((s, l) => s + l.amount, 0);
  const credit = lines.filter(l => l.direction === 'CREDIT').reduce((s, l) => s + l.amount, 0);
  if (Math.abs(debit - credit) > 0.01) {
    err.textContent = 'Debit and credit amounts must balance.';
    err.classList.remove('hidden');
    return;
  }

  try {
    await API.post('/api/entries', {
      date:        document.getElementById('me-date').value,
      description: document.getElementById('me-desc').value,
      lines,
    });
    location.hash = '#transactions';
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
};
```

- [ ] **Step 3: Create `frontend/pages/reports.js`**

```javascript
async function reports(app) {
  const now = new Date();
  const startOfYear = `${now.getFullYear()}-01-01`;
  const today = now.toISOString().slice(0, 10);

  app.innerHTML = `
    <div class="max-w-4xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Reports</h1>
      <div class="card mb-6 flex gap-4 flex-wrap items-end">
        <div>
          <label class="text-sm text-gray-600">From</label>
          <input id="r-start" type="date" class="block border rounded px-3 py-1.5 text-sm mt-1" value="${startOfYear}" />
        </div>
        <div>
          <label class="text-sm text-gray-600">To</label>
          <input id="r-end" type="date" class="block border rounded px-3 py-1.5 text-sm mt-1" value="${today}" />
        </div>
        <button onclick="loadReports()" class="btn-primary">Run</button>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div class="card">
          <h2 class="font-semibold text-gray-800 mb-4">Income Statement</h2>
          <div id="income-stmt"></div>
        </div>
        <div class="card">
          <h2 class="font-semibold text-gray-800 mb-4">Balance Sheet</h2>
          <div id="balance-sheet"></div>
        </div>
      </div>

      <div class="card mb-4">
        <h2 class="font-semibold text-gray-800 mb-4">Net Worth Timeline</h2>
        <canvas id="nwChart" height="100"></canvas>
      </div>

      <div class="flex justify-end">
        <a id="export-link" class="btn-primary" download="transactions.csv">Export CSV</a>
      </div>
    </div>`;

  let nwChart;

  window.loadReports = async function() {
    const start = document.getElementById('r-start').value;
    const end   = document.getElementById('r-end').value;
    const q     = `?startDate=${start}&endDate=${end}`;

    document.getElementById('export-link').href = `${window.API_BASE}/api/export/csv${q}`;

    const [is, bs, nw] = await Promise.all([
      API.get(`/api/reports/income-statement${q}`),
      API.get(`/api/reports/balance-sheet?asOf=${end}`),
      API.get('/api/reports/net-worth'),
    ]);

    document.getElementById('income-stmt').innerHTML = `
      <table class="w-full text-sm">
        <thead><tr class="text-xs text-gray-400 uppercase"><th class="text-left pb-2">Account</th><th class="text-right pb-2">Amount</th></tr></thead>
        <tbody>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-2 pb-1">Income</td></tr>
          ${is.income.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-green-600">¥${r.amount.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td class="pt-2">Total Income</td><td class="text-right text-green-600 pt-2">¥${is.totalIncome.toFixed(2)}</td></tr>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Expenses</td></tr>
          ${is.expenses.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">¥${r.amount.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td class="pt-2">Total Expenses</td><td class="text-right text-red-500 pt-2">¥${is.totalExpenses.toFixed(2)}</td></tr>
          <tr class="border-t-2 font-bold text-lg"><td class="pt-2">Net Income</td><td class="text-right pt-2 ${is.netIncome >= 0 ? 'text-green-600' : 'text-red-500'}">¥${is.netIncome.toFixed(2)}</td></tr>
        </tbody>
      </table>`;

    document.getElementById('balance-sheet').innerHTML = `
      <table class="w-full text-sm">
        <thead><tr class="text-xs text-gray-400 uppercase"><th class="text-left pb-2">Account</th><th class="text-right pb-2">Balance</th></tr></thead>
        <tbody>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pb-1">Assets</td></tr>
          ${bs.assets.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td class="pt-2">Total Assets</td><td class="text-right pt-2">¥${bs.totalAssets.toFixed(2)}</td></tr>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Liabilities</td></tr>
          ${bs.liabilities.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td colspan="2" class="pt-4 pb-1 text-gray-500">Equity</td></tr>
          ${bs.equity.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
        </tbody>
      </table>`;

    if (nwChart) nwChart.destroy();
    nwChart = new Chart(document.getElementById('nwChart'), {
      type: 'line',
      data: {
        labels: nw.map(d => d.month),
        datasets: [{
          label: 'Net Worth',
          data: nw.map(d => d.netWorth),
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.3,
        }],
      },
      options: { scales: { y: { ticks: { callback: v => '¥' + v.toLocaleString() } } } },
    });
  };

  loadReports();
}
```

- [ ] **Step 4: Create `frontend/pages/settings.js`**

```javascript
async function settings(app) {
  app.innerHTML = `
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Settings</h1>
      <div class="card mb-6">
        <h2 class="font-semibold text-gray-800 mb-4">Chart of Accounts</h2>
        <div id="acct-tree" class="text-sm"></div>
      </div>
      <div class="card">
        <h2 class="font-semibold text-gray-800 mb-4">Add Custom Sub-Account</h2>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-gray-600">Parent Account</label>
            <select id="s-parent" class="mt-1 w-full border rounded px-3 py-2 text-sm"></select>
          </div>
          <div>
            <label class="text-sm text-gray-600">Account ID</label>
            <input id="s-id" type="text" class="mt-1 w-full border rounded px-3 py-2 text-sm" placeholder="e.g. 1110" />
          </div>
          <div>
            <label class="text-sm text-gray-600">Name</label>
            <input id="s-name" type="text" class="mt-1 w-full border rounded px-3 py-2 text-sm" placeholder="e.g. ICBC Card" />
          </div>
          <div>
            <label class="text-sm text-gray-600">Type</label>
            <select id="s-type" class="mt-1 w-full border rounded px-3 py-2 text-sm">
              <option>ASSET</option><option>LIABILITY</option><option>INCOME</option><option>EXPENSE</option><option>EQUITY</option>
            </select>
          </div>
        </div>
        <button onclick="addAccount()" class="mt-4 btn-primary">Add Account</button>
        <p id="s-msg" class="mt-2 text-sm hidden"></p>
      </div>
    </div>`;

  const tree = await API.get('/api/accounts').catch(() => []);
  renderTree(tree, document.getElementById('acct-tree'), 0);

  const flat = flattenAccounts(tree);
  const parentSel = document.getElementById('s-parent');
  flat.forEach(a => {
    const o = document.createElement('option');
    o.value = a.accountId;
    o.textContent = `${a.accountId} ${a.name}`;
    parentSel.appendChild(o);
  });

  window.addAccount = async function() {
    const msg = document.getElementById('s-msg');
    try {
      await API.post('/api/accounts', {
        accountId: document.getElementById('s-id').value,
        name:      document.getElementById('s-name').value,
        type:      document.getElementById('s-type').value,
        parentId:  document.getElementById('s-parent').value,
      });
      msg.textContent = 'Account added. Refresh to see changes.';
      msg.className = 'mt-2 text-sm text-green-600';
      msg.classList.remove('hidden');
    } catch (e) {
      msg.textContent = e.message;
      msg.className = 'mt-2 text-sm text-red-500';
      msg.classList.remove('hidden');
    }
  };
}

function renderTree(nodes, parent, depth) {
  for (const n of nodes) {
    const el = document.createElement('div');
    el.style.paddingLeft = (depth * 16) + 'px';
    el.className = 'py-1 border-b border-gray-50 flex gap-2 items-center';
    el.innerHTML = `<span class="text-gray-400 text-xs">${n.accountId}</span>
      <span class="text-gray-700">${n.name}</span>
      <span class="text-xs text-gray-300">${n.type}</span>
      ${!n.isSystem ? '<span class="text-xs text-blue-400">custom</span>' : ''}`;
    parent.appendChild(el);
    if (n.children?.length) renderTree(n.children, parent, depth + 1);
  }
}

function flattenAccounts(nodes, result = []) {
  for (const n of nodes) { result.push(n); if (n.children) flattenAccounts(n.children, result); }
  return result;
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/pages/
git commit -m "feat: add Transactions, Manual Entry, Reports, and Settings pages"
```

---

## Task 14: Deploy

**Files:**
- Modify: `frontend/config.js` (post-deploy)

- [ ] **Step 1: Build Lambda layer**

```bash
./scripts/build-layer.sh
```

- [ ] **Step 2: Bootstrap CDK (first time only)**

```bash
npx cdk bootstrap
```

- [ ] **Step 3: Deploy**

```bash
npx cdk deploy --require-approval never
```
Expected output includes:
```
FinanceStack.SiteUrl = https://<id>.cloudfront.net
FinanceStack.ApiUrl  = https://<id>.execute-api.us-east-1.amazonaws.com/prod/
FinanceStack.BucketName = finance-stack-appbucket-<id>
```

- [ ] **Step 4: Store outputs**

Copy the three output values. You'll need them in the next steps.

- [ ] **Step 5: Update `frontend/config.js` with real API URL**

Replace `http://localhost:3000` with the `ApiUrl` output — but remove the trailing `/prod/`, use just the domain:
```javascript
window.API_BASE = 'https://<id>.execute-api.us-east-1.amazonaws.com/prod';
```

- [ ] **Step 6: Store Claude API key in Secrets Manager**

```bash
aws secretsmanager put-secret-value \
  --secret-id finance/claude-api-key \
  --secret-string '{"CLAUDE_API_KEY": "sk-ant-..."}'
```

- [ ] **Step 7: Upload frontend to S3**

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)

aws s3 sync frontend/ s3://$BUCKET/ --delete
```

- [ ] **Step 8: Seed chart of accounts**

```bash
npm run seed
```

- [ ] **Step 9: Invalidate CloudFront cache**

```bash
DIST_ID=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)

aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

- [ ] **Step 10: Smoke test**

Open `SiteUrl` in browser. Verify:
- Dashboard loads without errors
- Upload page shows drop zone
- Settings page shows account tree (23 preset accounts)
- Manual Entry shows account selector populated

- [ ] **Step 11: Commit final config**

```bash
git add frontend/config.js
git commit -m "chore: update config.js with deployed API URL"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ PDF/image upload → S3 → ParseLambda → Claude → PENDING entries
- ✅ Duplicate detection via MD5 hash on `fileKey`/`fileHash` field
- ✅ Manual entry with balance validation
- ✅ Confirm flow (ConfirmLambda)
- ✅ Preset chart of accounts (seed script, 23 accounts)
- ✅ Custom sub-accounts (POST /api/accounts)
- ✅ Dashboard with charts
- ✅ Transactions list with filter + delete
- ✅ Income Statement, Balance Sheet, Net Worth Timeline
- ✅ CSV export
- ✅ Settings with account tree

**Phase 2 items (not in this plan):** tags, foreign currency, budget alerts, AI Q&A.

**Known limitation:** QueryLambda's `get_confirmed_entries` uses DynamoDB `scan` with a filter. For MVP single-user, this is acceptable. At scale, replace with a GSI query on `yearMonth`.
