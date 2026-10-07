import importlib.util
import os

_LAMBDA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda'))


def load_lambda(name: str):
    """Import lambda/<name>/index.py as its own module.

    Every Lambda's entry file is named index.py, so importing them via sys.path
    makes tests depend on execution order. Loading by file path avoids that.
    """
    path = os.path.join(_LAMBDA_ROOT, name, 'index.py')
    spec = importlib.util.spec_from_file_location(f"lambda_{name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
