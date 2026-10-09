import filecmp
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
BASE = os.path.join(ROOT, 'eval', 'dataset', 'v1')
sys.path.insert(0, os.path.join(ROOT, 'eval'))
sys.path.insert(0, os.path.join(ROOT, 'lambda', 'common'))

import generate_dataset  # noqa: E402
from penny_common.pdftext import extract_pdf_pages  # noqa: E402

D = Decimal


def _load(name):
    with open(os.path.join(BASE, name)) as f:
        return json.load(f)


def test_committed_dataset_is_consistent():
    queries, manifest, gold = _load('queries.json'), _load('manifest.json'), _load('gold_transactions.json')
    files = {f['file'] for f in manifest['files']}
    assert all(os.path.exists(os.path.join(BASE, f)) for f in files)
    assert len({q['id'] for q in queries}) == len(queries) == 20
    for q in queries:
        assert q['expectTools'] or q['expectNoData']
        assert all(g['file'] in files for g in q['goldDocs'])
        assert all(D(a) > 0 for a in q['expectAmounts'])
    assert sum(f['entries'] for f in manifest['files']) == len(gold)


def test_dataset_is_reproducible(tmp_path):
    # PNG bytes depend on the Pillow version, so receipts are not regenerated here.
    generate_dataset.build(out=str(tmp_path), render=lambda lines, path: None)
    for name in os.listdir(BASE):
        if name.endswith('.png'):
            continue
        assert filecmp.cmp(os.path.join(BASE, name), os.path.join(tmp_path, name), shallow=False), name


def test_statement_pdfs_contain_every_gold_line_and_balances_chain():
    gold, manifest = _load('gold_transactions.json'), _load('manifest.json')
    statements = [f for f in manifest['files'] if f['kind'] == 'statement']
    for f in statements:
        with open(os.path.join(BASE, f['file']), 'rb') as fh:
            text = extract_pdf_pages(fh.read())[0]['text']
        lines = [g for g in gold if g['file'] == f['file']]
        assert len(lines) == f['entries']
        assert all(g['line'] in text for g in lines)
        assert D(f['endingBalance']) == D(f['beginningBalance']) + sum(D(g['amount']) for g in lines)
        assert f['beginningBalance'] in text and f['endingBalance'] in text
    for prev, cur in zip(statements, statements[1:]):
        assert prev['endingBalance'] == cur['beginningBalance']
