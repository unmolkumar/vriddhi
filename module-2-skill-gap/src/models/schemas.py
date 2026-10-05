"""Pydantic contracts for Module 2 (profile + extracted skills).

UserProfile is a superset of the common user profile in context/INTEGRATION.md:
skills[].name / level / evidence, experience_years, education (list[str]), location,
preferred_locations, target_occupation. Extra fields are additive.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    ocr_used: bool = False
    ocr_pages: list[int] = Field(default_factory=list, description="1-based pages read with OCR")
    ocr_seconds: float | None = Field(default=None, description="Time spent on OCR, for 'scanned resume' UI hints")
    ocr_repairs: list[str] = Field(default_factory=list, description="OCR confusions fixed, e.g. 'Power Bl -> Power BI'")
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


# --- gap analysis (step 2) ------------------------------------------------------------

MatchStatus = Literal["matched", "adjacent", "missing"]
MatchReason = Literal["exact", "maps_to", "prerequisite", "semantic"]
Verdict = Literal["under_skilled", "good_fit", "over_qualified"]
ImportanceSource = Literal["skill_importance", "knowledge_graph", "rank_decay"]


class ExperienceRange(BaseModel):
    min: float = Field(ge=0.0, le=60.0)
    max: float = Field(ge=0.0, le=60.0)

    @model_validator(mode="after")
    def _ordered(self) -> "ExperienceRange":
        if self.max < self.min:
            raise ValueError("max must be >= min")
        return self


class GapAnalysisRequest(BaseModel):
    """Module 1 target (plain data, no import) + the user's profile or typed skills."""
    target_role: str = Field(min_length=1, max_length=120, description="Module 1 occupation")
    required_skills: list[str] = Field(min_length=1, max_length=50, description="Module 1 top_skills ids, most important first")
    knowledge_graph: dict | None = Field(default=None, description="Module 1 knowledge_graph (nodes, edges), optional")
    skill_importance: dict[str, float] | None = Field(default=None, description="Optional id -> weight in [0, 1]")
    profile: UserProfile | None = Field(default=None, description="From /analyze_resume")
    manual_profile: ManualProfileInput | None = Field(default=None, description="Typed skills, if there's no resume")
    typical_experience_years: ExperienceRange | None = Field(
        default=None, description="Role's usual experience band; derived from the title's seniority if absent")
    hours_per_week: float | None = Field(default=None, gt=0, le=80, description="For weekly roadmap milestones")

    @model_validator(mode="after")
    def _one_profile(self) -> "GapAnalysisRequest":
        if (self.profile is None) == (self.manual_profile is None):
            raise ValueError("send exactly one of 'profile' or 'manual_profile'")
        return self


class SkillGap(BaseModel):
    """One required skill compared with the profile."""
    skill: str = Field(description="Required skill id (taxonomy id when it resolves)")
    display: str
    in_taxonomy: bool
    status: MatchStatus
    reason: MatchReason | None = Field(default=None, description="Why it matched / is adjacent; null when missing")
    via: str | None = Field(default=None, description="The user's skill that matched or is adjacent")
    via_display: str | None = None
    similarity: float | None = Field(default=None, description="Cosine, only when reason = semantic")
    relation: str | None = Field(default=None, description=(
        "For adjacent skills, how the required skill relates to the user's: 'is related to X' (shared maps_to "
        "parent), 'builds on X' (X is its prerequisite), 'is a foundation of X' (it is X's prerequisite), "
        "'is similar to X' (semantic)"))
    importance: float = Field(ge=0.0, le=1.0)
    priority: Literal["High", "Medium", "Low"]
    required_level: int = Field(ge=0, le=5)
    current_level: int = Field(ge=0, le=5)
    gap: int = Field(description="required_level - current_level (>0 gap, 0 met, <0 above)")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence behind the user's skill")
    advice: str | None = None


class SkillBuckets(BaseModel):
    matched: list[str] = Field(default_factory=list, description="Requirement met (gap <= 0)")
    weak: list[str] = Field(default_factory=list, description="Matched but below the required level")
    adjacent: list[str] = Field(default_factory=list, description="Related skill present; quick win")
    critical_missing: list[str] = Field(default_factory=list, description="Not present, most important first")
    above_requirement: list[str] = Field(default_factory=list, description="Matched above the required level")


class ScoreBreakdown(BaseModel):
    coverage: float = Field(description="Importance-weighted credit over required skills, 0-1")
    experience_factor: float = Field(description="1 when experience reaches the role's minimum, lower below it")
    coverage_weight: float
    experience_weight: float
    adjacent_credit: float
    formula: str


class HourRange(BaseModel):
    low: int
    high: int


class Milestone(BaseModel):
    order: int
    skill: str
    display: str
    kind: Literal["missing", "adjacent", "weak", "prerequisite"]
    reason: str
    importance: float
    difficulty_tier: int
    prerequisites: list[str] = Field(default_factory=list, description="Ids learned earlier in this roadmap")
    required_for: list[str] = Field(default_factory=list, description="For pulled-in prerequisites")
    estimated_hours: HourRange
    estimated_weeks: HourRange | None = Field(default=None, description="Cumulative week range when hours_per_week is given")


class Roadmap(BaseModel):
    milestones: list[Milestone] = Field(default_factory=list)
    total_estimated_hours: HourRange
    hours_per_week: float | None = None
    estimated_total_weeks: HourRange | None = None
    note: str


class GapAnalysisResult(BaseModel):
    target_role: str
    match_score: float = Field(ge=0.0, le=1.0)
    match_percent: int = Field(ge=0, le=100)
    verdict: Verdict
    verdict_message: str
    suggested_role: str | None = Field(default=None, description="Higher-level role when over-qualified")
    score_breakdown: ScoreBreakdown
    importance_source: ImportanceSource
    experience_years: float
    typical_experience_years: ExperienceRange
    gap_matrix: list[SkillGap]
    skills: SkillBuckets
    strengths: list[str] = Field(default_factory=list)
    learning_priorities: list[str] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    roadmap: Roadmap
    similarity_backend: Literal["minilm", "tfidf"]
    warnings: list[str] = Field(default_factory=list)


class AnalyzeResumeResponse(BaseModel):
    profile: UserProfile
    gap_analysis: GapAnalysisResult | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    module: str
    version: str
    taxonomy_skills: int
    similarity_backend: Literal["minilm", "tfidf"]
    llm_configured: bool


EXPORTED_MODELS = [UserProfile, ManualProfileInput, GapAnalysisRequest, GapAnalysisResult,
                   AnalyzeResumeResponse, HealthResponse, ErrorResponse]


def export_json_schema() -> dict:
    """JSON Schema for module 2's public models (written to src/models/schema_m2.json)."""
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(m, "validation") for m in EXPORTED_MODELS],
                                   title="Module 2 Skill Gap contracts")
    return schema


if __name__ == "__main__":
    import json
    from pathlib import Path

    out = Path(__file__).with_name("schema_m2.json")
    out.write_text(json.dumps(export_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
