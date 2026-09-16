"""Liveness. Open, unauthenticated, and deliberately says nothing else — a
health probe that leaked version or tenant counts would be a reconnaissance
surface on the one route guaranteed to be reachable.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
