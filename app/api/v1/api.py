from fastapi import APIRouter

from api.v1.endpoints import account, admin, auth, handovers, items, notifications, profile, wallet

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(profile.profile_router)
api_router.include_router(account.router)
api_router.include_router(items.router)
api_router.include_router(wallet.router)
api_router.include_router(handovers.router)
api_router.include_router(admin.router)
api_router.include_router(notifications.router)
