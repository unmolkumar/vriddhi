"""Pydantic contracts for Module 2 (profile + extracted skills).

UserProfile is a superset of the common user profile in context/INTEGRATION.md:
skills[].name / level / evidence, experience_years, education (list[str]), location,
preferred_locations, target_occupation. Extra fields are additive.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Evidence = Literal["self_reported", "resume_mentioned", "project_supported", "work_supported"]
DocumentFormat = Literal["pdf", "docx", "text", "manual"]


class ErrorDetail(BaseModel):
    code: str = Field(description="UPPER_SNAKE error code, e.g. CORRUPT_FILE")
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class SkillHit(BaseModel):
    """One skill found in a piece of text by the extractor."""
    id: str = Field(description="Taxonomy id (module-1 compatible), or a slug of the LLM wording if not in the taxonomy")
    display: str
    category: str | None = None
    in_taxonomy: bool
    source: Literal["dictionary", "llm"]
    matches: list[str] = Field(default_factory=list, description="Surface forms found in the text")


class ExtractedSkill(BaseModel):
    """A skill in the user's profile, with evidence and an evidence-based level."""
    name: str = Field(description="Taxonomy id, e.g. 'python' (INTEGRATION.md skills[].name)")
    display: str
    category: str | None = None
    in_taxonomy: bool = True
    maps_to: str | None = Field(default=None, description="Coarser taxonomy id, e.g. postgresql -> sql")
    level: int = Field(ge=0, le=5, description="0 not demonstrated … 5 expert; derived from evidence")
    confidence: float = Field(ge=0.0, le=1.0, description="How strongly the evidence supports this skill")
    evidence: list[Evidence]
    claimed_level: int | None = Field(default=None, ge=0, le=5, description="Level the user typed, if any")
    needs_verification: bool = Field(default=False, description="Claimed level is above what the evidence supports")


class WorkEntry(BaseModel):
    title: str | None = None
    company: str | None = None
    start: str | None = Field(default=None, description="YYYY-MM")
    end: str | None = Field(default=None, description="YYYY-MM; null when current")
    current: bool = False
    internship: bool = False


class EducationEntry(BaseModel):
    degree: str
    field: str | None = None
    institution: str | None = None
    year: int | None = None


class SourceInfo(BaseModel):
    format: DocumentFormat
    pages: int = 0
    ocr_pages: list[int] = Field(default_factory=list, description="1-based pages read with OCR")
    text_sha1: str | None = Field(default=None, description="Hash of the text for caching; the text itself is not kept")
    sections_found: list[str] = Field(default_factory=list)


class UserProfile(BaseModel):
    user_id: str | None = None
    skills: list[ExtractedSkill] = Field(default_factory=list)
    experience_years: float = Field(default=0.0, ge=0.0, description="Merged full-time work spans, rounded to 0.5")
    internship_years: float = Field(default=0.0, ge=0.0)
    education: list[str] = Field(default_factory=list, description="e.g. 'B.Tech Computer Science'")
    education_details: list[EducationEntry] = Field(default_factory=list)
    work_history: list[WorkEntry] = Field(default_factory=list)
    location: str | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    target_occupation: str | None = None
    source: SourceInfo
    warnings: list[str] = Field(default_factory=list)


class ManualSkill(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    level: int | None = Field(default=None, ge=0, le=5)


class ManualProfileInput(BaseModel):
    """Typed-in profile (no resume)."""
    skills: list[str | ManualSkill] = Field(default_factory=list)
    experience_years: float = Field(default=0.0, ge=0.0, le=60.0)
    location: str | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    target_occupation: str | None = None
    education: list[str] = Field(default_factory=list)
