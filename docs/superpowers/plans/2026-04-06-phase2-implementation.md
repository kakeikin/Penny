# Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four Phase 2 features to the PersonalFinanceApp: custom tags, foreign currency support, budget alerts, and an AI financial advisor chat.

**Architecture:** All features extend the existing serverless stack (CDK + Python Lambda + DynamoDB + vanilla JS frontend). Tags and currency are schema extensions on existing tables. Budget alerts add a new DynamoDB table and API routes. AI Advisor adds a new Lambda with Bedrock access. All frontend changes are in `frontend/pages/`.

**Tech Stack:** AWS CDK v2 (TypeScript), Python 3.12 Lambda, DynamoDB, Amazon Bedrock (Claude Sonnet 4.6 via `us.anthropic.claude-sonnet-4-6`), Vanilla JS + Tailwind CSS CDN.

**Working directory:** `/Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1`

---

## File Structure

### New files
- `lambda/advisor/index.py` — AI Financial Advisor Lambda (POST /api/advisor)
- `frontend/pages/advisor.js` — Chat UI page

### Modified files
- `lib/finance-stack.ts` — New table (budgets), new Lambda (advisor), new API routes
- `lambda/manual-entry/index.py` — Support tags + currency fields on create/update; budget CRUD
- `lambda/query/index.py` — Tag filtering, tags list, budget alerts endpoint
- `lambda/export/index.py` — Include tags + original currency in CSV
- `frontend/index.html` — Add Advisor nav item + route
- `frontend/pages/manual-entry.js` — Tag input, currency selector + rate
- `frontend/pages/upload.js` — Tag input + currency fields in edit modal
- `frontend/pages/transactions.js` — Tag badges, filter by tag, show original currency
- `frontend/pages/settings.js` — Budget management UI
- `frontend/pages/dashboard.js` — Budget alert banners

---

## Task 1: Tags — Backend (Schema + API)

**Files:**
- Modify: `lambda/manual-entry/index.py`
- Modify: `lambda/query/index.py`
- Modify: `lambda/export/index.py`

Tags are stored as a list of strings on `finance-journal-entries`. No new table needed — DynamoDB supports list attributes natively.

New API endpoints:
- `GET /api/tags` → returns all unique tags used across entries
- `GET /api/entries?tag=food` → filter by tag (extend existing endpoint)

- [ ] **Step 1: Extend ManualEntryLambda to accept tags**

In `lambda/manual-entry/index.py`, find the `POST /api/entries` handler. The current Item dict in `entries_table.put_item` is:
```python
entries_table.put_item(Item={
    'entryId':     entry_id,
    'date':        date,
    'yearMonth':   year_month,
    'description': description,
    'source':      'MANUAL',
    'status':      'CONFIRMED',
    'createdAt':   datetime.now(timezone.utc).isoformat(),
})
```

Replace with (add `tags` field):
```python
tags = body.get('tags', [])
if isinstance(tags, str):
    tags = [t.strip() for t in tags.split(',') if t.strip()]

entries_table.put_item(Item={
    'entryId':     entry_id,
    'date':        date,
    'yearMonth':   year_month,
    'description': description,
    'source':      'MANUAL',
    'status':      'CONFIRMED',
    'tags':        tags,
    'createdAt':   datetime.now(timezone.utc).isoformat(),
})
```

Also find the `PUT /api/entries/{id}` handler. After the current update expression, add tags to the update:
```python
# Current update expression likely sets description/date. Add tags:
body = json.loads(event.get('body', '{}'))
tags = body.get('tags', None)

# Build update expression dynamically
update_parts = ['#desc = :desc', '#dt = :dt', 'yearMonth = :ym']
expr_names = {'#desc': 'description', '#dt': 'date', '#s': 'status'}
expr_vals = {
    ':desc': body.get('description', ''),
    ':dt':   body.get('date', ''),
    ':ym':   body.get('date', '')[:7],
}
if tags is not None:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    update_parts.append('tags = :tags')
    expr_vals[':tags'] = tags

entries_table.update_item(
    Key={'entryId': entry_id},
    UpdateExpression='SET ' + ', '.join(update_parts),
    ExpressionAttributeNames=expr_names,
    ExpressionAttributeValues=expr_vals,
)
```

**Note:** Read the actual PUT handler first — update only the relevant lines, don't replace the whole function.

- [ ] **Step 2: Add GET /api/tags endpoint to QueryLambda**

In `lambda/query/index.py`, before the final `return 404` line, add:

```python
# GET /api/tags
if path.endswith('/tags'):
    all_entries = dynamodb.Table(ENTRIES_TABLE).scan(
        ProjectionExpression='tags',
    )['Items']
    tag_set = set()
    for e in all_entries:
        for t in e.get('tags', []):
            tag_set.add(t)
    return {'statusCode': 200, 'headers': CORS,
            'body': json.dumps(sorted(tag_set), cls=DecimalEncoder)}
```

- [ ] **Step 3: Add tag filter to GET /api/entries**

