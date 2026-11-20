"""Prints a fresh VAPID key pair for Web Push, in the format the API expects.

    uv run python scripts/generate_vapid_keys.py

Put both lines in Rfoof-BE/.env. Keep VAPID_PRIVATE_KEY secret. Changing the keys invalidates
every existing browser subscription (users re-enable alerts once).
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


key = ec.generate_private_key(ec.SECP256R1())
private_raw = key.private_numbers().private_value.to_bytes(32, "big")
public_raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(f"VAPID_PRIVATE_KEY={b64url(private_raw)}")
print(f"VAPID_PUBLIC_KEY={b64url(public_raw)}")
