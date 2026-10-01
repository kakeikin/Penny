"""Mask long numeric identifiers (account / card numbers) before storage or embedding."""
import re

# 8+ consecutive digits, unless they are part of an amount (12345678.90 / 12345678,90).
# Compact YYYYMMDD dates are masked too. Known v1 gap: numbers printed with internal
# spaces or dashes (4111 1111 1111 1111) are not masked.
_LONG_ID = re.compile(r'(?<!\d)(?<!\d[.,])(\d{8,})(?!\d)(?![.,]\d)')


def mask_identifiers(text: str) -> str:
    """Preserve only the last 4 digits for long numeric identifiers."""
    return _LONG_ID.sub(lambda m: '****' + m.group(1)[-4:], text or '')
