// Pure rendering helpers for advisor answers and source evidence (no DOM, no network).
// Model output and document text are untrusted: everything is escaped before it becomes HTML.
const Citations = (() => {
  const REF = /\[([DST]\d+)\]/g;
  const CHIP = 'cite-chip inline-flex items-center px-1.5 mx-0.5 rounded bg-[#f0f4e8] text-[#6a8a3e] '
             + 'text-xs font-semibold hover:bg-[#e2ebd3] align-baseline';

  function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Only presigned https links are ever rendered as hrefs (never javascript: or data: URLs).
  function safeUrl(url) {
    return typeof url === 'string' && url.startsWith('https://') ? url : null;
  }

  // Escaped answer with each known [T1]/[D2]/[S3] ref turned into a chip; unknown refs stay text.
  function renderAnswer(answer, citations) {
    const known = new Set((citations || []).map(c => c.ref));
    return esc(answer)
      .replace(REF, (m, ref) => known.has(ref)
        ? `<button type="button" class="${CHIP}" data-ref="${ref}">${ref}</button>` : m)
      .replace(/\n/g, '<br>');
  }

  // Amounts arrive as exact decimal strings from the API; keep them exact (no float parsing).
  function money(v) {
    const s = String(v ?? '');
    return s.startsWith('-') ? '-$' + esc(s.slice(1)) : '$' + esc(s);
  }

  function quote(text) {
    return `<pre class="whitespace-pre-wrap bg-gray-50 border rounded-lg p-3 text-xs text-gray-700 font-mono">${esc(text)}</pre>`;
  }

  function row(label, value) {
    return `<div class="flex gap-2 text-sm"><span class="text-gray-400 w-24 shrink-0">${esc(label)}</span>`
         + `<span class="text-gray-800">${value}</span></div>`;
  }

  // Viewer body for one advisor citation.
  function detailHtml(c) {
    if (!c) return '<p class="text-sm text-gray-500">Citation not found.</p>';
    if (c.type === 'transaction') {
      return [
        row('Transaction', esc(c.description)),
        row('Date', esc(c.date)),
        row('Amount', money(c.amount)),
        row('Accounts', esc((c.accounts || []).join(' → '))),
        c.evidence ? row('Source', `page ${esc(c.evidence.page)}`) + quote(c.evidence.text)
                   : '<p class="text-sm text-gray-400">No source line was recorded for this entry.</p>',
      ].join('');
    }
    if (c.type === 'document') {
      return row('Document', esc(c.fileName)) + row('Page', esc(c.page)) + row('Match', esc(c.score)) + quote(c.text);
    }
    if (c.type === 'summary' && c.groupBy === 'month') {
      return row('Month', esc(c.month)) + row('Income', money(c.income)) + row('Expense', money(c.expense))
           + row('Net', money(c.net));
    }
    if (c.type === 'summary') {
      return row('Account', esc(c.accountName)) + row('Period', esc(c.period)) + row('Spent', money(c.amount));
    }
    return '<p class="text-sm text-gray-500">Unknown citation type.</p>';
  }

  function linkHtml(ev) {
    const url = safeUrl(ev.fileUrl);
    return url
      ? `<a href="${esc(url)}#page=${encodeURIComponent(ev.page)}" target="_blank" rel="noopener noreferrer"`
        + ` class="text-sm text-[#6a8a3e] underline">Open source page ${esc(ev.page)} ↗</a>`
      : '<span class="text-xs text-gray-400">Original file not available.</span>';
  }

  // Viewer body for GET /api/entries/{id}/evidence.
  function evidenceHtml(evidence) {
    if (!evidence || !evidence.length) {
      return '<p class="text-sm text-gray-400">No source evidence was recorded for this entry.</p>';
    }
    return evidence.map(ev => `<div class="mb-4">${row('Page', esc(ev.page))}${quote(ev.text)}${linkHtml(ev)}</div>`).join('');
  }

  // Just the links to the original file (a transaction citation already shows the quoted line).
  function sourceLinksHtml(evidence) {
    return (evidence || []).map(ev => `<div>${linkHtml(ev)}</div>`).join('');
  }

  return { esc, safeUrl, renderAnswer, detailHtml, evidenceHtml, sourceLinksHtml };
})();

if (typeof module !== 'undefined') module.exports = Citations;   // jest
