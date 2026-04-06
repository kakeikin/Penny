async function upload(app) {
  app.innerHTML = `
    <div class="max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Upload Statement or Receipt</h1>

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

      <div id="pending-section" class="hidden">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-gray-800">Review Parsed Entries</h2>
          <button id="confirm-all" class="bg-green-600 hover:bg-green-700 text-white font-semibold px-5 py-2 rounded-lg text-sm transition-colors shadow-sm">Confirm All ✓</button>
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
      <div class="card" id="entry-${e.entryId}">
        <div class="flex justify-between items-start mb-3">
          <div>
            <p class="font-medium text-gray-800">${e.date} — ${e.description}</p>
            <span class="badge-pending">${e.source}</span>
          </div>
          <button onclick="confirmEntry('${e.entryId}')" class="bg-blue-600 hover:bg-blue-700 text-white font-semibold px-4 py-2 rounded-lg text-sm transition-colors shadow-sm">Confirm ✓</button>
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
  fileInput.onchange = () => fileInput.files[0] && handleFile(fileInput.files[0]);

  await loadPending();
}
