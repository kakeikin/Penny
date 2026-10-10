"""Generate the synthetic eval corpus, gold transactions, and questions (deterministic).

    python eval/generate_dataset.py          # writes eval/dataset/v1/

Everything is fake: no real names, accounts, or merchants' real data. The output is committed,
so eval runs are comparable; regenerate only when bumping DATASET_VERSION. The PDFs and JSON are
byte-reproducible (test/eval checks this); PNG bytes depend on the Pillow version and its bundled
font (Pillow >= 10.1, see eval/requirements.txt), which is why the PNGs are committed too.
"""
import json
import os
import random
import sys
from datetime import datetime
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'test', 'lambda'))
from pdf_fixtures import make_pdf  # noqa: E402  (tiny dependency-free PDF writer)

DATASET_VERSION = 'v1'
SEED = 20260101
OUT = os.path.join(ROOT, 'eval', 'dataset', DATASET_VERSION)
ACCOUNT_NUMBER = '0000123456789012'          # masked to ****9012 by the pipeline
OPENING_BALANCE = Decimal('2400.00')
MONTHS = [(2026, 1, 'January'), (2026, 2, 'February'), (2026, 3, 'March')]
D = Decimal


def money(v: Decimal) -> str:
    return f'{v:.2f}'


def statement_rows(rng: random.Random, month: int) -> list:
    """(day, description, signed amount) in date order."""
    cents = lambda lo, hi: D(rng.randint(lo, hi)) / 100
    return [
        (2,  'TRADER JOES #552',   -cents(4000, 9000)),
        (5,  'PAYROLL ACME CORP',  D('3200.00')),
        (9,  'ABC UTILITIES',      -cents(9000, 14000)),
        (14, 'NETFLIX.COM',        D('-15.49')),
        (17, 'TRADER JOES #552',   -cents(4000, 9000)),
        (21, 'SHELL OIL 57442',    -cents(3000, 6000)),
        (24, 'BLUE BOTTLE COFFEE', -cents(500, 1500)),
        (28, 'RENT PAYMENT',       D('-1850.00')),
    ]


def statement_line(month: int, day: int, desc: str, amount: Decimal) -> str:
    sign = '+' if amount > 0 else '-'
    return f'{month:02d}/{day:02d}  {desc:<22}  {sign + money(abs(amount)):>9}'


RECEIPTS = [
    {'file': 'receipt_2026_02_hardware.png', 'date': '2026-02-11', 'merchant': 'ACE HARDWARE #1182',
     'items': [('Claw Hammer', D('18.99')), ('Wood Screws 100ct', D('6.49')), ('Masking Tape', D('4.29'))],
     'tax': D('1.86'), 'tip': None},
    {'file': 'receipt_2026_03_cafe.png', 'date': '2026-03-19', 'merchant': 'PENNY EVAL CAFE',
     'items': [('Oat Latte', D('5.25')), ('Butter Croissant', D('4.10'))],
     'tax': D('0.65'), 'tip': D('2.00')},
]


def receipt_lines(r: dict) -> tuple:
    subtotal = sum(p for _, p in r['items'])
    total = subtotal + r['tax'] + (r['tip'] or 0)
    # Spelled-out month: 02/11 would be ambiguous (Feb 11 vs 2 Nov) for a parser.
    printed_date = datetime.strptime(r['date'], '%Y-%m-%d').strftime('%b %d, %Y')
    lines = [r['merchant'], f'Date: {printed_date}', '-' * 28]
    lines += [f'{name:<20}{money(p):>8}' for name, p in r['items']]
    lines += ['-' * 28, f'{"Subtotal":<20}{money(subtotal):>8}', f'{"Tax":<20}{money(r["tax"]):>8}']
    if r['tip'] is not None:
        lines.append(f'{"Tip":<20}{money(r["tip"]):>8}')
    lines += [f'{"TOTAL":<20}{money(total):>8}', 'VISA ****4242', 'SYNTHETIC SAMPLE - NOT A REAL RECEIPT']
    return lines, total


