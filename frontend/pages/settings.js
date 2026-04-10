function escHtmlSettings(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

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
              <button onclick="saveBudget()" class="w-full bg-[#8aaa5e] hover:bg-[#7a9a4e] text-white text-sm font-medium py-2 rounded-lg transition-colors">Save Budget</button>
            </div>
          </div>
        </div>
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

  // Budget management
  async function loadBudgetAccounts() {
    const tree = await API.get('/api/accounts').catch(() => []);
    const flat = [];
    function flattenAll(nodes) { for (const n of nodes) { flat.push(n); if (n.children) flattenAll(n.children); } }
    flattenAll(tree);
    const expenseAccts = flat.filter(a => a.type === 'EXPENSE');
    const sel = document.getElementById('budget-acct');
    if (sel) sel.innerHTML = expenseAccts.map(a => `<option value="${escHtmlSettings(a.accountId)}" data-name="${escHtmlSettings(a.name)}">${escHtmlSettings(a.name)}</option>`).join('');
  }

  async function loadBudgets() {
    const budgets = await API.get('/api/budgets').catch(() => []);
    const list = document.getElementById('budget-list');
    if (!list) return;
    if (!budgets.length) { list.innerHTML = '<p class="text-sm text-gray-400">No budgets set.</p>'; return; }
    list.innerHTML = budgets.map(b => `
      <div class="flex justify-between items-center py-2 border-b border-gray-50">
        <div>
          <span class="text-sm font-medium text-gray-700">${escHtmlSettings(b.name || b.accountId)}</span>
          <span class="text-xs text-gray-400 ml-2">¥${parseFloat(b.monthlyLimit).toFixed(2)}/month</span>
        </div>
        <button onclick="deleteBudget('${escHtmlSettings(b.accountId)}')" class="text-xs text-red-400 hover:text-red-600">Remove</button>
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
