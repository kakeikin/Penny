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
        <a id="csv-link" class="btn-ghost text-sm ml-auto" download="transactions.csv">Export CSV</a>
      </div>
      <div id="entries-list" class="space-y-2"></div>
    </div>`;

  // Fetch accounts for name lookup
  let accountMap = {};
  try {
    const tree = await API.get('/api/accounts');
    const flat = [];
    function flatten(nodes) { for (const n of nodes) { flat.push(n); if (n.children) flatten(n.children); } }
    flatten(tree);
    accountMap = Object.fromEntries(flat.map(a => [a.accountId, a]));
  } catch (e) { /* continue without names */ }

  function acctName(id) { return accountMap[id]?.name || id; }

  // Determine the "primary" line for display: expense > income > first debit
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
    if (!entries.length) {
      list.innerHTML = '<p class="text-gray-400 text-center py-10">No transactions found.</p>';
      return;
    }

    list.innerHTML = entries.map(e => {
      const s = summarize(e);
      const amountHtml = s.type === 'expense'
        ? `<span class="font-semibold text-red-600">-¥${s.amount.toFixed(2)}</span>`
        : s.type === 'income'
        ? `<span class="font-semibold text-green-600">+¥${s.amount.toFixed(2)}</span>`
        : `<span class="font-semibold text-blue-600">¥${s.amount.toFixed(2)}</span>`;

      const typeLabel = s.type === 'expense' ? 'Expense' : s.type === 'income' ? 'Income' : 'Transfer';
      const typeBadge = s.type === 'expense'
        ? 'bg-red-50 text-red-600'
        : s.type === 'income'
        ? 'bg-green-50 text-green-600'
        : 'bg-blue-50 text-blue-600';

      // Detail lines — show account name + friendly direction
      const detailLines = (e.lines || []).map(l => {
        const acct = accountMap[l.accountId] || {};
        const isOut = l.direction === 'DEBIT' && ['EXPENSE','ASSET'].includes(acct.type)
                   || l.direction === 'CREDIT' && ['LIABILITY'].includes(acct.type);
        const dirLabel = l.direction === 'DEBIT' ? '→ Out' : '← In';
        return `
          <div class="flex justify-between text-sm py-1 border-t border-gray-50">
            <span class="text-gray-600">${acctName(l.accountId)}</span>
            <span class="text-gray-400 text-xs self-center">${dirLabel}</span>
            <span class="text-gray-800">¥${parseFloat(l.amount).toFixed(2)}</span>
          </div>`;
      }).join('');

      return `
        <div class="card">
          <div class="flex items-start justify-between cursor-pointer" onclick="toggleDetail('${e.entryId}')">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-0.5">
                <span class="text-xs font-medium px-2 py-0.5 rounded-full ${typeBadge}">${typeLabel}</span>
                <span class="text-xs text-gray-400">${s.category}</span>
              </div>
              <p class="font-medium text-gray-800 truncate">${e.description}</p>
              <p class="text-xs text-gray-400">${e.date} · ${e.source}</p>
            </div>
            <div class="flex items-center gap-4 ml-4 shrink-0">
              ${amountHtml}
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

  loadEntries();
}
