"""Per-model token prices (USD per million tokens) for cost logging."""
import re
from decimal import Decimal

# Anthropic list prices; Bedrock bills separately and can differ - verify on the AWS
# pricing page when changing models. A key matches the model id as a whole token, so
# "claude-haiku-5-5" does not match a hypothetical "claude-haiku-5-5-mini", while
# Bedrock's dated ids ("us.anthropic.claude-haiku-4-5-20251001-v1:0") still match.
PRICES = {
    'claude-haiku-5-5':  (Decimal('0.10'), Decimal('0.50')),
    'claude-sonnet-5-5': (Decimal('2.00'), Decimal('10.00')),
    'claude-sonnet-4-6': (Decimal('3.00'), Decimal('15.00')),
    'claude-haiku-4-5':  (Decimal('1.00'), Decimal('5.00')),
}
_MILLION = Decimal(1_000_000)


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int):
    """Cost as a decimal string (6 dp), or None for an unknown model."""
    for key in sorted(PRICES, key=len, reverse=True):
        # Whole token; a dated snapshot suffix ("-20251001-v1:0") is still the same model.
        if re.search(rf'(?<![\w-]){re.escape(key)}(?!\w|-(?!\d{{8}}\b))', model_id or ''):
            inp, out = PRICES[key]
            cost = (Decimal(input_tokens) * inp + Decimal(output_tokens) * out) / _MILLION
            return str(cost.quantize(Decimal('0.000001')))
    return None
