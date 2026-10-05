"""REST endpoints for module 3. Errors use the integration shape {"error": {"code", "message"}}."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.engines.job_store import JobStore
from src.engines.m2_client import base_url
from src.engines.salary import candidate_value, negotiate
from src.engines.search import _role_band, run_search
from src.models.schemas import (
    ErrorResponse, HealthResponse, JobSearchRequest, JobSearchResponse, NegotiateRequest, NegotiateResponse,
    SalaryEstimateRequest, SalaryEstimateResponse,
)
from src.providers import adzuna, jsearch

VERSION = "1.0.0"

router = APIRouter(prefix="/api/v1")
_errors = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", module="module-3-job-matching-salary", version=VERSION,
                          providers={"adzuna": adzuna.configured(), "jsearch": jsearch.configured()},
                          m2_base_url=base_url(), cache=JobStore().stats())


@router.post("/jobs/search", response_model=JobSearchResponse, responses=_errors)
def search_jobs(request: JobSearchRequest) -> JobSearchResponse:
    """Current jobs for a role and city, matched and ranked for the candidate, with salary and negotiation."""
    return run_search(request).response


@router.post("/salary/estimate", response_model=SalaryEstimateResponse, responses=_errors)
def estimate_salary(request: SalaryEstimateRequest) -> SalaryEstimateResponse:
    """Market range for the role and city, and the candidate's estimated range within it."""
    r = run_search(request).response
    return SalaryEstimateResponse(target_role=r.target_role, cities=r.cities, market_salary=r.market_salary,
                                  candidate_value=r.candidate_value, warnings=r.warnings)


@router.post("/salary/negotiate", response_model=NegotiateResponse, responses=_errors)
def negotiate_salary(request: NegotiateRequest):
    """Negotiation guidance for one job (a job_id from a search) or a posted salary you were offered."""
    ctx = run_search(request)
    r = ctx.response
    job = None
    if request.job_id:
        job = ctx.jobs.get(request.job_id)
        if job is None:
            return error(404, "JOB_NOT_FOUND", f"Job {request.job_id} isn't in the current results for this search.")
    match = ctx.matches.get(job.job_id) if job else (r.jobs[0].match_score / 100 if r.jobs else None)
    value = (candidate_value(r.market_salary, match_score=match, years=request.candidate().experience_years,
                             band=_role_band(request), city=r.location) if match is not None else r.candidate_value)
    posted_min = job.salary_min if job else request.posted_salary_min
    posted_max = job.salary_max if job else request.posted_salary_max
    deal = negotiate(market=r.market_salary, candidate=value, job_id=job.job_id if job else None, posted_min=posted_min,
                     posted_max=posted_max, posted_is_predicted=bool(job and job.salary_is_predicted))
    return NegotiateResponse(target_role=r.target_role, negotiation=deal, market_salary=r.market_salary,
                             candidate_value=value, match_score=round(match * 100) if match is not None else None,
                             warnings=r.warnings)