In `lambda/query/index.py`, in the `GET /api/entries` handler, after `entries = get_confirmed_entries(...)`, add tag filtering:

```python
tag_filter = params.get('tag')
if tag_filter:
    entries = [e for e in entries if tag_filter in e.get('tags', [])]
```

- [ ] **Step 4: Include tags in ExportLambda CSV**

In `lambda/export/index.py`, find the CSV header row and add `Tags` column. Find the row-writing code and add:
```python
','.join(entry.get('tags', []))
```
at the end of each row (after the Note column).

- [ ] **Step 5: Add API routes in CDK stack**

In `lib/finance-stack.ts`, find where `/api/entries` routes are defined. Add:
```typescript
const tags = api.root.getResource('api')!.addResource('tags');
tags.addMethod('GET', queryIntegration, { ...corsOptions });
```

- [ ] **Step 6: Deploy**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1
npx cdk deploy --require-approval never
```

- [ ] **Step 7: Commit**

```bash
git add lambda/manual-entry/index.py lambda/query/index.py lambda/export/index.py lib/finance-stack.ts
git commit -m "feat: add tags support to entries — create, update, filter, export"
```

---

## Task 2: Tags — Frontend

**Files:**
- Modify: `frontend/pages/manual-entry.js`
- Modify: `frontend/pages/upload.js`
- Modify: `frontend/pages/transactions.js`

- [ ] **Step 1: Add tag input to Manual Entry form**

In `frontend/pages/manual-entry.js`, find the Note input block:
```html
        <!-- Note -->
        <div class="mb-6">
          <label class="text-sm text-gray-600 font-medium">Note ...
```

Add a Tags field **before** the Note block:
```html
        <!-- Tags -->
        <div class="mb-4">
          <label class="text-sm text-gray-600 font-medium">Tags <span class="text-gray-400 font-normal">(optional, comma-separated)</span></label>
          <input id="me-tags" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"
            placeholder="e.g. food, subscription, work" />
        </div>
```

In `window.submitEntry`, read the tags value:
```javascript
const tags = document.getElementById('me-tags').value.split(',').map(t => t.trim()).filter(Boolean);
```

Add `tags` to the API.post body:
```javascript
await API.post('/api/entries', { date, description, lines, tags });
```

- [ ] **Step 2: Add tag input to Transactions edit modal**

In `frontend/pages/transactions.js`, find the Note field in the edit modal HTML and add a Tags field before it:
```html
        <div class="mb-3">
          <label class="text-sm text-gray-600 font-medium">Tags <span class="text-gray-400 font-normal">(comma-separated)</span></label>
          <input id="edit-tags" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. food, rent" />
        </div>
```

In `window.openEditModal`, pre-fill the tags:
```javascript
document.getElementById('edit-tags').value = (entry.tags || []).join(', ');
```

In `window.saveEdit`, read and send tags:
```javascript
const tags = document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);
await API.put(`/api/entries/${_editId}`, { date, description, lines, tags });
```

- [ ] **Step 3: Show tag badges in Transactions list**

In `frontend/pages/transactions.js`, find the description line in the entry card render:
```javascript
<p class="font-medium text-gray-800 truncate">${e.description}</p>
```

Add tag badges below it:
```javascript
<p class="font-medium text-gray-800 truncate">${e.description}</p>
${(e.tags || []).length ? `<div class="flex flex-wrap gap-1 mt-1">${(e.tags||[]).map(t => `<span class="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">#${t}</span>`).join('')}</div>` : ''}
```

- [ ] **Step 4: Add tag filter to Transactions page**

In `frontend/pages/transactions.js`, find the filter bar HTML. Add a tag filter dropdown after the Filter button:
```html
        <select id="filter-tag" onchange="loadEntries()" class="border rounded-lg px-3 py-1.5 text-sm text-gray-600">
          <option value="">All tags</option>
        </select>
```

In `loadEntries()`, after building `url`, add:
```javascript
const tag = document.getElementById('filter-tag').value;
if (tag) q.push(`tag=${encodeURIComponent(tag)}`);
```

Load tags on page init (after `loadEntries()`):
```javascript
API.get('/api/tags').then(tags => {
  const sel = document.getElementById('filter-tag');
  if (sel) tags.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t; opt.textContent = '#' + t;
    sel.appendChild(opt);
  });
}).catch(() => {});
```

- [ ] **Step 5: Add tag input to Upload edit modal**

In `frontend/pages/upload.js`, find the Note field in the edit modal and add Tags before it (same HTML as Step 2). In `openUploadEditModal`, pre-fill: `document.getElementById('ue-tags').value = (entry.tags || []).join(', ');`. In `saveUploadEdit`, send `tags` in the PUT body.

- [ ] **Step 6: Upload frontend**

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 sync /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1/frontend/ s3://$BUCKET/ --delete
DIST_ID=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

- [ ] **Step 7: Commit**

```bash
git add frontend/pages/manual-entry.js frontend/pages/transactions.js frontend/pages/upload.js
git commit -m "feat: tags UI — add/edit tags on entries, badges in list, filter by tag"
```

---

## Task 3: Foreign Currency — Backend

**Files:**
- Modify: `lambda/manual-entry/index.py`
- Modify: `lambda/export/index.py`

Foreign currency fields are stored on each **journal line** (not the entry). Each line can optionally have:
- `originalCurrency`: e.g. `"USD"`
- `originalAmount`: e.g. `"30.00"`
- `exchangeRate`: e.g. `"7.25"` (1 USD = 7.25 base currency)

The `amount` field remains in base currency (used for all calculations). Original currency fields are display-only.

- [ ] **Step 1: Accept currency fields in ManualEntryLambda**

In `lambda/manual-entry/index.py`, find where lines are written to DynamoDB (the `lines_table.put_item` call). Currently:
```python
for i, line in enumerate(lines):
    lines_table.put_item(Item={
        'entryId':   entry_id,
        'lineId':    f'{i:03d}',
        'accountId': line['accountId'],
        'direction': line['direction'],
        'amount':    str(line['amount']),
        'note':      line.get('note', ''),
    })
