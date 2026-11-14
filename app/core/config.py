from __future__ import annotations

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