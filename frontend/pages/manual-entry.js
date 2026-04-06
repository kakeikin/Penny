let _accounts = [];

async function manualEntry(app) {
  _accounts = await API.get('/api/accounts').then(flattenTree).catch(() => []);

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
  return _accounts.map(a => `<option value="${a.accountId}">${a.accountId} ${a.name}</option>`).join('');
}

let _lineCount = 0;
window.addLine = function(direction = 'DEBIT') {
  const id = ++_lineCount;
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
  if (!el) return;
  el.textContent = `Balance: ¥${Math.abs(diff).toFixed(2)} ${Math.abs(diff) < 0.01 ? '✓' : diff > 0 ? '(debit heavy)' : '(credit heavy)'}`;
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
