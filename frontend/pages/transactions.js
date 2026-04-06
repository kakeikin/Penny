async function transactions(app) {
  app.innerHTML = `
    <div class="max-w-5xl mx-auto">
      <div class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-bold text-gray-900">Transactions</h1>
        <a href="#new" class="btn-primary">+ New Entry</a>
      </div>
      <div class="card mb-4 flex gap-4 flex-wrap">
        <input id="filter-start" type="date" class="border rounded-lg px-3 py-1.5 text-sm" />
        <input id="filter-end"   type="date" class="border rounded-lg px-3 py-1.5 text-sm" />
        <button onclick="loadEntries()" class="btn-primary text-sm">Filter</button>
        <a id="csv-link" class="btn-ghost text-sm ml-auto" download="transactions.csv">Export CSV</a>
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
    if (!entries.length) {
      list.innerHTML = '<p class="text-gray-400 text-center py-10">No transactions found.</p>';
      return;
    }

    list.innerHTML = entries.map(e => `
      <div class="card">
        <div class="flex justify-between items-start">
          <div>
            <p class="font-medium text-gray-800">${e.date} — ${e.description}</p>
            <span class="text-xs text-gray-400">${e.source}</span>
          </div>
          <button onclick="deleteEntry('${e.entryId}')" class="text-xs text-red-500 hover:underline">Delete</button>
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
