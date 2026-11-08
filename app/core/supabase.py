"""Adapter for the project's optional Supabase service implementation.

If ``supabase_service`` is present, it is used normally. If it is not
provided (for example, in a fresh demo checkout), the API runs with its
in-memory demo data instead.
"""

try:
    from supabase_service import get_supabase_service as _get_supabase_service

except ModuleNotFoundError as exc:

    if exc.name != "supabase_service":
        raise

    def get_supabase_service():
        return None
else:
    get_supabase_service = _get_supabase_service


__all__ = ["get_supabase_service"]