```

Replace with:
```python
for i, line in enumerate(lines):
    item = {
        'entryId':   entry_id,
        'lineId':    f'{i:03d}',
        'accountId': line['accountId'],
        'direction': line['direction'],
        'amount':    str(line['amount']),
        'note':      line.get('note', ''),
    }
    if line.get('originalCurrency') and line.get('originalAmount'):
        item['originalCurrency'] = line['originalCurrency']
        item['originalAmount']   = str(line['originalAmount'])
        item['exchangeRate']     = str(line.get('exchangeRate', '1'))
    lines_table.put_item(Item=item)
```

Apply the same change to the PUT handler's line-writing code.

- [ ] **Step 2: Include original currency in ExportLambda CSV**

In `lambda/export/index.py`, find the CSV row writer. Add two columns: `Original Amount` and `Original Currency`:
```python
orig = f"{line.get('originalCurrency', '')} {line.get('originalAmount', '')}" if line.get('originalCurrency') else ''
```
Add `orig` as a column in the CSV row.

- [ ] **Step 3: Deploy**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1
npx cdk deploy --require-approval never
```

- [ ] **Step 4: Commit**

```bash
git add lambda/manual-entry/index.py lambda/export/index.py
git commit -m "feat: store original currency/amount on journal lines"
```

---

## Task 4: Foreign Currency — Frontend

**Files:**
- Modify: `frontend/pages/manual-entry.js`
- Modify: `frontend/pages/upload.js`
- Modify: `frontend/pages/transactions.js`

- [ ] **Step 1: Add currency toggle to Manual Entry**

In `frontend/pages/manual-entry.js`, add a "Foreign Currency" toggle after the Amount field:

```html
        <!-- Foreign Currency -->
        <div class="mb-4">
          <label class="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
            <input type="checkbox" id="me-foreign" onchange="toggleForeignCurrency()" class="rounded" />
            <span>Paid in foreign currency</span>
          </label>
          <div id="me-foreign-fields" class="hidden mt-2 grid grid-cols-3 gap-2">
            <div>
              <label class="text-xs text-gray-500">Original Amount</label>
              <input id="me-orig-amount" type="number" step="0.01" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="0.00" />
            </div>
            <div>
              <label class="text-xs text-gray-500">Currency</label>
              <select id="me-orig-currency" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                <option>USD</option><option>EUR</option><option>GBP</option><option>JPY</option><option>HKD</option><option>CAD</option><option>AUD</option>
              </select>
            </div>
            <div>
              <label class="text-xs text-gray-500">Exchange Rate</label>
              <input id="me-rate" type="number" step="0.0001" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="7.25" />
            </div>
          </div>
        </div>
```

Add `window.toggleForeignCurrency`:
```javascript
window.toggleForeignCurrency = function() {
  const checked = document.getElementById('me-foreign').checked;
  document.getElementById('me-foreign-fields').classList.toggle('hidden', !checked);
};
```

In `window.submitEntry`, when building `lines`, attach currency fields to the first (DEBIT) line if foreign currency is checked:
```javascript
const isForeign = document.getElementById('me-foreign')?.checked;
if (isForeign && lines.length > 0) {
  lines[0].originalAmount   = parseFloat(document.getElementById('me-orig-amount').value) || null;
  lines[0].originalCurrency = document.getElementById('me-orig-currency').value;
  lines[0].exchangeRate     = parseFloat(document.getElementById('me-rate').value) || 1;
}
```

- [ ] **Step 2: Show original currency in Transactions list**

In `frontend/pages/transactions.js`, find the detail lines render (the `detailLines` variable). After the amount cell, add an original currency display:
```javascript
const origCurr = l.originalCurrency ? ` <span class="text-xs text-gray-400">(${l.originalCurrency} ${parseFloat(l.originalAmount).toFixed(2)})</span>` : '';
```

