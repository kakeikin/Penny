// frontend/citations.js is a browser script; it also exports itself for these tests.
const Citations = require('../../frontend/citations.js');

const T1 = {
  ref: 'T1', type: 'transaction', entryId: 'e1', date: '2026-03-09', description: 'ABC Utilities',
  amount: '120.00', kind: 'expense', accounts: ['Bank Accounts', 'Other Expenses'],
  evidence: { page: 1, text: '03/09  ABC UTILITIES  -120.00' },
};
const D1 = { ref: 'D1', type: 'document', fileName: 'mar.pdf', page: 1, text: 'NETFLIX.COM -15.49', score: '0.81', chunkKey: 'k' };

describe('renderAnswer', () => {
  test('turns known refs into chips', () => {
    const html = Citations.renderAnswer('You spent $120.00 [T1].', [T1]);
    expect(html).toContain('<button type="button"');
    expect(html).toContain('data-ref="T1">T1</button>');
    expect(html.startsWith('You spent $120.00 ')).toBe(true);
  });

  test('leaves unknown refs as plain text', () => {
    expect(Citations.renderAnswer('See [D9].', [T1])).toBe('See [D9].');
  });

  test('escapes model output before adding markup', () => {
    const html = Citations.renderAnswer('<img src=x onerror=alert(1)> [T1]', [T1]);
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });

  test('keeps line breaks', () => {
    expect(Citations.renderAnswer('a\nb', [])).toBe('a<br>b');
  });

  test('tolerates missing citations and answer', () => {
    expect(Citations.renderAnswer(undefined, undefined)).toBe('');
  });
});

describe('detailHtml', () => {
  test('transaction shows the escaped source line', () => {
    const html = Citations.detailHtml({ ...T1, description: '<b>x</b>' });
    expect(html).toContain('03/09  ABC UTILITIES  -120.00');
    expect(html).toContain('$120.00');
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;');
    expect(html).toContain('Bank Accounts → Other Expenses');
  });

  test('transaction without evidence says so', () => {
    expect(Citations.detailHtml({ ...T1, evidence: null })).toContain('No source line');
  });

  test('document shows file, page and chunk text', () => {
    const html = Citations.detailHtml(D1);
    expect(html).toContain('mar.pdf');
    expect(html).toContain('NETFLIX.COM -15.49');
  });

  test('summaries by month and by account', () => {
    expect(Citations.detailHtml({ ref: 'S1', type: 'summary', groupBy: 'month', month: '2026-03',
      income: '3200.00', expense: '2097.87', net: '1102.13' })).toContain('$1102.13');
    expect(Citations.detailHtml({ ref: 'S2', type: 'summary', groupBy: 'account', accountName: 'Rent',
      amount: '1850.00', period: '2026-03..2026-03' })).toContain('Rent');
  });

  test('missing or unknown citation', () => {
    expect(Citations.detailHtml(undefined)).toContain('not found');
    expect(Citations.detailHtml({ ref: 'X1', type: 'other' })).toContain('Unknown');
  });
});

describe('evidence links', () => {
  test('presigned https link opens the cited page', () => {
    const html = Citations.evidenceHtml([{ page: 2, text: 'line', fileUrl: 'https://s3.example/x?sig=a&b=c' }]);
    expect(html).toContain('href="https://s3.example/x?sig=a&amp;b=c#page=2"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  test('non-https URLs are never linked', () => {
    const html = Citations.evidenceHtml([{ page: 1, text: 'line', fileUrl: 'javascript:alert(1)' }]);
    expect(html).not.toContain('href=');
    expect(html).toContain('Original file not available');
  });

  test('empty evidence', () => {
    expect(Citations.evidenceHtml([])).toContain('No source evidence');
    expect(Citations.sourceLinksHtml(undefined)).toBe('');
  });

  test('sourceLinksHtml omits the quoted text', () => {
    const html = Citations.sourceLinksHtml([{ page: 1, text: 'SECRET LINE', fileUrl: 'https://s3.example/x' }]);
    expect(html).not.toContain('SECRET LINE');
    expect(html).toContain('Open source page 1');
  });
});

describe('money', () => {
  test('negative amounts put the sign before the symbol and stay exact', () => {
    const html = Citations.detailHtml({ ref: 'S1', type: 'summary', groupBy: 'month', month: '2026-03',
      income: '0.10', expense: '0.30', net: '-0.20' });
    expect(html).toContain('-$0.20');
    expect(html).toContain('$0.10');
  });
});