def render_png(lines: list, path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont   # only needed to regenerate: pip install pillow
    font = ImageFont.load_default(size=28)
    img = Image.new('RGB', (640, 90 + 40 * len(lines)), 'white')
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        y = 40 + 40 * i
        label, _, price = line.rpartition(' ')
        if label.endswith(' ') and price[:1].isdigit():      # "Tax      0.65": right-align the price
            draw.text((40, y), label.strip(), fill='black', font=font)
            draw.text((600, y), price, fill='black', font=font, anchor='ra')
        else:
            draw.text((40, y), line, fill='black', font=font)
    img.save(path, optimize=False)


def build(out=OUT, render=None):
    """Write the dataset to `out`. `render(lines, path)` draws a receipt PNG (default: Pillow)."""
    render = render or render_png
    rng = random.Random(SEED)
    os.makedirs(out, exist_ok=True)
    gold, files = [], []
    balance = OPENING_BALANCE
    month_spend, month_income = {}, {}
    for year, month, name in MONTHS:
        ym = f'{year}-{month:02d}'
        fname = f'statement_{year}_{month:02d}.pdf'
        rows = statement_rows(rng, month)
        begin = balance
        balance = begin + sum(a for _, _, a in rows)
        text = ['PENNY EVAL BANK - Monthly Statement (SYNTHETIC SAMPLE)',
                f'Account Number: {ACCOUNT_NUMBER}',
                f'Statement Period: {month:02d}/01/{year} - {month:02d}/28/{year}',
                f'Beginning Balance               {money(begin)}',
                'Date   Description               Amount']
        for day, desc, amount in rows:
            line = statement_line(month, day, desc, amount)
            text.append(line)
            gold.append({'file': fname, 'page': 1, 'line': line, 'date': f'{ym}-{day:02d}',
                         'description': desc, 'amount': money(amount)})
        text.append(f'Ending Balance                  {money(balance)}')
        with open(os.path.join(out, fname), 'wb') as f:
            f.write(make_pdf([text]))
        files.append({'file': fname, 'kind': 'statement', 'month': ym, 'entries': len(rows),
                      'beginningBalance': money(begin), 'endingBalance': money(balance)})
        month_spend[ym] = -sum(a for _, _, a in rows if a < 0)
        month_income[ym] = sum(a for _, _, a in rows if a > 0)

    for r in RECEIPTS:
        lines, total = receipt_lines(r)
        render(lines, os.path.join(out, r['file']))
        gold.append({'file': r['file'], 'page': 1, 'line': None, 'date': r['date'],
                     'description': r['merchant'], 'amount': money(-total)})
        files.append({'file': r['file'], 'kind': 'receipt', 'month': r['date'][:7], 'entries': 1})
        month_spend[r['date'][:7]] += total

    by = lambda desc, ym: [g for g in gold if g['description'] == desc and g['date'].startswith(ym)]
    amt = lambda g: money(abs(D(g['amount'])))
    file_of = {f['month']: f for f in files if f['kind'] == 'statement'}
    q = []

    def add(category, question, tools, amounts=(), gold_docs=(), text=None, no_data=False):
        # goldDocs: every (file, page) that legitimately answers the question, for retrieval scoring.
        q.append({'id': f'q{len(q) + 1:02d}', 'category': category, 'question': question,
                  'expectTools': list(tools), 'expectAmounts': list(amounts), 'expectText': text,
                  'goldDocs': list(gold_docs), 'expectNoData': no_data})
    doc = lambda f: {'file': f, 'page': 1}

    for ym, (_, _, mname) in zip(['2026-01', '2026-02', '2026-03'], MONTHS):
        add('summary', f'How much did I spend in total in {mname} 2026, including receipts?',
            ['get_spending_summary'], [money(month_spend[ym])])
    add('summary', 'What was my total income in January 2026?', ['get_spending_summary'],
        [money(month_income['2026-01'])])
    add('summary', 'What was my net income (income minus spending) in March 2026?', ['get_spending_summary'],
        [money(month_income['2026-03'] - month_spend['2026-03'])])
    add('summary', 'How much did I spend in each month from January through March 2026?',
        ['get_spending_summary'], [money(month_spend[m]) for m in ('2026-01', '2026-02', '2026-03')])

    add('transaction', 'How much was my ABC Utilities bill in February 2026?', ['find_transactions'],
        [amt(by('ABC UTILITIES', '2026-02')[0])])
    add('transaction', 'How much was the Netflix charge in March 2026?', ['find_transactions'],
        [amt(by('NETFLIX.COM', '2026-03')[0])])
    add('transaction', 'What were my two Trader Joes purchases in January 2026?', ['find_transactions'],
        [amt(g) for g in by('TRADER JOES #552', '2026-01')])
    add('transaction', 'How much did I pay for gas at Shell in March 2026?', ['find_transactions'],
        [amt(by('SHELL OIL 57442', '2026-03')[0])])
    add('transaction', 'What was my largest single expense in February 2026?', ['find_transactions'],
        ['1850.00'])
    add('transaction', 'How much did I spend at Ace Hardware in February 2026?', ['find_transactions'],
        [amt(by('ACE HARDWARE #1182', '2026-02')[0])])

    add('document', 'What is the ending balance on my February 2026 bank statement?', ['search_documents'],
        [file_of['2026-02']['endingBalance']],
        # February's ending balance is printed again as March's beginning balance.
        [doc('statement_2026_02.pdf'), doc('statement_2026_03.pdf')])
    add('document', 'What was the beginning balance on my March 2026 statement?', ['search_documents'],
        [file_of['2026-03']['beginningBalance']], [doc('statement_2026_03.pdf'), doc('statement_2026_02.pdf')])
    add('document', 'What are the last four digits of the account number on my bank statements?',
        ['search_documents'], [], [doc(f'statement_2026_{m:02d}.pdf') for _, m, _ in MONTHS], text='9012')
    add('document', 'How much tip did I leave on the cafe receipt in March 2026?', ['search_documents'],
        ['2.00'], [doc('receipt_2026_03_cafe.png')])
    add('document', 'Which items are listed on my hardware store receipt from February 2026?',
        ['search_documents'], [], [doc('receipt_2026_02_hardware.png')], text='Hammer')
    add('document', 'Search my January 2026 bank statement document: what line does it show for Blue Bottle Coffee?',
        ['search_documents'], [amt(by('BLUE BOTTLE COFFEE', '2026-01')[0])], [doc('statement_2026_01.pdf')])

    add('no_data', 'How much did I spend in December 2025?', [], no_data=True)
    add('no_data', 'What is my credit score?', [], no_data=True)

    # Held-out paraphrases, written before the post-fix re-run and never used to tune prompts or
    # tools: reported separately so eval-driven fixes can be checked for over-fitting.
    tuned, q = q, []
    add('summary', 'What were my total expenses for February 2026?', ['get_spending_summary'],
        [money(month_spend['2026-02'])])
    add('summary', 'How much money came in during March 2026?', ['get_spending_summary'],
        [money(month_income['2026-03'])])
    add('transaction', 'What did I pay for rent in January 2026?', ['find_transactions'], ['1850.00'])
    add('transaction', 'How much was my Netflix subscription in February 2026?', ['find_transactions'],
        [amt(by('NETFLIX.COM', '2026-02')[0])])
    add('transaction', 'What did I spend on fuel at Shell in January 2026?', ['find_transactions'],
        [amt(by('SHELL OIL 57442', '2026-01')[0])])
    add('document', 'According to my March 2026 bank statement, what was the ending balance?',
        ['search_documents'], [file_of['2026-03']['endingBalance']], [doc('statement_2026_03.pdf')])
    add('document', 'What was the subtotal on my Ace Hardware receipt from February 2026?', ['search_documents'],
        [money(sum(p for _, p in RECEIPTS[0]['items']))], [doc('receipt_2026_02_hardware.png')])
    add('no_data', 'How much did I spend in June 2025?', [], no_data=True)
    holdout = [{**h, 'id': 'h' + h['id'][1:]} for h in q]

    manifest = {'datasetVersion': DATASET_VERSION, 'seed': SEED, 'files': files}
    for name, obj in [('manifest.json', manifest), ('gold_transactions.json', gold), ('queries.json', tuned),
                      ('queries_holdout.json', holdout)]:
        with open(os.path.join(out, name), 'w') as f:
            json.dump(obj, f, indent=2)
            f.write('\n')
    print(f'wrote {len(files)} files, {len(gold)} gold transactions, {len(tuned)} questions '
          f'(+{len(holdout)} held out) to {out}')


if __name__ == '__main__':
    build()
