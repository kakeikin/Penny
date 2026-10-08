async function advisor(app) {
  app.innerHTML = `
    <div class="max-w-2xl mx-auto">
      <h1 class="text-2xl font-bold text-gray-900 mb-2">AI Financial Advisor</h1>
      <p class="text-sm text-gray-500 mb-6">Ask questions about your finances in plain language. Every figure links to its source: click a citation to see the transaction or document it came from.</p>

      <div class="card mb-4" style="min-height: 400px; max-height: 600px; overflow-y: auto;" id="chat-history">
        <div class="text-center text-gray-400 text-sm py-16" id="chat-placeholder">
          <div class="text-3xl mb-3">💬</div>
          <p>Ask anything about your finances.</p>
          <p class="mt-1">e.g. "Where did I spend the most last month?" or "How does my rent compare to my income?"</p>
        </div>
      </div>

      <div class="flex gap-3">
        <input id="advisor-input" type="text"
          class="flex-1 border rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          placeholder="Ask a question about your finances..."
          onkeydown="if(event.key==='Enter' && !event.shiftKey){ event.preventDefault(); sendQuestion(); }" />
        <button onclick="sendQuestion()" id="advisor-send"
          class="bg-[#8aaa5e] hover:bg-[#7a9a4e] text-white font-medium px-5 py-3 rounded-xl text-sm transition-colors shadow-sm">
          Ask
        </button>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <span class="text-xs text-gray-400">Try:</span>
        ${[
          "Where did I spend the most last month?",
          "What's my biggest recurring expense?",
          "How does my income compare to expenses?",
          "Am I saving money each month?",
        ].map(q => `<button onclick="document.getElementById('advisor-input').value=${JSON.stringify(q)}; sendQuestion();" class="text-xs bg-gray-100 hover:bg-gray-200 text-gray-600 px-3 py-1 rounded-full transition-colors">${q}</button>`).join('')}
      </div>
    </div>`;

  const citationsByMessage = new Map();   // message id -> citations array, for chip clicks
  let messageSeq = 0;

  document.getElementById('chat-history').addEventListener('click', e => {
    const chip = e.target.closest('.cite-chip');
    if (!chip) return;
    const citations = citationsByMessage.get(chip.closest('[data-msg]')?.dataset.msg) || [];
    EvidenceViewer.openCitation(citations.find(c => c.ref === chip.dataset.ref));
  });

  // html must already be safe: user text is escaped, advisor text goes through Citations.renderAnswer.
  function appendMessage(role, html, citations) {
    const history = document.getElementById('chat-history');
    const placeholder = document.getElementById('chat-placeholder');
    if (placeholder) placeholder.remove();

    const isUser = role === 'user';
    const id = String(++messageSeq);
    if (citations) citationsByMessage.set(id, citations);
    const div = document.createElement('div');
    div.dataset.msg = id;
    div.className = `flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`;
    div.innerHTML = `
      <div class="max-w-lg px-4 py-3 rounded-2xl text-sm ${isUser
        ? 'bg-[#8aaa5e] text-white rounded-br-sm'
        : 'bg-gray-100 text-gray-800 rounded-bl-sm'}">
        ${html}
      </div>`;
    history.appendChild(div);
    history.scrollTop = history.scrollHeight;
  }

  window.sendQuestion = async () => {
    const input = document.getElementById('advisor-input');
    const sendBtn = document.getElementById('advisor-send');
    const question = input.value.trim();
    if (!question) return;

    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;
    sendBtn.textContent = '...';

    appendMessage('user', Citations.esc(question));

    // Thinking indicator
    const history = document.getElementById('chat-history');
    const thinking = document.createElement('div');
    thinking.id = 'thinking';
    thinking.className = 'flex justify-start mb-4';
    thinking.innerHTML = `<div class="bg-gray-100 text-gray-400 text-sm px-4 py-3 rounded-2xl rounded-bl-sm">Analyzing your finances…</div>`;
    history.appendChild(thinking);
    history.scrollTop = history.scrollHeight;

    try {
      const res = await API.post('/api/advisor', { question });
      document.getElementById('thinking')?.remove();
      const notes = [];
      if (res.evidenceStatus === 'unsupported') notes.push('Some figures in this answer have no cited source.');
      if (res.truncated) notes.push('The lookup was cut short; the answer may be incomplete.');
      appendMessage('advisor', Citations.renderAnswer(res.answer || 'No response.', res.citations)
        + notes.map(n => `<p class="mt-2 text-xs text-amber-700">${Citations.esc(n)}</p>`).join(''),
        res.citations || []);
    } catch (e) {
      document.getElementById('thinking')?.remove();
      appendMessage('advisor', Citations.esc(`Error: ${e.message}`));
    } finally {
      input.disabled = false;
      sendBtn.disabled = false;
      sendBtn.textContent = 'Ask';
      input.focus();
    }
  };
}
