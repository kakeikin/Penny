// One modal shared by the Transactions page ("View source evidence") and advisor citation chips.
const EvidenceViewer = (() => {
  let seq = 0;          // bumped by every open/close: a slow fetch never repaints a newer view
  let returnFocus = null;

  function modal() {
    let el = document.getElementById('evidence-modal');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'evidence-modal';
    el.className = 'fixed inset-0 bg-black/40 z-50 hidden items-center justify-center p-4';
    el.innerHTML = `
      <div role="dialog" aria-modal="true" aria-labelledby="evidence-title"
           class="bg-white rounded-xl shadow-lg w-full max-w-lg max-h-[80vh] overflow-y-auto p-6">
        <div class="flex justify-between items-center mb-4">
          <h2 id="evidence-title" class="font-semibold text-gray-900"></h2>
          <button type="button" id="evidence-close" class="text-gray-400 hover:text-gray-600 text-xl" aria-label="Close">×</button>
        </div>
        <div id="evidence-body"></div>
      </div>`;
    el.addEventListener('click', e => { if (e.target === el || e.target.id === 'evidence-close') close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && isOpen()) close(); });
    window.addEventListener('hashchange', close);    // the modal lives outside #app, so close it on navigation
    document.body.appendChild(el);
    return el;
  }

  function isOpen() {
    const el = document.getElementById('evidence-modal');
    return !!el && !el.classList.contains('hidden');
  }

  function show(title, bodyHtml) {
    const el = modal();
    if (!isOpen()) returnFocus = document.activeElement;
    document.getElementById('evidence-title').textContent = title;
    document.getElementById('evidence-body').innerHTML = bodyHtml;
    el.classList.remove('hidden');
    el.classList.add('flex');
    document.getElementById('evidence-close').focus();
  }

  function close() {
    seq++;
    if (!isOpen()) return;
    const el = document.getElementById('evidence-modal');
    el.classList.add('hidden');
    el.classList.remove('flex');
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
  }

  async function fetchEvidence(entryId, render) {
    try {
      const res = await API.get(`/api/entries/${encodeURIComponent(entryId)}/evidence`);
      return render(res.evidence);
    } catch (e) {
      return `<p class="text-sm text-red-600">Could not load evidence: ${Citations.esc(e.message)}</p>`;
    }
  }

  async function openEntry(entryId) {
    const mine = ++seq;
    show('Source evidence', '<p class="text-sm text-gray-400">Loading…</p>');
    const html = await fetchEvidence(entryId, Citations.evidenceHtml);
    if (mine === seq) show('Source evidence', html);
  }

  // A transaction chip already quotes its source line; fetch only the presigned link to the file.
  async function openCitation(citation) {
    const title = citation ? `Citation ${citation.ref}` : 'Citation';
    const detail = Citations.detailHtml(citation);
    const mine = ++seq;
    if (!citation || citation.type !== 'transaction' || !citation.evidence) return show(title, detail);
    show(title, detail + '<p class="text-sm text-gray-400 mt-3">Loading source file…</p>');
    const links = await fetchEvidence(citation.entryId, Citations.sourceLinksHtml);
    if (mine === seq) show(title, detail + `<div class="mt-3">${links}</div>`);
  }

  return { openEntry, openCitation, close };
})();
