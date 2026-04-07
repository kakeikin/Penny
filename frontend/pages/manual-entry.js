let _accounts = [];
let _entryType = 'expense';

async function manualEntry(app) {
  const tree = await API.get('/api/accounts').catch(() => []);
  _accounts = [];
  function flatten(nodes) { for (const n of nodes) { _accounts.push(n); if (n.children) flatten(n.children); } }
  flatten(tree);

  const parentIds = new Set(_accounts.map(a => a.parentId).filter(Boolean));
  function isLeaf(a) { return !parentIds.has(a.accountId); }

  const expenseAccounts  = _accounts.filter(a => a.type === 'EXPENSE' && isLeaf(a));
  const incomeAccounts   = _accounts.filter(a => a.type === 'INCOME' && isLeaf(a));
  const assetAccounts    = _accounts.filter(a => a.type === 'ASSET' && isLeaf(a));
  const paymentAccounts  = _accounts.filter(a => ['ASSET', 'LIABILITY'].includes(a.type) && isLeaf(a));

  function opts(list) {
    return list.map(a => `<option value="${a.accountId}">${a.name}</option>`).join('');
  }

  app.innerHTML = `
    <div class="max-w-2xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Manual Entry</h1>
      <div class="card">

        <!-- Transaction type tabs -->
        <div class="flex rounded-xl overflow-hidden border border-gray-200 mb-6 text-sm font-medium">
          <button id="type-expense"  onclick="setEntryType('expense')"  class="flex-1 py-2.5 transition-colors">💸 Expense</button>
          <button id="type-income"   onclick="setEntryType('income')"   class="flex-1 py-2.5 transition-colors border-l border-r border-gray-200">💰 Income</button>
          <button id="type-transfer" onclick="setEntryType('transfer')" class="flex-1 py-2.5 transition-colors">🔄 Transfer</button>
        </div>

        <!-- Date & Description -->
        <div class="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label class="text-sm text-gray-600 font-medium">Date</label>
            <input id="me-date" type="date" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              value="${new Date().toISOString().slice(0,10)}" />
          </div>
          <div>
            <label class="text-sm text-gray-600 font-medium">Description</label>
            <input id="me-desc" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              placeholder="e.g. Coffee at Starbucks" />
          </div>
        </div>

        <!-- Amount -->
        <div class="mb-4">
          <label class="text-sm text-gray-600 font-medium">Amount</label>
          <div class="mt-1 relative">
            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">¥</span>
            <input id="me-amount" type="number" step="0.01" min="0"
              class="w-full border rounded-lg pl-7 pr-3 py-2 text-sm" placeholder="0.00" />
          </div>
        </div>

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

        <!-- Expense fields -->
        <div id="expense-fields">
          <div class="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label class="text-sm text-gray-600 font-medium">Category</label>
              <select id="me-expense-cat" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                ${opts(expenseAccounts)}
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">Paid with</label>
              <select id="me-expense-pay" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                ${opts(paymentAccounts)}
              </select>
            </div>
          </div>
        </div>

        <!-- Income fields -->
        <div id="income-fields" class="hidden">
          <div class="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label class="text-sm text-gray-600 font-medium">Income Source</label>
              <select id="me-income-src" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                ${opts(incomeAccounts)}
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">Received in</label>
              <select id="me-income-dest" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                ${opts(assetAccounts)}
              </select>
            </div>
          </div>
        </div>

        <!-- Transfer fields -->
        <div id="transfer-fields" class="hidden">
          <div class="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label class="text-sm text-gray-600 font-medium">From Account</label>
              <select id="me-xfer-from" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                ${opts(paymentAccounts)}
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">To Account</label>
              <select id="me-xfer-to" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                ${opts(paymentAccounts)}
              </select>
            </div>
          </div>
        </div>

        <!-- Tags -->
        <div class="mb-4">
          <label class="text-sm text-gray-600 font-medium">Tags <span class="text-gray-400 font-normal">(optional, comma-separated)</span></label>
          <input id="me-tags" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"
            placeholder="e.g. food, subscription, work" />
        </div>

        <!-- Note -->
        <div class="mb-6">
          <label class="text-sm text-gray-600 font-medium">Note <span class="text-gray-400 font-normal">(optional)</span></label>
          <input id="me-note" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"
            placeholder="Add a note..." />
        </div>

        <button onclick="submitEntry()" id="submit-btn"
          class="w-full py-2.5 rounded-lg text-sm font-semibold text-white transition-colors shadow-sm bg-[#8aaa5e] hover:bg-[#7a9a4e]">
          Save Entry
        </button>
        <p id="me-error" class="mt-2 text-sm text-red-500 hidden"></p>
      </div>
    </div>`;

  setEntryType('expense');
}

