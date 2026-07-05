"""uvicorn entry: `uvicorn axor_backend.main:app --factory`."""
from __future__ import annotations

from fastapi import FastAPI

from axor_backend.app import create_app


def app() -> FastAPI:
    return create_app()
