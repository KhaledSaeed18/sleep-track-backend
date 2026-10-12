"""Versioned API router composed from the feature modules."""

from fastapi import APIRouter

from app.modules.admin.router import router as admin_router
from app.modules.analytics.router import router as analytics_router
from app.modules.auth.router import router as auth_router
from app.modules.goals.router import router as goals_router
from app.modules.insights.router import router as insights_router
from app.modules.meta.router import router as meta_router
from app.modules.reports.router import router as reports_router
from app.modules.sleep.router import router as sleep_router
from app.modules.tags.router import router as tags_router
from app.modules.users.router import router as users_router

api_router = APIRouter()
for module_router in (
    auth_router,
    users_router,
    sleep_router,
    tags_router,
    goals_router,
    analytics_router,
    insights_router,
    reports_router,
    admin_router,
    meta_router,
):
    api_router.include_router(module_router)
