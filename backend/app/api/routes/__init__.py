"""All API routers, mounted under /api."""

from fastapi import APIRouter

from app.api.routes import approvals, audit, auth, chat, evals, health, staff, tickets

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(tickets.router)
api_router.include_router(staff.router)
api_router.include_router(approvals.router)
api_router.include_router(audit.router)
api_router.include_router(evals.router)
