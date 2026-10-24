import pytest

from app.supabase_service import get_supabase_service


@pytest.fixture(autouse=True)
def use_memory_storage_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKNOOK_STORAGE", "memory")
    get_supabase_service.cache_clear()
