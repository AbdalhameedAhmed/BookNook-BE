from __future__ import annotations

import os

VALID_CONDITIONS = {"like_new", "good", "acceptable", "heavily_used"}
# Egypt's 27 governorates (codes match the listings_governorate_check constraint, migration 020).
GOVERNORATE_NAMES = {
    "cairo": "Cairo", "giza": "Giza", "alexandria": "Alexandria", "qalyubia": "Qalyubia",
    "sharqia": "Sharqia", "dakahlia": "Dakahlia", "gharbia": "Gharbia", "monufia": "Monufia",
    "kafr_el_sheikh": "Kafr El Sheikh", "beheira": "Beheira", "damietta": "Damietta",
    "port_said": "Port Said", "ismailia": "Ismailia", "suez": "Suez", "north_sinai": "North Sinai",
    "south_sinai": "South Sinai", "red_sea": "Red Sea", "fayoum": "Fayoum", "beni_suef": "Beni Suef",
    "minya": "Minya", "asyut": "Asyut", "sohag": "Sohag", "qena": "Qena", "luxor": "Luxor",
    "aswan": "Aswan", "new_valley": "New Valley", "matrouh": "Matrouh",
}
VALID_GOVERNORATES = set(GOVERNORATE_NAMES)
DEMO_USERS = {"demo-user-1", "demo-user-2"}
# Phone photos are often 5–15 MB, so image uploads (book photos, profile photos, receipts) allow 20 MB.
MAX_IMAGE_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_BOOK_IMAGE_SIZE_BYTES = MAX_IMAGE_UPLOAD_BYTES

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
MAX_RECEIPT_SIZE_BYTES = MAX_IMAGE_UPLOAD_BYTES
MIN_TOPUP_EGP = 1
MAX_TOPUP_EGP = 50_000
# Abuse guard: a user cannot pile up unreviewed top-ups.
MAX_PENDING_TOPUPS_PER_USER = 5
TOPUP_METHODS = {"e_wallet"}

# --- Wallet withdrawals (moderators send the money by hand, then mark the request paid) ---
MIN_WITHDRAWAL_EGP = 1
MAX_WITHDRAWAL_EGP = 50_000
MAX_PENDING_WITHDRAWALS_PER_USER = 3


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
