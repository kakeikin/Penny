import os
import sys

import pytest

from lambda_loader import load_lambda

# boto3 clients are created at import time in every Lambda; give tests a region by default.
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# Lambdas import penny_common from the layer at runtime; tests import it from source.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda/common')))


@pytest.fixture
def lambda_module():
    return load_lambda
