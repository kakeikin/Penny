async function dashboard(app) {
  app.innerHTML = `
    <div class="max-w-5xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>
      <div id="dash-loading" class="text-gray-400">Loading...</div>
      <div id="dash-content" class="hidden space-y-6">
        <div id="alert-banners" class="space-y-2 mb-6"></div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4" id="summary-cards"></div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="card">
            <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Expenses by Category</h2>
            <canvas id="pieChart" height="200"></canvas>
          </div>
          <div class="card">
            <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Net Worth Trend</h2>
            <canvas id="trendChart" height="200"></canvas>
          </div>
        </div>
      </div>
    </div>`;

  async function loadAlerts() {
    const alerts = await API.get('/api/alerts').catch(() => []);
    const container = document.getElementById('alert-banners');
    if (!container) return;
    if (!alerts.length) { container.innerHTML = ''; return; }
    container.innerHTML = alerts.map(a => {
      const msg = a.overLimit
        ? `<strong>${a.accountName}</strong> exceeded monthly limit of ¥${a.monthlyLimit.toFixed(2)} — spent ¥${a.currentMonthTotal.toFixed(2)} this month.`
        : `<strong>${a.accountName}</strong> is ${a.percentOverAverage}% above the 6-month average (¥${a.sixMonthAverage.toFixed(2)}/mo) — spent ¥${a.currentMonthTotal.toFixed(2)} this month.`;
      return `<div class="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900">
        <span class="text-lg">⚠️</span><span>${msg}</span>
      </div>`;
    }).join('');
  }

  try {
    const [data] = await Promise.all([
      API.get('/api/summary'),
      loadAlerts(),
    ]);
    document.getElementById('dash-loading').remove();
    document.getElementById('dash-content').classList.remove('hidden');

    const { thisMonth, netWorth } = data;
    const cards = [
      { label: 'This Month Income',   value: fmt(thisMonth.totalIncome),   color: 'text-green-600' },
      { label: 'This Month Expenses', value: fmt(thisMonth.totalExpenses),  color: 'text-red-600' },
      { label: 'Net Income',          value: fmt(thisMonth.netIncome),      color: thisMonth.netIncome >= 0 ? 'text-green-600' : 'text-red-600' },
      { label: 'Net Worth',           value: fmt(netWorth),                 color: 'text-blue-600' },
    ];
    document.getElementById('summary-cards').innerHTML = cards.map(c => `
      <div class="card">
        <p class="text-xs font-medium text-gray-500 uppercase tracking-wide">${c.label}</p>
        <p class="text-2xl font-bold mt-1 ${c.color}">${c.value}</p>
      </div>`).join('');

    const expLabels = thisMonth.expenses.map(e => e.name);
    const expData   = thisMonth.expenses.map(e => e.amount);
    new Chart(document.getElementById('pieChart'), {
      type: 'doughnut',
      data: { labels: expLabels, datasets: [{ data: expData, backgroundColor: PALETTE }] },
      options: { plugins: { legend: { position: 'right' } } },
    });

    const nwData = await API.get('/api/reports/net-worth');
    new Chart(document.getElementById('trendChart'), {
      type: 'line',
      data: {
        labels: nwData.map(d => d.month),
        datasets: [{
          label: 'Net Worth',
          data: nwData.map(d => d.netWorth),
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.1)',
          fill: true,
          tension: 0.3,
        }],
      },
      options: { scales: { y: { ticks: { callback: v => '¥' + v.toLocaleString() } } } },
    });
  } catch (e) {
    document.getElementById('dash-loading').textContent = 'Error: ' + e.message;
  }
}

const PALETTE = ['#3b82f6','#ef4444','#f59e0b','#10b981','#8b5cf6','#ec4899','#06b6d4','#84cc16'];
const fmt = v => '¥' + Number(v).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