Change the amount cell to:
```javascript
<span class="text-gray-800">¥${parseFloat(l.amount).toFixed(2)}${origCurr}</span>
```

- [ ] **Step 3: Add currency fields to Upload edit modal**

In `frontend/pages/upload.js`, add the same foreign currency toggle HTML (same as Step 1, but with `ue-` prefix IDs: `ue-foreign`, `ue-foreign-fields`, `ue-orig-amount`, `ue-orig-currency`, `ue-rate`). In `saveUploadEdit`, attach currency fields to lines the same way.

- [ ] **Step 4: Upload frontend**

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 sync /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1/frontend/ s3://$BUCKET/ --delete
DIST_ID=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

- [ ] **Step 5: Commit**

```bash
git add frontend/pages/manual-entry.js frontend/pages/upload.js frontend/pages/transactions.js
git commit -m "feat: foreign currency UI — toggle, original amount display in detail view"
```

---

## Task 5: Budget Alerts — Backend

**Files:**
- Modify: `lib/finance-stack.ts`
- Modify: `lambda/manual-entry/index.py`
- Modify: `lambda/query/index.py`

New DynamoDB table: `finance-budgets` (PK: `accountId`). Each item: `{ accountId, monthlyLimit, name }`.

New API endpoints:
- `GET /api/budgets` → list all budgets
- `POST /api/budgets` → create/update budget for an account
- `DELETE /api/budgets/{accountId}` → remove budget
- `GET /api/alerts` → compute current alerts (spend vs 6-month average)

- [ ] **Step 1: Add budgets table to CDK stack**

In `lib/finance-stack.ts`, after the existing table definitions, add:

```typescript
const budgetsTable = new dynamodb.Table(this, 'BudgetsTable', {
  tableName: 'finance-budgets',
  partitionKey: { name: 'accountId', type: dynamodb.AttributeType.STRING },
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});
```

Grant all Lambda functions read access to budgetsTable, and manualEntryFn read/write access:
```typescript
[parseFn, confirmFn, queryFn, exportFn].forEach(fn => budgetsTable.grantReadData(fn));
budgetsTable.grantReadWriteData(manualEntryFn);
```

Add `BUDGETS_TABLE: 'finance-budgets'` to the shared `lambdaEnv` object.

Add API routes:
```typescript
const budgets = api.root.getResource('api')!.addResource('budgets');
budgets.addMethod('GET', queryIntegration, { ...corsOptions });
budgets.addMethod('POST', manualEntryIntegration, { ...corsOptions });
const budgetById = budgets.addResource('{accountId}');
budgetById.addMethod('DELETE', manualEntryIntegration, { ...corsOptions });

const alerts = api.root.getResource('api')!.addResource('alerts');
alerts.addMethod('GET', queryIntegration, { ...corsOptions });
```

- [ ] **Step 2: Add budget CRUD to ManualEntryLambda**

In `lambda/manual-entry/index.py`, add at top:
```python
BUDGETS_TABLE = os.environ.get('BUDGETS_TABLE', 'finance-budgets')
```

Add budget route handlers before the final 404 return:

```python
# POST /api/budgets
if method == 'POST' and path.endswith('/budgets'):
    body = json.loads(event.get('body', '{}'))
    account_id   = body.get('accountId')
    monthly_limit = float(body.get('monthlyLimit', 0))
    name         = body.get('name', '')
    if not account_id or monthly_limit <= 0:
        return {'statusCode': 400, 'headers': CORS, 'body': json.dumps({'error': 'accountId and monthlyLimit required'})}
    dynamodb.Table(BUDGETS_TABLE).put_item(Item={
        'accountId':    account_id,
        'monthlyLimit': str(monthly_limit),
        'name':         name,
    })
    return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'ok': True})}

# DELETE /api/budgets/{accountId}
if method == 'DELETE' and '/budgets/' in path:
    account_id = path.split('/budgets/')[-1]
    dynamodb.Table(BUDGETS_TABLE).delete_item(Key={'accountId': account_id})
    return {'statusCode': 200, 'headers': CORS, 'body': json.dumps({'ok': True})}
```

- [ ] **Step 3: Add GET /api/budgets and GET /api/alerts to QueryLambda**

In `lambda/query/index.py`, add at top:
```python
BUDGETS_TABLE = os.environ.get('BUDGETS_TABLE', 'finance-budgets')
```

Add before the final `return 404`:

