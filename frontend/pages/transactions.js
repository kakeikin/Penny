async function transactions(app) {
  app.innerHTML = `
    <div class="max-w-4xl mx-auto">
      <div class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-bold text-gray-900">Transactions</h1>
        <a href="#new" class="btn-primary">+ New Entry</a>
      </div>
      <div class="card mb-4 flex gap-4 flex-wrap items-center">
        <span class="text-gray-500 text-sm font-medium">From</span>
        <input id="filter-start" type="date" class="border rounded-lg px-3 py-1.5 text-sm" />
        <span class="text-gray-400 text-sm">to</span>
        <input id="filter-end" type="date" class="border rounded-lg px-3 py-1.5 text-sm" />
        <button onclick="loadEntries()" class="btn-primary text-sm">Filter</button>
        <select id="filter-tag" onchange="loadEntries()" class="border rounded-lg px-3 py-1.5 text-sm text-gray-600">
          <option value="">All tags</option>
        </select>
        <a id="csv-link" class="btn-ghost text-sm ml-auto" download="transactions.csv">Export CSV</a>
      </div>
      <div id="entries-list" class="space-y-2"></div>
    </div>

    <!-- Edit Modal -->
    <div id="edit-modal" class="hidden fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50 p-4">
      <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6">
        <div class="flex items-center justify-between mb-5">
          <h2 class="text-lg font-semibold text-gray-900">Edit Transaction</h2>
          <button onclick="closeEditModal()" class="text-gray-400 hover:text-gray-600 text-xl">✕</button>
        </div>

        <!-- Type tabs -->
        <div class="flex rounded-xl overflow-hidden border border-gray-200 mb-5 text-sm font-medium">
          <button id="edit-type-expense"  onclick="setEditType('expense')"  class="flex-1 py-2 transition-colors">💸 Expense</button>
          <button id="edit-type-income"   onclick="setEditType('income')"   class="flex-1 py-2 transition-colors border-l border-r border-gray-200">💰 Income</button>
          <button id="edit-type-transfer" onclick="setEditType('transfer')" class="flex-1 py-2 transition-colors">🔄 Transfer</button>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label class="text-sm text-gray-600 font-medium">Date</label>
            <input id="edit-date" type="date" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="text-sm text-gray-600 font-medium">Description</label>
            <input id="edit-desc" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
        </div>

        <div class="mb-3">
          <label class="text-sm text-gray-600 font-medium">Amount</label>
          <div class="mt-1 relative">
            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">¥</span>
            <input id="edit-amount" type="number" step="0.01" min="0" class="w-full border rounded-lg pl-7 pr-3 py-2 text-sm" />
          </div>
        </div>

        <!-- Expense fields -->
        <div id="edit-expense-fields" class="mb-3">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-gray-600 font-medium">Category</label>
              <select id="edit-expense-cat" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">Paid with</label>
              <select id="edit-expense-pay" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
          </div>
        </div>

        <!-- Income fields -->
        <div id="edit-income-fields" class="hidden mb-3">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-gray-600 font-medium">Income Source</label>
              <select id="edit-income-src" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">Received in</label>
              <select id="edit-income-dest" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
          </div>
        </div>

        <!-- Transfer fields -->
        <div id="edit-transfer-fields" class="hidden mb-3">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-gray-600 font-medium">From Account</label>
              <select id="edit-xfer-from" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">To Account</label>
              <select id="edit-xfer-to" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
          </div>
        </div>

        <div class="mb-3">
          <label class="text-sm text-gray-600 font-medium">Tags <span class="text-gray-400 font-normal">(comma-separated)</span></label>
          <input id="edit-tags" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. food, rent" />
        </div>

        <div class="mb-5">
          <label class="text-sm text-gray-600 font-medium">Note <span class="text-gray-400 font-normal">(optional)</span></label>
          <input id="edit-note" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="Add a note..." />
        </div>

        <p id="edit-error" class="mb-3 text-sm text-red-500 hidden"></p>
        <div class="flex gap-3">
          <button onclick="closeEditModal()" class="flex-1 py-2 rounded-lg border text-sm font-medium text-gray-600 hover:bg-gray-50">Cancel</button>
          <button id="edit-save-btn" onclick="saveEdit()" class="flex-1 py-2 rounded-lg text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors">Save Changes</button>
        </div>
      </div>
    </div>`;

  // Fetch accounts for name lookup + dropdowns
  let accountMap = {};
  let allAccounts = [];
  function flatten(nodes) { for (const n of nodes) { allAccounts.push(n); if (n.children) flatten(n.children); } }

  // Populate modal dropdowns (computed lazily so allAccounts is populated first)
  function isLeaf(a) {
    const parentIds = new Set(allAccounts.map(x => x.parentId).filter(Boolean));
    return !parentIds.has(a.accountId);
  }
  function opts(list, selectedId = '') {
    return list.map(a => `<option value="${a.accountId}" ${a.accountId === selectedId ? 'selected' : ''}>${a.name}</option>`).join('');
  }
  function expenseAccts()  { return allAccounts.filter(a => a.type === 'EXPENSE' && isLeaf(a)); }
  function incomeAccts()   { return allAccounts.filter(a => a.type === 'INCOME' && isLeaf(a)); }
  function assetAccts()    { return allAccounts.filter(a => a.type === 'ASSET' && isLeaf(a)); }
  function paymentAccts()  { return allAccounts.filter(a => ['ASSET','LIABILITY'].includes(a.type) && isLeaf(a)); }

  function acctName(id) { return accountMap[id]?.name || id; }

  function summarize(entry) {
    const lines = entry.lines || [];
    const expLine = lines.find(l => accountMap[l.accountId]?.type === 'EXPENSE' && l.direction === 'DEBIT');
    if (expLine) return { type: 'expense', amount: parseFloat(expLine.amount), category: acctName(expLine.accountId) };
    const incLine = lines.find(l => accountMap[l.accountId]?.type === 'INCOME' && l.direction === 'CREDIT');
    if (incLine) return { type: 'income', amount: parseFloat(incLine.amount), category: acctName(incLine.accountId) };
    const dLine = lines.find(l => l.direction === 'DEBIT');
    if (dLine) return { type: 'transfer', amount: parseFloat(dLine.amount), category: acctName(dLine.accountId) };
    return { type: 'other', amount: 0, category: '' };
  }

  // Build a set of duplicate entry IDs by detecting same date+amount+description(30chars)
  function findDuplicateIds(entries) {
    const seen = {};
    const dupIds = new Set();
    entries.forEach(e => {
      const s = summarize(e);
      const key = `${e.date}|${s.amount.toFixed(2)}|${e.description.slice(0, 30).toLowerCase().trim()}`;
      if (seen[key]) {
        dupIds.add(e.entryId);
        dupIds.add(seen[key]);
      } else {
        seen[key] = e.entryId;
      }
    });
    return dupIds;
  }

  let _entries = [];
  let _editId = null;
  let _editType = 'expense';

  async function loadEntries() {
    const start = document.getElementById('filter-start').value;
    const end   = document.getElementById('filter-end').value;
    let url = '/api/entries';
    const q = [];
    if (start) q.push(`startDate=${start}`);
    if (end)   q.push(`endDate=${end}`);
    const tag = document.getElementById('filter-tag').value;
    if (tag) q.push(`tag=${encodeURIComponent(tag)}`);
    if (q.length) url += '?' + q.join('&');

    const csvQ = q.join('&');
    document.getElementById('csv-link').href = `${window.API_BASE}/api/export/csv${csvQ ? '?' + csvQ : ''}`;

    _entries = await API.get(url).catch(e => { alert(e.message); return []; });
    const dupIds = findDuplicateIds(_entries);
    const list = document.getElementById('entries-list');
    if (!_entries.length) {
      list.innerHTML = '<p class="text-gray-400 text-center py-10">No transactions found.</p>';
      return;
    }

    list.innerHTML = _entries.map(e => {
      const s = summarize(e);
      const amountHtml = s.type === 'expense'
        ? `<span class="font-semibold text-red-600">-¥${s.amount.toFixed(2)}</span>`
        : s.type === 'income'
        ? `<span class="font-semibold text-green-600">+¥${s.amount.toFixed(2)}</span>`
        : `<span class="font-semibold text-blue-600">¥${s.amount.toFixed(2)}</span>`;

      const typeLabel = s.type === 'expense' ? 'Expense' : s.type === 'income' ? 'Income' : 'Transfer';
      const typeBadge = s.type === 'expense' ? 'bg-red-50 text-red-600'
        : s.type === 'income' ? 'bg-green-50 text-green-600' : 'bg-blue-50 text-blue-600';

      const detailLines = (e.lines || []).map(l => `
        <div class="flex justify-between text-sm py-1 border-t border-gray-50">
          <span class="text-gray-600">${acctName(l.accountId)}</span>
          <span class="text-gray-400 text-xs self-center">${l.direction === 'DEBIT' ? '→ Out' : '← In'}</span>
          <span class="text-gray-800">¥${parseFloat(l.amount).toFixed(2)}</span>
        </div>`).join('');

      const isDup = dupIds.has(e.entryId);
      return `
        <div class="card ${isDup ? 'border-l-4 border-yellow-400' : ''}">
          ${isDup ? `<div class="flex items-center gap-2 mb-2 px-3 py-1.5 bg-yellow-50 rounded-lg text-xs text-yellow-800"><span>⚠️</span><span><strong>Possible duplicate</strong> — review and delete if needed.</span></div>` : ''}
          <div class="flex items-start justify-between cursor-pointer" onclick="toggleDetail('${e.entryId}')">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-0.5">
                <span class="text-xs font-medium px-2 py-0.5 rounded-full ${typeBadge}">${typeLabel}</span>
                <span class="text-xs text-gray-400">${s.category}</span>
              </div>
              <p class="font-medium text-gray-800 truncate">${e.description}</p>
              ${(e.tags || []).length ? `<div class="flex flex-wrap gap-1 mt-1">${(e.tags||[]).map(t => `<span class="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">#${t}</span>`).join('')}</div>` : ''}
              <p class="text-xs text-gray-400">${e.date} · ${e.source}</p>
            </div>
            <div class="flex items-center gap-3 ml-4 shrink-0">
              ${amountHtml}
              <button onclick="event.stopPropagation(); openEditModal('${e.entryId}')" class="text-xs text-blue-500 hover:text-blue-700">Edit</button>
              <button onclick="event.stopPropagation(); deleteEntry('${e.entryId}')" class="text-xs text-red-400 hover:text-red-600">Delete</button>
            </div>
          </div>
          <div id="detail-${e.entryId}" class="hidden mt-2">${detailLines}</div>
        </div>`;
    }).join('');
  }

  window.toggleDetail = (id) => {
    const el = document.getElementById(`detail-${id}`);
    if (el) el.classList.toggle('hidden');
  };

  window.deleteEntry = async (id) => {
    if (!confirm('Delete this entry?')) return;
    await API.delete(`/api/entries/${id}`);
    loadEntries();
  };

  window.openEditModal = (entryId) => {
    const entry = _entries.find(e => e.entryId === entryId);
    if (!entry) return;
    _editId = entryId;

    // Pre-fill basic fields
    document.getElementById('edit-date').value = entry.date;
    document.getElementById('edit-desc').value = entry.description;
    document.getElementById('edit-tags').value = (entry.tags || []).join(', ');
    document.getElementById('edit-note').value = '';
    document.getElementById('edit-error').classList.add('hidden');

    // Detect type and pre-fill selects
    const lines = entry.lines || [];
    const expLine = lines.find(l => accountMap[l.accountId]?.type === 'EXPENSE' && l.direction === 'DEBIT');
    const payLine = lines.find(l => (accountMap[l.accountId]?.type === 'ASSET' || accountMap[l.accountId]?.type === 'LIABILITY') && l.direction === 'CREDIT');
    const incLine = lines.find(l => accountMap[l.accountId]?.type === 'INCOME' && l.direction === 'CREDIT');
    const depLine = lines.find(l => accountMap[l.accountId]?.type === 'ASSET' && l.direction === 'DEBIT');

    let detectedType = 'expense';
    let amount = 0;

    if (expLine) {
      detectedType = 'expense';
      amount = parseFloat(expLine.amount);
      document.getElementById('edit-expense-cat').innerHTML = opts(expenseAccts(), expLine.accountId);
      document.getElementById('edit-expense-pay').innerHTML = opts(paymentAccts(), payLine?.accountId || '');
    } else if (incLine) {
      detectedType = 'income';
      amount = parseFloat(incLine.amount);
      document.getElementById('edit-income-src').innerHTML = opts(incomeAccts(), incLine.accountId);
      document.getElementById('edit-income-dest').innerHTML = opts(assetAccts(), depLine?.accountId || '');
    } else {
      detectedType = 'transfer';
      const debitLine = lines.find(l => l.direction === 'DEBIT');
      const creditLine = lines.find(l => l.direction === 'CREDIT');
      amount = debitLine ? parseFloat(debitLine.amount) : 0;
      document.getElementById('edit-xfer-from').innerHTML = opts(paymentAccts(), creditLine?.accountId || '');
      document.getElementById('edit-xfer-to').innerHTML = opts(paymentAccts(), debitLine?.accountId || '');
    }

    // If dropdowns not yet populated for non-detected types, fill them
    if (detectedType !== 'expense') {
      document.getElementById('edit-expense-cat').innerHTML = opts(expenseAccts());
      document.getElementById('edit-expense-pay').innerHTML = opts(paymentAccts());
    }
    if (detectedType !== 'income') {
      document.getElementById('edit-income-src').innerHTML = opts(incomeAccts());
      document.getElementById('edit-income-dest').innerHTML = opts(assetAccts());
    }
    if (detectedType !== 'transfer') {
      document.getElementById('edit-xfer-from').innerHTML = opts(paymentAccts());
      document.getElementById('edit-xfer-to').innerHTML = opts(paymentAccts());
    }

    document.getElementById('edit-amount').value = amount.toFixed(2);
    setEditType(detectedType);
    document.getElementById('edit-modal').classList.remove('hidden');
  };

  window.closeEditModal = () => {
    document.getElementById('edit-modal').classList.add('hidden');
    _editId = null;
  };

  window.setEditType = (type) => {
    _editType = type;
    const styles = {
      expense:  { active: 'bg-red-50 text-red-700',    btn: 'bg-red-600 hover:bg-red-700' },
      income:   { active: 'bg-green-50 text-green-700', btn: 'bg-green-600 hover:bg-green-700' },
      transfer: { active: 'bg-blue-50 text-blue-700',   btn: 'bg-blue-600 hover:bg-blue-700' },
    };
    ['expense', 'income', 'transfer'].forEach(t => {
      const btn = document.getElementById(`edit-type-${t}`);
      document.getElementById(`edit-${t}-fields`).classList.toggle('hidden', t !== type);
      btn.className = t === type
        ? `flex-1 py-2 transition-colors ${styles[t].active} font-semibold`
        : 'flex-1 py-2 transition-colors text-gray-500 hover:bg-gray-50';
    });
    const saveBtn = document.getElementById('edit-save-btn');
    if (saveBtn) saveBtn.className = `flex-1 py-2 rounded-lg text-sm font-semibold text-white transition-colors ${styles[type].btn}`;
  };

  window.saveEdit = async () => {
    const amount = parseFloat(document.getElementById('edit-amount').value);
    const date   = document.getElementById('edit-date').value;
    const description = document.getElementById('edit-desc').value.trim();
    const note   = document.getElementById('edit-note').value;
    const tags = document.getElementById('edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);
    const err    = document.getElementById('edit-error');
    err.classList.add('hidden');

    if (!date || !description) { err.textContent = 'Date and description are required.'; err.classList.remove('hidden'); return; }
    if (!amount || amount <= 0) { err.textContent = 'Please enter a valid amount.'; err.classList.remove('hidden'); return; }

    let lines = [];
    if (_editType === 'expense') {
      lines = [
        { direction: 'DEBIT',  accountId: document.getElementById('edit-expense-cat').value, amount, note },
        { direction: 'CREDIT', accountId: document.getElementById('edit-expense-pay').value, amount, note: '' },
      ];
    } else if (_editType === 'income') {
      lines = [
        { direction: 'DEBIT',  accountId: document.getElementById('edit-income-dest').value, amount, note },
        { direction: 'CREDIT', accountId: document.getElementById('edit-income-src').value,  amount, note: '' },
      ];
    } else {
      const fromId = document.getElementById('edit-xfer-from').value;
      const toId   = document.getElementById('edit-xfer-to').value;
      if (fromId === toId) { err.textContent = 'From and To accounts must be different.'; err.classList.remove('hidden'); return; }
      lines = [
        { direction: 'DEBIT',  accountId: toId,   amount, note },
        { direction: 'CREDIT', accountId: fromId,  amount, note: '' },
      ];
    }

    try {
      await API.put(`/api/entries/${_editId}`, { date, description, lines, tags });
      closeEditModal();
      loadEntries();
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove('hidden');
    }
  };

  // Load accounts and entries in parallel, render once both are ready
  const [accountTree] = await Promise.all([
    API.get('/api/accounts').catch(() => []),
  ]);
  try {
    flatten(accountTree);
    accountMap = Object.fromEntries(allAccounts.map(a => [a.accountId, a]));
  } catch(e) {}
  loadEntries();
  API.get('/api/tags').then(tags => {
    const sel = document.getElementById('filter-tag');
    if (sel) tags.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t; opt.textContent = '#' + t;
      sel.appendChild(opt);
    });
  }).catch(() => {});
}
