"""REST endpoints for module 3. Errors use the integration shape {"error": {"code", "message"}}."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.engines.job_store import JobStore
from src.engines.m2_client import base_url
from src.models.schemas import HealthResponse
from src.providers import adzuna, jsearch

VERSION = "0.1.0"

router = APIRouter(prefix="/api/v1")


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", module="module-3-job-matching-salary", version=VERSION,
                          providers={"adzuna": adzuna.configured(), "jsearch": jsearch.configured()},
                          m2_base_url=base_url(), cache=JobStore().stats())
