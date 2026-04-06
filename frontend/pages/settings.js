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
