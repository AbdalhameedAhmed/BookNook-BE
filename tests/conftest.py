import pytest

import app.main  # noqa: F401  (puts app/ on sys.path so top-level module names resolve)
from core import rate_limit
from core.auth_settings import get_auth_settings
from crud.crud_user import MEMORY_AUTH_STORE
from crud.crud_deals import MEMORY_DEAL_STORE
from crud.crud_item import reset_memory_data
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE
from crud.crud_promos import MEMORY_PROMO_STORE
from crud.crud_wallet import MEMORY_WALLET_STORE
from supabase_service import get_supabase_service


@pytest.fixture(autouse=True)
def use_memory_storage_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RFOOF_STORAGE", "memory")
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()
    MEMORY_AUTH_STORE.reset()
    MEMORY_WALLET_STORE.reset()
    MEMORY_DEAL_STORE.reset()
    reset_memory_data()
    MEMORY_PROMO_STORE.reset()
    MEMORY_NOTIFICATION_STORE.reset()
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    for limiter in rate_limit.ALL_LIMITERS:
        limiter.reset()
