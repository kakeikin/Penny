# scripts/generate-vapid-keys.py
# Run once to generate VAPID keys for Web Push notifications
# Usage: pip install py_vapid cryptography && python scripts/generate-vapid-keys.py

from py_vapid import Vapid
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
import json

v = Vapid()
v.generate_keys()

public_key = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()
private_key = v.private_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()).decode()

print(json.dumps({
    "public_key": public_key,
    "private_key": private_key,
}, indent=2))