window.setEntryType = function(type) {
  _entryType = type;
  const styles = {
    expense:  { active: 'bg-red-50 text-red-700',    btn: 'bg-[#8aaa5e] hover:bg-[#7a9a4e]' },
    income:   { active: 'bg-green-50 text-green-700', btn: 'bg-[#8aaa5e] hover:bg-[#7a9a4e]' },
    transfer: { active: 'bg-blue-50 text-blue-700', btn: 'bg-blue-600 hover:bg-blue-700' },
  };
  ['expense', 'income', 'transfer'].forEach(t => {
    const btn = document.getElementById(`type-${t}`);
    const fields = document.getElementById(`${t}-fields`);
    if (t === type) {
      btn.className = `flex-1 py-2.5 transition-colors ${styles[t].active} font-semibold`;
      fields?.classList.remove('hidden');
    } else {
      btn.className = 'flex-1 py-2.5 transition-colors text-gray-500 hover:bg-gray-50';
      fields?.classList.add('hidden');
    }
  });
  const submitBtn = document.getElementById('submit-btn');
  if (submitBtn) submitBtn.className = `w-full py-2.5 rounded-lg text-sm font-semibold text-white transition-colors shadow-sm ${styles[type].btn}`;
};

window.toggleForeignCurrency = function() {
  const checked = document.getElementById('me-foreign').checked;
  document.getElementById('me-foreign-fields').classList.toggle('hidden', !checked);
};

window.submitEntry = async function() {
  const amount = parseFloat(document.getElementById('me-amount').value);
  const date   = document.getElementById('me-date').value;
  const description = document.getElementById('me-desc').value.trim();
  const note   = document.getElementById('me-note').value;
  const tags = document.getElementById('me-tags').value.split(',').map(t => t.trim()).filter(Boolean);
  const err    = document.getElementById('me-error');

  err.classList.add('hidden');
  if (!date || !description) { err.textContent = 'Date and description are required.'; err.classList.remove('hidden'); return; }
  if (!amount || amount <= 0) { err.textContent = 'Please enter a valid amount.'; err.classList.remove('hidden'); return; }

  let lines = [];
  if (_entryType === 'expense') {
    const catId = document.getElementById('me-expense-cat').value;
    const payId = document.getElementById('me-expense-pay').value;
    lines = [
      { direction: 'DEBIT',  accountId: catId, amount, note },
      { direction: 'CREDIT', accountId: payId, amount, note: '' },
    ];
  } else if (_entryType === 'income') {
    const srcId  = document.getElementById('me-income-src').value;
    const destId = document.getElementById('me-income-dest').value;
    lines = [
      { direction: 'DEBIT',  accountId: destId, amount, note },
      { direction: 'CREDIT', accountId: srcId,  amount, note: '' },
    ];
  } else {
    const fromId = document.getElementById('me-xfer-from').value;
    const toId   = document.getElementById('me-xfer-to').value;
    if (fromId === toId) { err.textContent = 'From and To accounts must be different.'; err.classList.remove('hidden'); return; }
    lines = [
      { direction: 'DEBIT',  accountId: toId,   amount, note },
      { direction: 'CREDIT', accountId: fromId,  amount, note: '' },
    ];
  }

  const isForeign = document.getElementById('me-foreign')?.checked;
  if (isForeign && lines.length > 0) {
    lines[0].originalAmount   = parseFloat(document.getElementById('me-orig-amount').value) || null;
    lines[0].originalCurrency = document.getElementById('me-orig-currency').value;
    lines[0].exchangeRate     = parseFloat(document.getElementById('me-rate').value) || 1;
  }

  try {
    await API.post('/api/entries', { date, description, lines, tags });
    location.hash = '#transactions';
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
};