```python
# GET /api/budgets
if path.endswith('/budgets'):
    items = dynamodb.Table(BUDGETS_TABLE).scan()['Items']
    return {'statusCode': 200, 'headers': CORS,
            'body': json.dumps(items, cls=DecimalEncoder)}

# GET /api/alerts
if path.endswith('/alerts'):
    from datetime import datetime
    budgets = dynamodb.Table(BUDGETS_TABLE).scan()['Items']
    if not budgets:
        return {'statusCode': 200, 'headers': CORS, 'body': json.dumps([])}

    alerts_out = []
    now = datetime.utcnow()

    for budget in budgets:
        acct_id = budget['accountId']
        limit   = float(budget['monthlyLimit'])

        # Collect spending for last 6 complete months + current month
        monthly_totals = []
        for i in range(7):  # 0 = current month, 1-6 = past months
            m = (now.month - i - 1) % 12 + 1
            y = now.year if (now.month - i) > 0 else now.year - 1
            ym = f'{y}-{m:02d}'
            entries = dynamodb.Table(ENTRIES_TABLE).scan(
                FilterExpression='yearMonth = :ym AND #s = :confirmed',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':ym': ym, ':confirmed': 'CONFIRMED'},
            )['Items']
            total = 0.0
            for entry in entries:
                lines = dynamodb.Table(LINES_TABLE).query(
                    KeyConditionExpression='entryId = :e',
                    ExpressionAttributeValues={':e': entry['entryId']},
                )['Items']
                for line in lines:
                    if line['accountId'] == acct_id and line['direction'] == 'DEBIT':
                        total += float(line['amount'])
            monthly_totals.append({'month': ym, 'total': total})

        current_month_total = monthly_totals[0]['total']
        past_6 = [m['total'] for m in monthly_totals[1:7] if m['total'] > 0]
        if not past_6:
            continue
        avg = sum(past_6) / len(past_6)

        # Alert if: current month > limit OR current month > avg * 1.1
        over_limit   = current_month_total > limit
        over_avg     = avg > 0 and current_month_total > avg * 1.10

        if over_limit or over_avg:
            alerts_out.append({
                'accountId':          acct_id,
                'accountName':        budget.get('name', acct_id),
                'monthlyLimit':       limit,
                'currentMonthTotal':  current_month_total,
                'sixMonthAverage':    round(avg, 2),
                'overLimit':          over_limit,
                'overAverage':        over_avg,
                'percentOverAverage': round((current_month_total / avg - 1) * 100, 1) if avg > 0 else 0,
            })

    return {'statusCode': 200, 'headers': CORS,
            'body': json.dumps(alerts_out, cls=DecimalEncoder)}
```

- [ ] **Step 4: Deploy**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1
npx cdk deploy --require-approval never
```

- [ ] **Step 5: Commit**

```bash
git add lib/finance-stack.ts lambda/manual-entry/index.py lambda/query/index.py
git commit -m "feat: budget alerts — table, CRUD API, 6-month trend alert computation"
```

---

## Task 6: Budget Alerts — Frontend

**Files:**
- Modify: `frontend/pages/settings.js`
- Modify: `frontend/pages/dashboard.js`

- [ ] **Step 1: Add Budget Management section to Settings page**

In `frontend/pages/settings.js`, at the end of the rendered HTML (after the account tree section), add a Budget Management section. Read the file first. Then append to the `app.innerHTML` string (or add to the existing template):

```html
      <!-- Budget Management -->
      <div class="card mt-6">
        <h2 class="text-lg font-semibold text-gray-800 mb-4">Budget Alerts</h2>
        <p class="text-sm text-gray-500 mb-4">Set a monthly spending limit for any expense account. You'll see an alert on the Dashboard when spending exceeds the limit or rises more than 10% above the 6-month average.</p>
        <div id="budget-list" class="space-y-2 mb-4"></div>
        <div class="border-t pt-4">
          <h3 class="text-sm font-medium text-gray-700 mb-3">Add Budget</h3>
          <div class="grid grid-cols-3 gap-3">
            <div>
              <label class="text-xs text-gray-500">Account</label>
              <select id="budget-acct" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-xs text-gray-500">Monthly Limit (¥)</label>
              <input id="budget-limit" type="number" step="0.01" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="500.00" />
            </div>
            <div class="flex items-end">
              <button onclick="saveBudget()" class="w-full bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium py-2 rounded-lg transition-colors">Save Budget</button>
            </div>
          </div>
        </div>
      </div>
```

Add budget management JS at the end of the `settings` function:

```javascript
  // Load expense accounts for budget selector
  async function loadBudgetAccounts() {
    const tree = await API.get('/api/accounts').catch(() => []);
    const flat = [];
    function flattenAll(nodes) { for (const n of nodes) { flat.push(n); if (n.children) flattenAll(n.children); } }
    flattenAll(tree);
    const expenseAccts = flat.filter(a => a.type === 'EXPENSE');
    const sel = document.getElementById('budget-acct');
    if (sel) sel.innerHTML = expenseAccts.map(a => `<option value="${a.accountId}" data-name="${a.name}">${a.name}</option>`).join('');
  }

  async function loadBudgets() {
    const budgets = await API.get('/api/budgets').catch(() => []);
    const list = document.getElementById('budget-list');
    if (!list) return;
    if (!budgets.length) { list.innerHTML = '<p class="text-sm text-gray-400">No budgets set.</p>'; return; }
    list.innerHTML = budgets.map(b => `
      <div class="flex justify-between items-center py-2 border-b border-gray-50">
        <div>
          <span class="text-sm font-medium text-gray-700">${b.name || b.accountId}</span>
          <span class="text-xs text-gray-400 ml-2">¥${parseFloat(b.monthlyLimit).toFixed(2)}/month</span>
        </div>
        <button onclick="deleteBudget('${b.accountId}')" class="text-xs text-red-400 hover:text-red-600">Remove</button>
      </div>`).join('');
  }

  window.saveBudget = async () => {
    const sel   = document.getElementById('budget-acct');
    const limit = parseFloat(document.getElementById('budget-limit').value);
    if (!sel || !limit) return;
    const name = sel.options[sel.selectedIndex]?.dataset.name || sel.value;
    await API.post('/api/budgets', { accountId: sel.value, monthlyLimit: limit, name });
    await loadBudgets();
  };

  window.deleteBudget = async (accountId) => {
    await API.delete(`/api/budgets/${accountId}`);
    await loadBudgets();
  };

  loadBudgetAccounts();
  loadBudgets();
