"""v2 endpoints: job search for any occupation (module 1 occupations, module 2 match_texts). v1 is unchanged.

Module 1 or 2 being down never fails the request: the response says what fell back (module_1_available,
module_2_available, warnings). Errors use the integration shape {"error": {"code", "message"}}.
"""
from __future__ import annotations

from fastapi import APIRouter

from src.general import engine
from src.general.schemas import JobSearchV2Request, JobSearchV2Response
from src.models.schemas import ErrorResponse

router_v2 = APIRouter(prefix="/api/v2", tags=["v2: any occupation"])
CLIENTS = engine.Clients()          # tests replace this with recorded transports


@router_v2.post("/jobs/search", response_model=JobSearchV2Response,
                responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
def search_jobs_v2(request: JobSearchV2Request) -> JobSearchV2Response:
    """Any occupation: target_role (resolved through module 1) or soc_code, a city or region, and the user's free
    text / skills / module 2 profile -> relevant current listings, each matched by module 2 and ranked, with the
    requirements that would unlock the most jobs."""
    return engine.search(request, CLIENTS)
