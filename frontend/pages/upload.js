async function upload(app) {
  app.innerHTML = `
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Upload Statement or Receipt</h1>

      <div id="drop-zone" class="card border-2 border-dashed border-gray-300 text-center cursor-pointer hover:border-blue-400 transition mb-6">
        <div class="py-10 flex flex-col items-center">
          <svg class="w-12 h-12 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <p class="text-gray-600 font-medium mb-1">Drag & drop a PDF or image here</p>
          <p class="text-gray-400 text-sm mb-5">Supports PDF, JPG, PNG</p>
          <label class="bg-[#8aaa5e] hover:bg-[#7a9a4e] text-white font-medium px-6 py-2.5 rounded-lg cursor-pointer transition-colors text-sm shadow-sm">
            Choose File
            <input type="file" id="file-input" accept=".pdf,.jpg,.jpeg,.png" class="hidden" />
          </label>
        </div>
      </div>

      <div id="status" class="hidden card mb-6"></div>

      <div id="pending-section" class="hidden">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-gray-800">Review Parsed Entries</h2>
          <button id="confirm-all" class="bg-green-600 hover:bg-green-700 text-white font-semibold px-5 py-2 rounded-lg text-sm transition-colors shadow-sm">Confirm All ✓</button>
        </div>
        <div id="pending-list" class="space-y-3"></div>
      </div>
    </div>

    <!-- Edit Modal -->
    <div id="upload-edit-modal" class="hidden fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50 p-4">
      <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6">
        <div class="flex items-center justify-between mb-5">
          <h2 class="text-lg font-semibold text-gray-900">Edit Entry</h2>
          <button onclick="closeUploadEditModal()" class="text-gray-400 hover:text-gray-600 text-xl">✕</button>
        </div>

        <!-- Type tabs -->
        <div class="flex rounded-xl overflow-hidden border border-gray-200 mb-5 text-sm font-medium">
          <button id="ue-type-expense"  onclick="setUploadEditType('expense')"  class="flex-1 py-2 transition-colors">💸 Expense</button>
          <button id="ue-type-income"   onclick="setUploadEditType('income')"   class="flex-1 py-2 transition-colors border-l border-r border-gray-200">💰 Income</button>
          <button id="ue-type-transfer" onclick="setUploadEditType('transfer')" class="flex-1 py-2 transition-colors">🔄 Transfer</button>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label class="text-sm text-gray-600 font-medium">Date</label>
            <input id="ue-date" type="date" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label class="text-sm text-gray-600 font-medium">Description</label>
            <input id="ue-desc" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
        </div>

        <div class="mb-3">
          <label class="text-sm text-gray-600 font-medium">Amount</label>
          <div class="mt-1 relative">
            <span class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">¥</span>
            <input id="ue-amount" type="number" step="0.01" min="0" class="w-full border rounded-lg pl-7 pr-3 py-2 text-sm" />
          </div>
        </div>

        <!-- Foreign Currency -->
        <div class="mb-3">
          <label class="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
            <input type="checkbox" id="ue-foreign" onchange="toggleUploadForeignCurrency()" class="rounded" />
            <span>Paid in foreign currency</span>
          </label>
          <div id="ue-foreign-fields" class="hidden mt-2 grid grid-cols-3 gap-2">
            <div>
              <label class="text-xs text-gray-500">Original Amount</label>
              <input id="ue-orig-amount" type="number" step="0.01" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="0.00" />
            </div>
            <div>
              <label class="text-xs text-gray-500">Currency</label>
              <select id="ue-orig-currency" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm">
                <option>USD</option><option>EUR</option><option>GBP</option><option>JPY</option><option>HKD</option><option>CAD</option><option>AUD</option>
              </select>
            </div>
            <div>
              <label class="text-xs text-gray-500">Exchange Rate</label>
              <input id="ue-rate" type="number" step="0.0001" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="7.25" />
            </div>
          </div>
        </div>

        <div id="ue-expense-fields" class="mb-3">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-gray-600 font-medium">Category</label>
              <select id="ue-expense-cat" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">Paid with</label>
              <select id="ue-expense-pay" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
          </div>
        </div>

        <div id="ue-income-fields" class="hidden mb-3">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-gray-600 font-medium">Income Source</label>
              <select id="ue-income-src" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">Received in</label>
              <select id="ue-income-dest" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
          </div>
        </div>

        <div id="ue-transfer-fields" class="hidden mb-3">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-sm text-gray-600 font-medium">From Account</label>
              <select id="ue-xfer-from" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
            <div>
              <label class="text-sm text-gray-600 font-medium">To Account</label>
              <select id="ue-xfer-to" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm"></select>
            </div>
          </div>
        </div>

        <div class="mb-3">
          <label class="text-sm text-gray-600 font-medium">Tags <span class="text-gray-400 font-normal">(comma-separated)</span></label>
          <input id="ue-tags" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. food, rent" />
        </div>

        <div class="mb-5">
          <label class="text-sm text-gray-600 font-medium">Note <span class="text-gray-400 font-normal">(optional)</span></label>
          <input id="ue-note" type="text" class="mt-1 w-full border rounded-lg px-3 py-2 text-sm" placeholder="Add a note..." />
        </div>

        <p id="ue-error" class="mb-3 text-sm text-red-500 hidden"></p>
        <div class="flex gap-3">
          <button onclick="closeUploadEditModal()" class="flex-1 py-2 rounded-lg border text-sm font-medium text-gray-600 hover:bg-gray-50">Cancel</button>
          <button id="ue-save-btn" onclick="saveUploadEdit()" class="flex-1 py-2 rounded-lg text-sm font-semibold text-white bg-[#8aaa5e] hover:bg-[#7a9a4e] transition-colors">Save Changes</button>
        </div>
      </div>
    </div>`;

  const dropZone  = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const status    = document.getElementById('status');

  // Load accounts
  let accountMap = {};
  let allAccounts = [];
  try {
    const tree = await API.get('/api/accounts');
    function flatten(nodes) { for (const n of nodes) { allAccounts.push(n); if (n.children) flatten(n.children); } }
    flatten(tree);
    accountMap = Object.fromEntries(allAccounts.map(a => [a.accountId, a]));
  } catch (e) {}

  const parentIds = new Set(allAccounts.map(a => a.parentId).filter(Boolean));
  function isLeaf(a) { return !parentIds.has(a.accountId); }
  function opts(list, selectedId = '') {
    return list.map(a => `<option value="${a.accountId}" ${a.accountId === selectedId ? 'selected' : ''}>${a.name}</option>`).join('');
  }
  const expenseAccts = allAccounts.filter(a => a.type === 'EXPENSE' && isLeaf(a));
  const incomeAccts  = allAccounts.filter(a => a.type === 'INCOME' && isLeaf(a));
  const assetAccts   = allAccounts.filter(a => a.type === 'ASSET' && isLeaf(a));
  const paymentAccts = allAccounts.filter(a => ['ASSET','LIABILITY'].includes(a.type) && isLeaf(a));
  function acctName(id) { return accountMap[id]?.name || id; }

  function showStatus(msg, color = 'text-gray-700') {
    status.className = `card mb-6 ${color}`;
    status.textContent = msg;
    status.classList.remove('hidden');
  }

  async function handleFile(file) {
    showStatus(`Uploading ${file.name}…`);
    try {
      await API.uploadFile(file.name, file.type, file);
      showStatus('File uploaded. Parsing with Claude AI… this may take 20–30 seconds.', 'text-blue-600');
      await new Promise(r => setTimeout(r, 20000));
      await loadPending();
      showStatus('Parsing complete. Review entries below.', 'text-green-600');
    } catch (e) {
      showStatus('Error: ' + e.message, 'text-red-600');
    }
  }

  async function loadPending() {
    const pending = await API.get('/api/entries?status=PENDING').catch(() => []);
    if (!pending.length) return;

    document.getElementById('pending-section').classList.remove('hidden');
    const list = document.getElementById('pending-list');
    list.innerHTML = pending.map(e => `
      <div class="card ${e.status === 'DUPLICATE_SUSPECT' ? 'border-l-4 border-yellow-400' : ''}" id="entry-${e.entryId}">
        ${e.status === 'DUPLICATE_SUSPECT' ? `
        <div class="flex items-center gap-2 mb-3 px-3 py-2 bg-yellow-50 rounded-lg text-sm text-yellow-800">
          <span>⚠️</span>
          <span><strong>Possible duplicate</strong> — a similar transaction already exists. Review before confirming.</span>
        </div>` : ''}
        <div class="flex justify-between items-start mb-3">
          <div>
            <p class="font-medium text-gray-800">${e.date} — ${e.description}</p>
            <span class="text-xs text-gray-400 uppercase tracking-wide">${e.source}</span>
          </div>
          <div class="flex gap-2">
            <button onclick="openUploadEditModal('${e.entryId}')" class="bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium px-3 py-1.5 rounded-lg text-sm transition-colors">Edit</button>
            <button onclick="confirmEntry('${e.entryId}')" class="bg-[#8aaa5e] hover:bg-[#7a9a4e] text-white font-semibold px-4 py-1.5 rounded-lg text-sm transition-colors shadow-sm">Confirm ✓</button>
          </div>
        </div>
        <table class="w-full text-sm">
          <thead><tr class="text-gray-400 text-xs uppercase">
            <th class="text-left pb-1">Account</th>
            <th class="text-left pb-1">Direction</th>
            <th class="text-right pb-1">Amount</th>
            <th class="text-left pb-1">Note</th>
          </tr></thead>
          <tbody id="lines-${e.entryId}">
            ${(e.lines || []).map(l => `
            <tr class="border-t border-gray-50">
              <td class="py-1 text-gray-700">${acctName(l.accountId)}</td>
              <td class="py-1"><span class="${l.direction === 'DEBIT' ? 'badge-debit' : 'badge-credit'}">${l.direction}</span></td>
              <td class="py-1 text-right">¥${parseFloat(l.amount).toFixed(2)}</td>
              <td class="py-1 text-gray-400 text-xs">${l.note || ''}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`).join('');

    // Store pending entries for edit modal
    window._pendingEntries = pending;
  }

  window.confirmEntry = async (id) => {
    await API.put(`/api/entries/${id}/confirm`);
    document.getElementById(`entry-${id}`)?.remove();
    const remaining = document.querySelectorAll('[id^="entry-"]');
    if (!remaining.length) document.getElementById('pending-section').classList.add('hidden');
  };

  document.getElementById('confirm-all').onclick = async () => {
    const cards = document.querySelectorAll('[id^="entry-"]');
    for (const card of cards) {
      const id = card.id.replace('entry-', '');
      await API.put(`/api/entries/${id}/confirm`);
      card.remove();
    }
    document.getElementById('pending-section').classList.add('hidden');
    showStatus('All entries confirmed.', 'text-green-600');
  };

  // Edit modal logic
  let _ueEditId = null;
  let _ueEditType = 'expense';

  window.openUploadEditModal = (entryId) => {
    const entry = (window._pendingEntries || []).find(e => e.entryId === entryId);
    if (!entry) return;
    _ueEditId = entryId;

    document.getElementById('ue-date').value = entry.date;
    document.getElementById('ue-desc').value = entry.description;
    document.getElementById('ue-tags').value = (entry.tags || []).join(', ');
    document.getElementById('ue-note').value = '';
    document.getElementById('ue-error').classList.add('hidden');

    const lines = entry.lines || [];
    const expLine  = lines.find(l => accountMap[l.accountId]?.type === 'EXPENSE' && l.direction === 'DEBIT');
    const payLine  = lines.find(l => ['ASSET','LIABILITY'].includes(accountMap[l.accountId]?.type) && l.direction === 'CREDIT');
    const incLine  = lines.find(l => accountMap[l.accountId]?.type === 'INCOME' && l.direction === 'CREDIT');
    const depLine  = lines.find(l => accountMap[l.accountId]?.type === 'ASSET' && l.direction === 'DEBIT');

    let detectedType = 'expense';
    let amount = 0;

    if (expLine) {
      detectedType = 'expense'; amount = parseFloat(expLine.amount);
    } else if (incLine) {
      detectedType = 'income'; amount = parseFloat(incLine.amount);
    } else {
      detectedType = 'transfer';
      const dl = lines.find(l => l.direction === 'DEBIT');
      amount = dl ? parseFloat(dl.amount) : 0;
    }

    document.getElementById('ue-expense-cat').innerHTML  = opts(expenseAccts, expLine?.accountId || '');
    document.getElementById('ue-expense-pay').innerHTML  = opts(paymentAccts, payLine?.accountId || '');
    document.getElementById('ue-income-src').innerHTML   = opts(incomeAccts,  incLine?.accountId || '');
    document.getElementById('ue-income-dest').innerHTML  = opts(assetAccts,   depLine?.accountId || '');
    const creditLine = lines.find(l => l.direction === 'CREDIT');
    const debitLine  = lines.find(l => l.direction === 'DEBIT');
    document.getElementById('ue-xfer-from').innerHTML = opts(paymentAccts, creditLine?.accountId || '');
    document.getElementById('ue-xfer-to').innerHTML   = opts(paymentAccts, debitLine?.accountId || '');

    document.getElementById('ue-amount').value = amount.toFixed(2);
    setUploadEditType(detectedType);

    // Pre-fill foreign currency fields
    const fxLine = (entry.lines || []).find(l => l.direction === 'DEBIT' && l.originalCurrency);
    if (fxLine) {
      document.getElementById('ue-foreign').checked = true;
      document.getElementById('ue-foreign-fields').classList.remove('hidden');
      document.getElementById('ue-orig-amount').value = fxLine.originalAmount || '';
      document.getElementById('ue-orig-currency').value = fxLine.originalCurrency || 'USD';
      document.getElementById('ue-rate').value = fxLine.exchangeRate || '';
    } else {
      document.getElementById('ue-foreign').checked = false;
      document.getElementById('ue-foreign-fields').classList.add('hidden');
    }

    document.getElementById('upload-edit-modal').classList.remove('hidden');
  };

  window.closeUploadEditModal = () => {
    document.getElementById('upload-edit-modal').classList.add('hidden');
    _ueEditId = null;
  };

  window.setUploadEditType = (type) => {
    _ueEditType = type;
    const styles = {
      expense:  { active: 'bg-red-50 text-red-700',    btn: 'bg-red-600 hover:bg-red-700' },
      income:   { active: 'bg-green-50 text-green-700', btn: 'bg-green-600 hover:bg-green-700' },
      transfer: { active: 'bg-blue-50 text-blue-700',   btn: 'bg-blue-600 hover:bg-blue-700' },
    };
    ['expense', 'income', 'transfer'].forEach(t => {
      document.getElementById(`ue-type-${t}`).className = t === type
        ? `flex-1 py-2 transition-colors ${styles[t].active} font-semibold`
        : 'flex-1 py-2 transition-colors text-gray-500 hover:bg-gray-50';
      document.getElementById(`ue-${t}-fields`).classList.toggle('hidden', t !== type);
    });
    const btn = document.getElementById('ue-save-btn');
    if (btn) btn.className = `flex-1 py-2 rounded-lg text-sm font-semibold text-white transition-colors ${styles[type].btn}`;
  };

  window.toggleUploadForeignCurrency = function() {
    const checked = document.getElementById('ue-foreign').checked;
    document.getElementById('ue-foreign-fields').classList.toggle('hidden', !checked);
  };

  window.saveUploadEdit = async () => {
    const amount = parseFloat(document.getElementById('ue-amount').value);
    const date   = document.getElementById('ue-date').value;
    const description = document.getElementById('ue-desc').value.trim();
    const note   = document.getElementById('ue-note').value;
    const tags = document.getElementById('ue-tags').value.split(',').map(t => t.trim()).filter(Boolean);
    const err    = document.getElementById('ue-error');
    err.classList.add('hidden');

    if (!date || !description) { err.textContent = 'Date and description are required.'; err.classList.remove('hidden'); return; }
    if (!amount || amount <= 0) { err.textContent = 'Please enter a valid amount.'; err.classList.remove('hidden'); return; }

    let lines = [];
    if (_ueEditType === 'expense') {
      lines = [
        { direction: 'DEBIT',  accountId: document.getElementById('ue-expense-cat').value, amount, note },
        { direction: 'CREDIT', accountId: document.getElementById('ue-expense-pay').value, amount, note: '' },
      ];
    } else if (_ueEditType === 'income') {
      lines = [
        { direction: 'DEBIT',  accountId: document.getElementById('ue-income-dest').value, amount, note },
        { direction: 'CREDIT', accountId: document.getElementById('ue-income-src').value,  amount, note: '' },
      ];
    } else {
      const fromId = document.getElementById('ue-xfer-from').value;
      const toId   = document.getElementById('ue-xfer-to').value;
      if (fromId === toId) { err.textContent = 'From and To must be different.'; err.classList.remove('hidden'); return; }
      lines = [
        { direction: 'DEBIT',  accountId: toId,   amount, note },
        { direction: 'CREDIT', accountId: fromId,  amount, note: '' },
      ];
    }

    const isForeign = document.getElementById('ue-foreign')?.checked;
    if (isForeign && lines.length > 0) {
      const debitIdx = lines.findIndex(l => l.direction === 'DEBIT');
      if (debitIdx >= 0) {
        lines[debitIdx].originalAmount   = parseFloat(document.getElementById('ue-orig-amount').value) || null;
        lines[debitIdx].originalCurrency = document.getElementById('ue-orig-currency').value;
        lines[debitIdx].exchangeRate     = parseFloat(document.getElementById('ue-rate').value) || 1;
      }
    }

    try {
      await API.put(`/api/entries/${_ueEditId}`, { date, description, lines, tags });
      closeUploadEditModal();
      // Refresh pending list
      const updated = await API.get('/api/entries?status=PENDING').catch(() => []);
      window._pendingEntries = updated;
      // Re-render the edited card
      const entry = updated.find(e => e.entryId === _ueEditId) || { entryId: _ueEditId, date, description, source: 'MANUAL', lines };
      const card = document.getElementById(`entry-${_ueEditId}`);
      if (card) {
        // Just reload all pending to be safe
        const pending = await API.get('/api/entries?status=PENDING').catch(() => []);
        window._pendingEntries = pending;
        const list = document.getElementById('pending-list');
        // Re-render by reloading
        await loadPending();
      }
    } catch (e) {
      document.getElementById('ue-error').textContent = e.message;
      document.getElementById('ue-error').classList.remove('hidden');
    }
  };

  dropZone.ondragover  = e => { e.preventDefault(); dropZone.classList.add('border-blue-400'); };
  dropZone.ondragleave = () => dropZone.classList.remove('border-blue-400');
  dropZone.ondrop = e => { e.preventDefault(); handleFile(e.dataTransfer.files[0]); };
  fileInput.onchange = () => fileInput.files[0] && handleFile(fileInput.files[0]);

  await loadPending();
}
