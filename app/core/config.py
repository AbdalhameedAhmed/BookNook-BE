from __future__ import annotations

import os

VALID_CONDITIONS = {"like_new", "good", "acceptable", "heavily_used"}
DEMO_USERS = {"demo-user-1", "demo-user-2"}
MAX_BOOK_IMAGE_SIZE_BYTES = 2 * 1024 * 1024

IMAGE_SIGNATURES = {
    "image/jpeg": lambda content: content.startswith(b"\xff\xd8\xff"),
    "image/png": lambda content: content.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/webp": lambda content: (
        len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP"
    ),
}

# --- Wallet top-ups ---
# Receipts are phone screenshots, which are often larger than book photos.
MAX_RECEIPT_SIZE_BYTES = 5 * 1024 * 1024
MIN_TOPUP_EGP = 1
MAX_TOPUP_EGP = 50_000
# Abuse guard: a user cannot pile up unreviewed top-ups.
MAX_PENDING_TOPUPS_PER_USER = 5
TOPUP_METHODS = {"e_wallet"}


# --- Platform fee ---
DEFAULT_PLATFORM_FEE_EGP = 10


def platform_fee_egp() -> int:
    """Flat fee charged to each party of a sale or exchange (free listings pay nothing)."""
    raw = os.getenv("PLATFORM_FEE_EGP", "").strip()
    if not raw:
        return DEFAULT_PLATFORM_FEE_EGP
    fee = int(raw)
    if fee < 0:
        raise RuntimeError("PLATFORM_FEE_EGP must be zero or more")
    return fee
