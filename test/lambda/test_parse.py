import sys, os, json
import pytest
from unittest.mock import patch, MagicMock

_parse_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../lambda/parse')
)
if _parse_path not in sys.path:
    sys.path.insert(0, _parse_path)


@pytest.fixture(autouse=True)
def isolate_parse_index():
    saved_path = sys.path[:]
    saved_module = sys.modules.get('index')

    sys.modules.pop('index', None)
    if _parse_path in sys.path:
        sys.path.remove(_parse_path)
    sys.path.insert(0, _parse_path)

    yield

    sys.modules.pop('index', None)
    sys.path[:] = saved_path
    if saved_module is not None:
        sys.modules['index'] = saved_module


def test_compute_md5():
    from index import compute_md5
    data = b'hello world'
    assert compute_md5(data) == '5eb63bbbe01eeed093cb22bb8f5acdc3'


def test_validate_balance_passes():
    from index import validate_balance
    lines = [
        {'direction': 'DEBIT',  'amount': 50.0},
        {'direction': 'CREDIT', 'amount': 50.0},
    ]
    assert validate_balance(lines) is True


def test_validate_balance_fails():
    from index import validate_balance
    lines = [
        {'direction': 'DEBIT',  'amount': 100.0},
        {'direction': 'CREDIT', 'amount': 50.0},
    ]
    assert validate_balance(lines) is False
