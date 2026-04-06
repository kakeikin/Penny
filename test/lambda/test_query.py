import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/query'))

def test_build_account_tree():
    from index import build_account_tree
    accounts = [
        {'accountId': '1000', 'name': 'Assets', 'type': 'ASSET', 'parentId': None},
        {'accountId': '1100', 'name': 'Bank', 'type': 'ASSET', 'parentId': '1000'},
    ]
    tree = build_account_tree(accounts)
    assert len(tree) == 1
    assert tree[0]['accountId'] == '1000'
    assert len(tree[0]['children']) == 1
    assert tree[0]['children'][0]['accountId'] == '1100'

def test_income_statement_sums():
    from index import compute_income_statement
    entries = [
        {'entryId': 'e1', 'date': '2026-04-01', 'status': 'CONFIRMED'},
    ]
    lines_by_entry = {
        'e1': [
            {'accountId': '4100', 'direction': 'CREDIT', 'amount': '5000.00'},
            {'accountId': '1100', 'direction': 'DEBIT',  'amount': '5000.00'},
        ]
    }
    accounts = {
        '4100': {'accountId': '4100', 'name': 'Salary', 'type': 'INCOME'},
        '1100': {'accountId': '1100', 'name': 'Bank', 'type': 'ASSET'},
    }
    result = compute_income_statement(entries, lines_by_entry, accounts)
    assert result['totalIncome'] == 5000.0
    assert result['totalExpenses'] == 0.0
    assert result['netIncome'] == 5000.0

def test_balance_sheet_assets():
    from index import compute_balance_sheet
    entries = [{'entryId': 'e1'}]
    lines_by_entry = {
        'e1': [
            {'accountId': '1100', 'direction': 'DEBIT', 'amount': '1000.00'},
            {'accountId': '4100', 'direction': 'CREDIT', 'amount': '1000.00'},
        ]
    }
    accounts = {
        '1100': {'accountId': '1100', 'name': 'Bank', 'type': 'ASSET'},
        '4100': {'accountId': '4100', 'name': 'Salary', 'type': 'INCOME'},
    }
    result = compute_balance_sheet(accounts, lines_by_entry, entries)
    assert result['totalAssets'] == 1000.0
    assert result['totalLiabilities'] == 0.0
