"""REST endpoints for module 2. Errors use the integration shape {"error": {"code", "message"}}."""
from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from src.engines import similarity
from src.engines.gap_analyzer import analyze_gap
from src.engines.profile_builder import build_profile
from src.engines.skill_extractor import taxonomy
from src.models.schemas import (
    AnalyzeResumeResponse, ErrorResponse, GapAnalysisRequest, GapAnalysisResult, HealthResponse,
)
from src.parsers.resume_parser import MAX_FILE_BYTES, ResumeParseError, parse_document

VERSION = "1.0.0"
ERROR_STATUS = {"FILE_TOO_LARGE": 413, "UNSUPPORTED_FORMAT": 415, "TOO_MANY_PAGES": 413}

router = APIRouter(prefix="/api/v1")
_errors = {400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 415: {"model": ErrorResponse},
           422: {"model": ErrorResponse}}


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", module="module-2-skill-gap", version=VERSION,
                          taxonomy_skills=len(taxonomy()["skills"]), similarity_backend=similarity.backend(),
                          llm_configured=bool(os.getenv("GROQ_API_KEY", "").strip()))


@router.post("/skills/analyze_resume", response_model=AnalyzeResumeResponse, responses=_errors)
async def analyze_resume(
    file: UploadFile = File(description="Resume: PDF, DOCX or TXT, max 5 MB"),
    target_role: str | None = Form(default=None, description="If given with required_skills, a gap analysis is included"),
    required_skills: str | None = Form(default=None, description="Comma-separated module 1 skill ids"),
    location: str | None = Form(default=None),
    use_llm: bool = Form(default=True, description="Optional Groq pass for skills the dictionary misses"),
):
    data = await file.read(MAX_FILE_BYTES + 1)  # never read more than the limit into memory
    try:
        doc = parse_document(data, file.filename)
    except ResumeParseError as e:
        return error(ERROR_STATUS.get(e.code, 400), e.code, e.message)
    profile = build_profile(doc, use_llm=use_llm, location=location, target_occupation=target_role)
    gap = None
    skills = [s.strip() for s in (required_skills or "").split(",") if s.strip()]
    if skills:
        if not target_role:
            return error(422, "INVALID_REQUEST", "required_skills needs target_role.")
        gap = analyze_gap(GapAnalysisRequest(target_role=target_role, required_skills=skills, profile=profile))
    return AnalyzeResumeResponse(profile=profile, gap_analysis=gap)


@router.post("/skills/gap_analysis", response_model=GapAnalysisResult, responses=_errors)
def gap_analysis(request: GapAnalysisRequest) -> GapAnalysisResult:
    return analyze_gap(request)
