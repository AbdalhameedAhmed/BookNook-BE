from fastapi import APIRouter

from api.v1.endpoints import auth, items, profile

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(profile.profile_router)
api_router.include_router(items.router)




