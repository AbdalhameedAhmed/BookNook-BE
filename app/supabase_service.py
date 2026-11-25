from __future__ import annotations

import os
import base64
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException, status

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


class SupabaseService:
    def __init__(self, url: str, api_key: str) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.is_legacy_jwt_key = len(api_key.split(".")) == 3

    # --- Authentication (users table owned by the backend; see migration 006) ---

    _USER_COLUMNS = "id,email,password_hash,role,email_confirmed_at,password_changed_at"

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        rows = self.rest(
            "GET", "users", params={"select": self._USER_COLUMNS, "email": f"eq.{email}", "limit": "1"}
        ).json()
        return rows[0] if rows else None

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        if not _is_uuid(user_id):
            return None
        rows = self.rest(
            "GET", "users", params={"select": self._USER_COLUMNS, "id": f"eq.{user_id}", "limit": "1"}
        ).json()
        return rows[0] if rows else None

    def get_display_name(self, user_id: str) -> str | None:
        rows = self.rest(
            "GET", "profiles", params={"select": "display_name", "id": f"eq.{user_id}", "limit": "1"}
        ).json()
        return rows[0]["display_name"] if rows else None

    def get_profile(self, user_id: str) -> dict[str, Any]:
        """Display name, photo and taken tours in one read (used for /auth/me and sign-in)."""
        rows = self.rest(
            "GET", "profiles",
            params={"select": "display_name,avatar_url,completed_tours", "id": f"eq.{user_id}", "limit": "1"},
        ).json()
        return rows[0] if rows else {}

    def public_profile(self, user_id: str) -> dict[str, Any] | None:
        if not _is_uuid(user_id):
            return None
        return self.rpc("public_profile", {"p_user_id": user_id})

    def complete_tour(self, user_id: str, tour_id: str) -> list[str]:
        return self.rpc("complete_tour", {"p_user_id": user_id, "p_tour": tour_id}) or []

    def get_avatar_url(self, user_id: str) -> str | None:
        rows = self.rest(
            "GET", "profiles", params={"select": "avatar_url", "id": f"eq.{user_id}", "limit": "1"}
        ).json()
        return rows[0].get("avatar_url") if rows else None

    def set_avatar_url(self, user_id: str, url: str | None) -> None:
        self.rest("PATCH", "profiles", params={"id": f"eq.{user_id}"},
                  payload={"avatar_url": url, "updated_at": datetime.now(timezone.utc).isoformat()})

    def request_email_change(self, user_id: str, new_email: str, token_hash: str, expires_at: datetime) -> None:
        self.rpc("request_email_change", {
            "p_user_id": user_id, "p_new_email": new_email, "p_token_hash": token_hash,
            "p_expires_at": expires_at.isoformat(),
        })

    def confirm_email_change(self, token_hash: str) -> dict[str, Any] | None:
        return self.rpc("confirm_email_change", {"p_token_hash": token_hash})

    def register_user(self, email: str, password_hash: str, display_name: str,
                      invite_code: str | None = None) -> dict[str, Any] | None:
        return self.rpc(
            "register_user",
            {"p_email": email, "p_password_hash": password_hash, "p_display_name": display_name,
             "p_invite_code": invite_code},
        )

    def issue_auth_token(self, user_id: str, purpose: str, token_hash: str, expires_at: datetime) -> None:
        self.rpc(
            "issue_auth_token",
            {
                "p_user_id": user_id,
                "p_purpose": purpose,
                "p_token_hash": token_hash,
                "p_expires_at": expires_at.isoformat(),
            },
        )

    def confirm_user_email(self, token_hash: str) -> str | None:
        return self.rpc("confirm_user_email", {"p_token_hash": token_hash})

    def reset_user_password(self, token_hash: str, password_hash: str) -> str | None:
        return self.rpc("reset_user_password", {"p_token_hash": token_hash, "p_password_hash": password_hash})

    def upload_listing_image(self, seller_id: str, content: bytes, content_type: str) -> str:
        return self._upload_image(f"{seller_id}/", content, content_type, "Book photo could not be uploaded")

    def upload_avatar(self, user_id: str, content: bytes, content_type: str) -> str:
        return self._upload_image(f"avatars/{user_id}/", content, content_type, "Profile photo could not be uploaded")

    def delete_avatar_object(self, user_id: str, url: str) -> None:
        """Best effort: removes an old profile photo from storage so its public URL stops working."""
        prefix = f"{self.url}/storage/v1/object/public/book-images/"
        if not url.startswith(f"{prefix}avatars/{user_id}/"):
            return
        headers = {"apikey": self.api_key}
        if self.is_legacy_jwt_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            httpx.delete(f"{self.url}/storage/v1/object/book-images/{url[len(prefix):]}", headers=headers, timeout=10)
        except httpx.HTTPError:
            pass

    def _upload_image(self, folder: str, content: bytes, content_type: str, failure: str) -> str:
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
        path = f"{folder}{uuid4()}.{extension}"
        headers = {
            "apikey": self.api_key,
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        if self.is_legacy_jwt_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = httpx.post(
                f"{self.url}/storage/v1/object/book-images/{path}",
                content=content,
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="Image storage service unavailable") from error

        if response.is_error:
            raise HTTPException(status_code=502, detail=failure)

        return f"{self.url}/storage/v1/object/public/book-images/{quote(path, safe='/')}"

    def rest(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        headers = {
            "apikey": self.api_key,
            "Accept": "application/json",
        }
        if self.is_legacy_jwt_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        try:
            response = httpx.request(
                method,
                f"{self.url}/rest/v1/{table}",
                params=params,
                json=payload,
                headers=headers,
                timeout=10,
            )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="Database service unavailable") from error

        if response.is_error:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            message = error_payload.get("message", "") if isinstance(error_payload, dict) else ""
            domain_errors = {
                "listing_not_found": (404, "Listing not found"),
                "request_not_found": (404, "Request not found"),
                "transaction_not_found": (404, "Transaction not found"),
                "own_listing": (403, "You cannot request your own listing"),
                "buyer_only_cancel": (403, "Only the buyer can cancel a request"),
                "seller_only_decision": (403, "Only the seller can accept or reject a request"),
                "transaction_participant_only": (403, "You are not authorized to complete this transaction"),
                "listing_unavailable": (400, "Inactive, reserved, or sold listings cannot receive this action"),
                "request_not_pending": (409, "Only pending requests can be updated"),
                "duplicate_pending_request": (409, "You already have a pending request for this listing"),
                "transaction_not_arranging": (409, "Only arranging transactions can be completed"),
                "unsupported_request_status": (400, "Unsupported request status update"),
                "topup_not_found": (404, "Top-up not found"),
                "invalid_tour": (422, "Unknown tour"),
                "topup_not_pending": (409, "Only pending top-ups can be reviewed"),
                "insufficient_funds": (402, "Insufficient wallet balance"),
                "withdrawal_not_found": (404, "Withdrawal not found"),
                "withdrawal_not_pending": (409, "Only pending withdrawals can be reviewed"),
                "too_many_pending_withdrawals": (409, "You have too many withdrawals waiting to be paid"),
                "offer_required": (400, "Choose at least one of your books to offer"),
                "offer_invalid": (400, "Offered books must be your own available listings"),
                "choose_offer": (400, "Choose one of the offered books"),
                "offer_unavailable": (409, "The chosen book is no longer available"),
                "buyer_only_confirm": (403, "Only the buyer can confirm receiving the book"),
                "already_confirmed": (409, "You already confirmed this handover"),
                "listing_in_handover": (409, "This book is in an open handover. Finish or cancel the handover first."),
                "unsupported_listing_status": (400, "Unsupported listing status"),
                "promo_invalid": (400, "This promo code isn't valid"),
                "promo_exhausted": (409, "This promo code has been fully used"),
                "promo_not_applicable": (400, "Promo codes only apply to sale and exchange fees"),
                "promo_code_taken": (409, "A promo code with this name already exists"),
            }
            if message in domain_errors:
                code, detail = domain_errors[message]
                raise HTTPException(status_code=code, detail=detail)
            raise HTTPException(status_code=502, detail="Database request failed")
        return response

    def rpc(self, function: str, payload: dict[str, Any]) -> Any:
        response = self.rest("POST", f"rpc/{function}", payload=payload)
        # VOID functions return 204 No Content with an empty body.
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def list_listings(
        self,
        *,
        query: str | None,
        listing_type: str | None,
        condition: str | None,
        page: int,
        governorate: str | None = None,
        page_size: int,
        seller_id: str | None = None,
        sort: str = "newest",
    ) -> tuple[list[dict[str, Any]], int]:
        if seller_id is not None and not _is_uuid(seller_id):
            return [], 0
        params = {
            "select": "*,profiles(display_name)",
            "status": "eq.active",
            "order": "price_egp.asc.nullslast,created_at.desc" if sort == "price_asc" else "created_at.desc",
            "offset": str((page - 1) * page_size),
            "limit": str(page_size),
        }
        if query and query.strip():
            term = query.strip().replace("%", "")
            params["or"] = f"(title.ilike.*{term}*,author.ilike.*{term}*)"
        if listing_type:
            params["listing_type"] = f"eq.{listing_type}"
        if condition:
            params["condition"] = f"eq.{condition}"
        if governorate:
            params["governorate"] = f"eq.{governorate}"
        if seller_id:
            params["seller_id"] = f"eq.{seller_id}"

        response = self.rest("GET", "listings", params=params, prefer="count=exact")
        content_range = response.headers.get("content-range", "*/0")
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            total = len(response.json())
        return [self._listing_view(row) for row in response.json()], total

    def get_listing(self, listing_id: str, seller_id: str | None = None) -> dict[str, Any]:
        params = {"select": "*,profiles(display_name)", "id": f"eq.{listing_id}", "limit": "1"}
        if seller_id is None:
            params["status"] = "eq.active"
        else:
            params["seller_id"] = f"eq.{seller_id}"
        response = self.rest(
            "GET",
            "listings",
            params=params,
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Listing not found")
        return self._listing_view(rows[0])

    def create_listing(self, payload: dict[str, Any], seller_id: str) -> dict[str, Any]:
        response = self.rest(
            "POST",
            "listings",
            params={"select": "*,profiles(display_name)"},
            payload={**payload, "seller_id": seller_id},
            prefer="return=representation",
        )
        rows = response.json()
        return self._listing_view(rows[0])

    def update_listing(self, listing_id: str, seller_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self._get_owned_listing(listing_id, seller_id)
        if not payload:
            return existing
        response = self.rest(
            "PATCH",
            "listings",
            params={"select": "*,profiles(display_name)", "id": f"eq.{listing_id}", "seller_id": f"eq.{seller_id}"},
            payload=payload,
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Listing not found")
        return self._listing_view(rows[0])

    def my_listings(self, seller_id: str) -> list[dict[str, Any]]:
        response = self.rest(
            "GET",
            "listings",
            params={"select": "*,profiles(display_name)", "seller_id": f"eq.{seller_id}", "order": "created_at.desc"},
        )
        return [self._listing_view(row) for row in response.json()]

    def create_report(self, payload: dict[str, Any], reporter_id: str) -> dict[str, Any]:
        response = self.rest(
            "POST",
            "reports",
            params={"select": "*"},
            payload={**payload, "reporter_id": reporter_id},
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=502, detail="Report was not saved")
        return rows[0]

    def my_reports(self, reporter_id: str, is_moderator: bool) -> list[dict[str, Any]]:
        params: dict[str, str] = {"select": "*", "order": "created_at.desc"}
        if not is_moderator:
            params["reporter_id"] = f"eq.{reporter_id}"
        return self.rest("GET", "reports", params=params).json()

    def update_report(self, report_id: str, next_status: str) -> dict[str, Any]:
        response = self.rest(
            "PATCH",
            "reports",
            params={"id": f"eq.{report_id}", "select": "*"},
            payload={"status": next_status},
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Report not found")
        return rows[0]

    def _get_owned_listing(self, listing_id: str, seller_id: str) -> dict[str, Any]:
        response = self.rest(
            "GET",
            "listings",
            params={
                "select": "*,profiles(display_name)",
                "id": f"eq.{listing_id}",
                "seller_id": f"eq.{seller_id}",
                "limit": "1",
            },
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Listing not found")
        return self._listing_view(rows[0])

    @staticmethod
    def _listing_view(row: dict[str, Any]) -> dict[str, Any]:
        profile = row.pop("profiles", None) or {}
        row["seller_name"] = profile.get("display_name") or "BookNook user"
        row["images"] = row.get("images") or []
        return row



@lru_cache(maxsize=1)
def get_supabase_service() -> SupabaseService | None:
    storage = os.getenv("RFOOF_STORAGE", "memory").strip().lower()

    if storage == "memory":
        return None
    if storage != "supabase":
        raise RuntimeError("RFOOF_STORAGE must be either 'memory' or 'supabase'")

    url = os.getenv("SUPABASE_URL", "").strip()
    api_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not all((url, api_key)):
        raise RuntimeError("Supabase storage requires SUPABASE_URL and SUPABASE_SECRET_KEY")
    if not _is_privileged_supabase_key(api_key):
        raise RuntimeError(
            "SUPABASE_SECRET_KEY must be an sb_secret_ key or a legacy service_role key; "
            "publishable/anon keys cannot be used for backend database writes"
        )
    return SupabaseService(url, api_key)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _is_privileged_supabase_key(api_key: str) -> bool:
    if api_key.startswith("sb_secret_"):
        return True

    parts = api_key.split(".")
    if len(parts) != 3:
        return False

    try:
        payload_segment = parts[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_segment))
    except (ValueError, json.JSONDecodeError):
        return False

    return isinstance(claims, dict) and claims.get("role") == "service_role"