```

- [ ] **Step 2: Add alert banners to Dashboard**

In `frontend/pages/dashboard.js`, read the file first. Find the beginning of the `dashboard` function where summary is fetched and rendered. Add an alerts fetch and banner render.

After `app.innerHTML = ...` and before the existing API calls, add:

```javascript
  // Load alerts
  async function loadAlerts() {
    const alerts = await API.get('/api/alerts').catch(() => []);
    const container = document.getElementById('alert-banners');
    if (!container) return;
    if (!alerts.length) { container.innerHTML = ''; return; }
    container.innerHTML = alerts.map(a => {
      const msg = a.overLimit
        ? `<strong>${a.accountName}</strong> exceeded monthly limit of ¥${a.monthlyLimit.toFixed(2)} — spent ¥${a.currentMonthTotal.toFixed(2)} this month.`
        : `<strong>${a.accountName}</strong> is ${a.percentOverAverage}% above the 6-month average (¥${a.sixMonthAverage.toFixed(2)}/mo) — spent ¥${a.currentMonthTotal.toFixed(2)} this month.`;
      return `<div class="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900">
        <span class="text-lg">⚠️</span><span>${msg}</span>
      </div>`;
    }).join('');
  }
```

Add an alert banners container to the dashboard HTML template. Find the dashboard `app.innerHTML` and add before the summary cards:
```html
      <div id="alert-banners" class="space-y-2 mb-6"></div>
```

Call `loadAlerts()` alongside the existing data fetch.

- [ ] **Step 3: Upload frontend**

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 sync /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1/frontend/ s3://$BUCKET/ --delete
DIST_ID=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/settings.js frontend/pages/dashboard.js
git commit -m "feat: budget alerts UI — settings management, dashboard alert banners"
```

---

## Task 7: AI Financial Advisor — Backend

**Files:**
- Create: `lambda/advisor/index.py`
- Modify: `lib/finance-stack.ts`

New Lambda that accepts a natural-language question, fetches recent financial data, and calls Bedrock Claude to answer.

- [ ] **Step 1: Create AdvisorLambda**

Create `lambda/advisor/index.py`:

