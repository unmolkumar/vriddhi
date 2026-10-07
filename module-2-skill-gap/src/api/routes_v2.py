"""v2 endpoints: the general engine for any occupation (module 1 v2 requirements). v1 (/api/v1) is unchanged.

Errors use the integration shape {"error": {"code", "message"}}. Module 1 unreachable -> 503 M1_UNAVAILABLE; only v2
depends on module 1 being up.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import ValidationError

from src.api.routes import ERROR_STATUS, error
from src.engines.profile_builder import build_profile
from src.general.m1_client import M1Error
from src.general.schemas import (
    MAX_TEXT_CHARS, AnalyzeResumeV2Response, GapAnalysisV2Request, GapAnalysisV2Response, MatchTextRequest,
    MatchTextResponse, MatchTextsRequest, MatchTextsResponse,
)
from src.general.service import GeneralEngine, RoleNotResolved
from src.models.schemas import ErrorResponse
from src.parsers.resume_parser import MAX_FILE_BYTES, ResumeParseError, parse_document

router_v2 = APIRouter(prefix="/api/v2", tags=["v2: any occupation"])
M1_ERRORS = {"not_found": (404, "OCCUPATION_NOT_FOUND"), "unreachable": (503, "M1_UNAVAILABLE"),
             "timeout": (503, "M1_UNAVAILABLE"), "bad_response": (502, "M1_BAD_RESPONSE")}
_errors = {s: {"model": ErrorResponse} for s in (400, 404, 413, 415, 422, 502, 503)}
_engine: GeneralEngine | None = None


def get_engine() -> GeneralEngine:
    """One engine per process (occupations stay prepared between requests). Tests override this dependency."""
    global _engine
    if _engine is None:
        _engine = GeneralEngine()
    return _engine


def _run(fn):
    try:
        return fn()
    except RoleNotResolved as e:
        return error(404, "ROLE_NOT_RESOLVED", str(e))
    except M1Error as e:
        status, code = M1_ERRORS.get(e.code, (502, "M1_BAD_RESPONSE"))
        return error(status, code, str(e))


@router_v2.post("/skills/gap_analysis", response_model=GapAnalysisV2Response, responses=_errors)
def gap_analysis(request: GapAnalysisV2Request, engine: GeneralEngine = Depends(get_engine)):
    """Any occupation: target_role (resolved through module 1) or soc_code, and free text, skills and/or a v1
    profile -> match score, verdict, strengths, gaps, close alternatives and a roadmap."""
    return _run(lambda: engine.analyze(request))


@router_v2.post("/skills/analyze_resume", response_model=AnalyzeResumeV2Response, responses=_errors)
async def analyze_resume(
    file: UploadFile = File(description="Resume: PDF, DOCX or TXT, max 5 MB"),
    target_role: str | None = Form(default=None),
    soc_code: str | None = Form(default=None),
    experience_years: float | None = Form(default=None),
    city: str | None = Form(default=None),
    hours_per_week: float | None = Form(default=None),
    engine: GeneralEngine = Depends(get_engine),
):
    """v1 parsing (same file checks) -> v2 gap analysis on the resume text."""
    data = await file.read(MAX_FILE_BYTES + 1)  # never read more than the limit into memory
    try:
        doc = parse_document(data, file.filename)
    except ResumeParseError as e:
        return error(ERROR_STATUS.get(e.code, 400), e.code, e.message)
    profile = build_profile(doc, use_llm=False, location=city, target_occupation=target_role)
    try:
        request = GapAnalysisV2Request(
            target_role=target_role, soc_code=soc_code, free_text=doc.text[:MAX_TEXT_CHARS], city=city,
            experience_years=experience_years if experience_years is not None else profile.experience_years or None,
            hours_per_week=hours_per_week)
    except ValidationError as e:
        return error(422, "INVALID_REQUEST", "; ".join(err["msg"] for err in e.errors()))
    return _run(lambda: AnalyzeResumeV2Response(profile=profile, gap_analysis=engine.analyze(request)))


@router_v2.post("/skills/match_text", response_model=MatchTextResponse, responses=_errors)
def match_text(request: MatchTextRequest, engine: GeneralEngine = Depends(get_engine)):
    """For module 3: one job's text (+ optional soc_code) against the user's evidence -> job-level match."""
    return _run(lambda: engine.match_text(request))


@router_v2.post("/skills/match_texts", response_model=MatchTextsResponse, responses=_errors)
def match_texts(request: MatchTextsRequest, engine: GeneralEngine = Depends(get_engine)):
    """For module 3: one user's evidence against up to 50 jobs ({job_id, job_text, job_title?, soc_code?}) -> one
    match_text-shaped result per job, in order. The evidence is prepared once. A job's SOC that module 1 can't give
    falls back to its text alone (warning on that job)."""
    return _run(lambda: engine.match_texts(request))
