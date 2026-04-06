async function reports(app) {
  const now = new Date();
  const startOfYear = `${now.getFullYear()}-01-01`;
  const today = now.toISOString().slice(0, 10);

  app.innerHTML = `
    <div class="max-w-4xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Reports</h1>
      <div class="card mb-6 flex gap-4 flex-wrap items-end">
        <div>
          <label class="text-sm text-gray-600">From</label>
          <input id="r-start" type="date" class="block border rounded px-3 py-1.5 text-sm mt-1" value="${startOfYear}" />
        </div>
        <div>
          <label class="text-sm text-gray-600">To</label>
          <input id="r-end" type="date" class="block border rounded px-3 py-1.5 text-sm mt-1" value="${today}" />
        </div>
        <button onclick="loadReports()" class="btn-primary">Run</button>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div class="card">
          <h2 class="font-semibold text-gray-800 mb-4">Income Statement</h2>
          <div id="income-stmt"></div>
        </div>
        <div class="card">
          <h2 class="font-semibold text-gray-800 mb-4">Balance Sheet</h2>
          <div id="balance-sheet"></div>
        </div>
      </div>

      <div class="card mb-4">
        <h2 class="font-semibold text-gray-800 mb-4">Net Worth Timeline</h2>
        <canvas id="nwChart" height="100"></canvas>
      </div>

      <div class="flex justify-end">
        <a id="export-link" class="btn-primary" download="transactions.csv">Export CSV</a>
      </div>
    </div>`;

  let nwChart;

  window.loadReports = async function() {
    const start = document.getElementById('r-start').value;
    const end   = document.getElementById('r-end').value;
    const q     = `?startDate=${start}&endDate=${end}`;

    document.getElementById('export-link').href = `${window.API_BASE}/api/export/csv${q}`;

    const [is, bs, nw] = await Promise.all([
      API.get(`/api/reports/income-statement${q}`),
      API.get(`/api/reports/balance-sheet?asOf=${end}`),
      API.get('/api/reports/net-worth'),
    ]);

    document.getElementById('income-stmt').innerHTML = `
      <table class="w-full text-sm">
        <thead><tr class="text-xs text-gray-400 uppercase"><th class="text-left pb-2">Account</th><th class="text-right pb-2">Amount</th></tr></thead>
        <tbody>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-2 pb-1">Income</td></tr>
          ${is.income.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-green-600">¥${r.amount.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td class="pt-2">Total Income</td><td class="text-right text-green-600 pt-2">¥${is.totalIncome.toFixed(2)}</td></tr>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Expenses</td></tr>
          ${is.expenses.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">¥${r.amount.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td class="pt-2">Total Expenses</td><td class="text-right text-red-500 pt-2">¥${is.totalExpenses.toFixed(2)}</td></tr>
          <tr class="border-t-2 font-bold text-lg"><td class="pt-2">Net Income</td><td class="text-right pt-2 ${is.netIncome >= 0 ? 'text-green-600' : 'text-red-500'}">¥${is.netIncome.toFixed(2)}</td></tr>
        </tbody>
      </table>`;

    document.getElementById('balance-sheet').innerHTML = `
      <table class="w-full text-sm">
        <thead><tr class="text-xs text-gray-400 uppercase"><th class="text-left pb-2">Account</th><th class="text-right pb-2">Balance</th></tr></thead>
        <tbody>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pb-1">Assets</td></tr>
          ${bs.assets.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
          <tr class="border-t font-semibold"><td class="pt-2">Total Assets</td><td class="text-right pt-2">¥${bs.totalAssets.toFixed(2)}</td></tr>
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Liabilities</td></tr>
          ${bs.liabilities.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right text-red-500">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
          <tr class="font-semibold text-gray-500"><td colspan="2" class="pt-4 pb-1">Equity</td></tr>
          ${bs.equity.map(r => `<tr><td class="py-0.5 pl-3 text-gray-700">${r.name}</td><td class="text-right">¥${r.balance.toFixed(2)}</td></tr>`).join('')}
        </tbody>
      </table>`;

    if (nwChart) nwChart.destroy();
    nwChart = new Chart(document.getElementById('nwChart'), {
      type: 'line',
      data: {
        labels: nw.map(d => d.month),
        datasets: [{
          label: 'Net Worth',
          data: nw.map(d => d.netWorth),
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.3,
        }],
      },
      options: { scales: { y: { ticks: { callback: v => '¥' + v.toLocaleString() } } } },
    });
  };

  loadReports();
}
