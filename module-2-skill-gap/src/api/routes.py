"""REST endpoints for module 2. Errors use the integration shape {"error": {"code", "message"}}."""
from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from src.engines import similarity
from src.engines.gap_analyzer import analyze_gap
from src.engines.profile_builder import build_profile
from src.engines.skill_extractor import extract_skills, resolve_skill, taxonomy
from src.models.schemas import (
    AnalyzeResumeResponse, ErrorResponse, ExtractedTextSkill, GapAnalysisRequest, GapAnalysisResult, HealthResponse,
    SkillExtractRequest, SkillExtractResponse,
)
from src.parsers.resume_parser import MAX_FILE_BYTES, ResumeParseError, parse_document

VERSION = "1.0.0"
ERROR_STATUS = {"FILE_TOO_LARGE": 413, "UNSUPPORTED_FORMAT": 415, "TOO_MANY_PAGES": 413}
MAX_EXTRACT_CHARS = 50_000  # a long job description is ~10k characters

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
        try:
            gap = analyze_gap(GapAnalysisRequest(target_role=target_role, required_skills=skills, profile=profile))
        except ValueError as e:
            return error(422, "INVALID_REQUEST", str(e))
    return AnalyzeResumeResponse(profile=profile, gap_analysis=gap)


@router.post("/skills/gap_analysis", response_model=GapAnalysisResult, responses=_errors)
def gap_analysis(request: GapAnalysisRequest):
    try:
        return analyze_gap(request)
    except ValueError as e:
        return error(422, "INVALID_REQUEST", str(e))


@router.post("/skills/extract", response_model=SkillExtractResponse, responses=_errors)
def extract(request: SkillExtractRequest):
    """Skills in a job description or other plain text. No evidence or levels: that's for resumes."""
    if not request.text.strip():
        return error(422, "EMPTY_TEXT", "text is empty.")
    if len(request.text) > MAX_EXTRACT_CHARS:
        return error(413, "TEXT_TOO_LARGE", f"text is longer than {MAX_EXTRACT_CHARS:,} characters.")
    warnings = []
    if request.use_llm and not os.getenv("GROQ_API_KEY", "").strip():
        warnings.append("use_llm was requested but no GROQ_API_KEY is configured; dictionary results only.")
    skills = []
    for hit in extract_skills(request.text, use_llm=request.use_llm):
        entry = resolve_skill(hit.id) if hit.in_taxonomy else None
        skills.append(ExtractedTextSkill(**hit.model_dump(), maps_to=entry.get("maps_to") if entry else None))
    if not skills:
        warnings.append("No known skills found in the text.")
    return SkillExtractResponse(skills=skills, warnings=warnings)