```python
import boto3
import json
import os
from datetime import datetime, timezone
from collections import defaultdict

dynamodb = boto3.resource('dynamodb')
bedrock  = boto3.client('bedrock-runtime', region_name='us-east-1')

ACCOUNTS_TABLE = os.environ.get('ACCOUNTS_TABLE', 'finance-accounts')
ENTRIES_TABLE  = os.environ.get('ENTRIES_TABLE', 'finance-journal-entries')
LINES_TABLE    = os.environ.get('LINES_TABLE', 'finance-journal-lines')

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
}


def get_financial_context() -> str:
    """Build a text summary of the last 3 months of transactions for Claude."""
    accounts = {a['accountId']: a for a in dynamodb.Table(ACCOUNTS_TABLE).scan()['Items']}

    from datetime import datetime
    now = datetime.utcnow()

    # Get last 3 months of confirmed entries
    entries = dynamodb.Table(ENTRIES_TABLE).scan(
        FilterExpression='#s = :confirmed',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':confirmed': 'CONFIRMED'},
    )['Items']

    # Sort by date desc, take last 90 days
    cutoff = f"{now.year}-{now.month:02d}-01"
    # Go back 3 months
    m3 = (now.month - 3 - 1) % 12 + 1
    y3 = now.year if now.month > 3 else now.year - 1
    cutoff = f"{y3}-{m3:02d}-01"
    recent = [e for e in entries if e.get('date', '') >= cutoff]
    recent.sort(key=lambda e: e.get('date', ''), reverse=True)

    # Fetch lines for recent entries
    lines_by_entry = defaultdict(list)
    for entry in recent:
        lines = dynamodb.Table(LINES_TABLE).query(
            KeyConditionExpression='entryId = :e',
            ExpressionAttributeValues={':e': entry['entryId']},
        )['Items']
        lines_by_entry[entry['entryId']] = lines

    # Build summary text
    lines_text = []
    for entry in recent[:100]:  # limit to 100 entries for context
        eid = entry['entryId']
        for line in lines_by_entry[eid]:
            acct = accounts.get(line['accountId'], {})
            lines_text.append(
                f"{entry['date']} | {entry['description']} | {acct.get('type','?')} {acct.get('name', line['accountId'])} | "
                f"{line['direction']} ¥{line['amount']}"
            )

    # Monthly totals
    monthly = defaultdict(lambda: {'income': 0.0, 'expense': 0.0})
    for entry in recent:
        ym = entry.get('yearMonth', '')
        for line in lines_by_entry[entry['entryId']]:
            acct = accounts.get(line['accountId'], {})
            amt = float(line['amount'])
            if acct.get('type') == 'INCOME' and line['direction'] == 'CREDIT':
                monthly[ym]['income'] += amt
            elif acct.get('type') == 'EXPENSE' and line['direction'] == 'DEBIT':
                monthly[ym]['expense'] += amt

    monthly_text = '\n'.join(
        f"{ym}: Income ¥{v['income']:.2f}, Expenses ¥{v['expense']:.2f}, Net ¥{v['income']-v['expense']:.2f}"
        for ym, v in sorted(monthly.items(), reverse=True)
    )

    return f"""## Monthly Summary (last 3 months)
{monthly_text}

## Recent Transactions (last 100)
{chr(10).join(lines_text)}"""


def handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 200, 'headers': CORS, 'body': ''}

    try:
        body = json.loads(event.get('body', '{}'))
        question = body.get('question', '').strip()
        if not question:
            return {'statusCode': 400, 'headers': CORS,
                    'body': json.dumps({'error': 'question is required'})}

        context_text = get_financial_context()

        prompt = f"""You are a helpful personal finance advisor. The user has provided their financial data below.
Answer the user's question concisely and helpfully. Use specific numbers from the data when relevant.
If you can't answer from the data provided, say so clearly.

{context_text}

User question: {question}"""

        response = bedrock.invoke_model(
            modelId='us.anthropic.claude-sonnet-4-6',
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            })
        )
        result = json.loads(response['body'].read())
        answer = result['content'][0]['text']

        return {'statusCode': 200, 'headers': CORS,
                'body': json.dumps({'answer': answer})}

    except Exception as e:
        print(f"Error: {e}")
        return {'statusCode': 500, 'headers': CORS,
                'body': json.dumps({'error': str(e)})}
```

- [ ] **Step 2: Add AdvisorLambda to CDK stack**

In `lib/finance-stack.ts`, after the existing Lambda function definitions, add:

```typescript
const advisorFn = new lambda.Function(this, 'AdvisorLambda', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'index.handler',
  code: lambda.Code.fromAsset('lambda/advisor'),
  layers: [layer],
  environment: lambdaEnv,
  timeout: cdk.Duration.seconds(60),
  memorySize: 512,
});

// Grant permissions
accountsTable.grantReadData(advisorFn);
entriesTable.grantReadData(advisorFn);
linesTable.grantReadData(advisorFn);
advisorFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ['bedrock:InvokeModel'],
  resources: ['*'],
}));
advisorFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ['aws-marketplace:ViewSubscriptions', 'aws-marketplace:Subscribe', 'aws-marketplace:Unsubscribe'],
  resources: ['*'],
}));
```

Add the API route:
```typescript
const advisorIntegration = new apigw.LambdaIntegration(advisorFn);
const advisor = api.root.getResource('api')!.addResource('advisor');
advisor.addMethod('POST', advisorIntegration, { ...corsOptions });
```

- [ ] **Step 3: Deploy**

```bash
cd /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1
npx cdk deploy --require-approval never
```

- [ ] **Step 4: Commit**

```bash
git add lambda/advisor/index.py lib/finance-stack.ts
git commit -m "feat: AI Financial Advisor Lambda with Bedrock Claude context-aware Q&A"
```

---

## Task 8: AI Financial Advisor — Frontend

**Files:**
- Create: `frontend/pages/advisor.js`
- Modify: `frontend/index.html`

- [ ] **Step 1: Create advisor.js page**

Create `frontend/pages/advisor.js`:

