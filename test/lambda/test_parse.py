import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda/parse'))

from unittest.mock import patch, MagicMock

def test_compute_md5():
    from index import compute_md5
    data = b'hello world'
    assert compute_md5(data) == '5eb63bbbe01eeed093cb22bb8f5acdc3'

def test_validate_balance_passes():
    from index import validate_balance
    lines = [
        {'direction': 'DEBIT', 'amount': 50.0},
        {'direction': 'CREDIT', 'amount': 50.0},
    ]
    assert validate_balance(lines) is True

def test_validate_balance_fails():
    from index import validate_balance
    lines = [
        {'direction': 'DEBIT', 'amount': 100.0},
        {'direction': 'CREDIT', 'amount': 50.0},
    ]
    assert validate_balance(lines) is False