```javascript
async function advisor(app) {
  app.innerHTML = `
    <div class="max-w-2xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-2">AI Financial Advisor</h1>
      <p class="text-sm text-gray-500 mb-6">Ask questions about your finances in plain language. Your data from the last 3 months is used as context.</p>

      <div class="card mb-4" style="min-height: 400px; max-height: 600px; overflow-y: auto;" id="chat-history">
        <div class="text-center text-gray-400 text-sm py-16" id="chat-placeholder">
          <div class="text-3xl mb-3">💬</div>
          <p>Ask anything about your finances.</p>
          <p class="mt-1">e.g. "Where did I spend the most last month?" or "How does my rent compare to my income?"</p>
        </div>
      </div>

      <div class="flex gap-3">
        <input id="advisor-input" type="text"
          class="flex-1 border rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          placeholder="Ask a question about your finances..."
          onkeydown="if(event.key==='Enter' && !event.shiftKey){ event.preventDefault(); sendQuestion(); }" />
        <button onclick="sendQuestion()" id="advisor-send"
          class="bg-blue-600 hover:bg-blue-700 text-white font-medium px-5 py-3 rounded-xl text-sm transition-colors shadow-sm">
          Ask
        </button>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <span class="text-xs text-gray-400">Try:</span>
        ${[
          "Where did I spend the most last month?",
          "What's my biggest recurring expense?",
          "How does my income compare to expenses?",
          "Am I saving money each month?",
        ].map(q => `<button onclick="document.getElementById('advisor-input').value='${q}'; sendQuestion();" class="text-xs bg-gray-100 hover:bg-gray-200 text-gray-600 px-3 py-1 rounded-full transition-colors">${q}</button>`).join('')}
      </div>
    </div>`;

  function appendMessage(role, text) {
    const history = document.getElementById('chat-history');
    const placeholder = document.getElementById('chat-placeholder');
    if (placeholder) placeholder.remove();

    const isUser = role === 'user';
    const div = document.createElement('div');
    div.className = `flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`;
    div.innerHTML = `
      <div class="max-w-lg px-4 py-3 rounded-2xl text-sm ${isUser
        ? 'bg-blue-600 text-white rounded-br-sm'
        : 'bg-gray-100 text-gray-800 rounded-bl-sm'}">
        ${text.replace(/\n/g, '<br>')}
      </div>`;
    history.appendChild(div);
    history.scrollTop = history.scrollHeight;
  }

  window.sendQuestion = async () => {
    const input = document.getElementById('advisor-input');
    const sendBtn = document.getElementById('advisor-send');
    const question = input.value.trim();
    if (!question) return;

    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;
    sendBtn.textContent = '...';

    appendMessage('user', question);

    // Thinking indicator
    const history = document.getElementById('chat-history');
    const thinking = document.createElement('div');
    thinking.id = 'thinking';
    thinking.className = 'flex justify-start mb-4';
    thinking.innerHTML = `<div class="bg-gray-100 text-gray-400 text-sm px-4 py-3 rounded-2xl rounded-bl-sm">Analyzing your finances…</div>`;
    history.appendChild(thinking);
    history.scrollTop = history.scrollHeight;

    try {
      const res = await API.post('/api/advisor', { question });
      document.getElementById('thinking')?.remove();
      appendMessage('advisor', res.answer || 'No response.');
    } catch (e) {
      document.getElementById('thinking')?.remove();
      appendMessage('advisor', `Error: ${e.message}`);
    } finally {
      input.disabled = false;
      sendBtn.disabled = false;
      sendBtn.textContent = 'Ask';
      input.focus();
    }
  };
}
```

- [ ] **Step 2: Add Advisor to nav and router in index.html**

In `frontend/index.html`, find the sidebar nav links. Add after the Reports link:
```html
<a href="#advisor" class="nav-link" data-page="advisor">Advisor</a>
```

Find the router switch/if block that maps hash to page functions. Add:
```javascript
case 'advisor': advisor(app); break;
```

Find the `<script>` tags that load page files. Add:
```html
<script src="pages/advisor.js"></script>
```

- [ ] **Step 3: Upload frontend**

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 sync /Users/jiaxin/ClaudeProjects/PersonalFinanceApp/.worktrees/phase1/frontend/ s3://$BUCKET/ --delete
DIST_ID=$(aws cloudformation describe-stacks --stack-name FinanceStack \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

- [ ] **Step 4: Commit**

```bash
git add frontend/pages/advisor.js frontend/index.html
git commit -m "feat: AI Financial Advisor chat page with quick-start question suggestions"
```

---

## Self-Review

### Spec coverage check

| Feature | Task |
|---|---|
| Custom tags — create/edit | Task 1 (backend), Task 2 (frontend) |
| Tags — filter in Transactions | Task 2 Step 4 |
| Tags — CSV export | Task 1 Step 4 |
| Foreign currency — store | Task 3 |
| Foreign currency — UI input | Task 4 |
| Foreign currency — display in list | Task 4 Step 2 |
| Budget alerts — set limits | Task 5 (backend), Task 6 Step 1 (settings UI) |
| Budget alerts — 6-month trend | Task 5 Step 3 |
| Budget alerts — dashboard | Task 6 Step 2 |
| AI Advisor — backend | Task 7 |
| AI Advisor — chat UI | Task 8 |

All spec requirements covered. ✅

### Known limitations (acceptable for Phase 2 MVP)

- Budget alerts are computed on-demand (GET /api/alerts), not pushed. No email/SMS notification — alert only shows on Dashboard when user visits.
- Foreign currency exchange rate is manually entered — no live rate API.
- AI Advisor fetches last 100 transactions for context (enough for personal use; at scale would need summarization).
- Tags on AI-parsed entries (upload) require manual edit after parsing — ParseLambda could auto-tag in Phase 3.
