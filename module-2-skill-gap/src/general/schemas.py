"""Public contracts of the general engine (v2): /api/v2/skills/gap_analysis, /analyze_resume, /match_text.

Exported to src/models/schema_m2_v2.json (python -m src.general.schemas); a test checks it doesn't drift.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.models.schemas import ErrorResponse, HourRange, UserProfile

SOC_PATTERN = r"^\d{2}-\d{4}\.\d{2}$"
MAX_TEXT_CHARS = 50_000
Status = Literal["met", "partial", "missing"]
Provenance = Literal["onet", "india_postings", "curated", "job_text"]
VerdictLabel = Literal["under_skilled", "insufficient_evidence", "good_fit", "over_qualified"]


class EvidenceSources(BaseModel):
    """One or more of: free text (resume text, a description in any style), typed skills, a v1 profile."""
    free_text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS,
                                  description="Resume text or a description of your work, any style")
    skills: list[str] = Field(default_factory=list, max_length=200, description="Typed skills (self-reported)")
    profile: UserProfile | None = Field(default=None, description="v1 UserProfile from /api/v1/skills/analyze_resume")
    experience_years: float | None = Field(default=None, ge=0, le=60, description="Overrides the parsed experience")

    def has_evidence(self) -> bool:
        return bool((self.free_text or "").strip() or [s for s in self.skills if s.strip()] or self.profile)


class AnswerIn(BaseModel):
    requirement_id: str = Field(description="follow_up_questions[].requirement_id")
    answer: Literal["yes", "no", "some"]
    detail: str | None = Field(default=None, max_length=500, description="Optional: where or how you did it")


class GapAnalysisV2Request(EvidenceSources):
    target_role: str | None = Field(default=None, min_length=2, max_length=120,
                                    description="Free-text role, e.g. 'staff nurse'; resolved through module 1")
    soc_code: str | None = Field(default=None, pattern=SOC_PATTERN, description="O*NET-SOC code, e.g. 29-1141.00")
    city: str | None = Field(default=None, max_length=60)
    hours_per_week: float | None = Field(default=None, gt=0, le=80, description="For roadmap weeks")
    answers: list[AnswerIn] = Field(default_factory=list, max_length=20,
                                    description="Answers to follow_up_questions: yes/some become self-reported evidence")

    @model_validator(mode="after")
    def _check(self) -> "GapAnalysisV2Request":
        if not (self.target_role or self.soc_code):
            raise ValueError("give target_role or soc_code")
        if not self.has_evidence():
            raise ValueError("give free_text, skills or profile")
        return self


class RoleOption(BaseModel):
    soc_code: str
    title: str
    confidence: float


class RoleResolution(BaseModel):
    soc_code: str
    title: str
    confidence: float = Field(description="1.0 when soc_code was given")
    method: str | None = None
    low_confidence: bool
    did_you_mean: list[RoleOption] = Field(default_factory=list, description="Other matches when confidence is low")


class EvidenceRef(BaseModel):
    text: str
    evidence_type: Literal["work", "project", "mentioned", "self"]
    section: str
    span: tuple[int, int] | None = Field(default=None, description="Offsets in free_text")
    context_span: tuple[int, int] | None = None
    translated: bool = Field(default=False, description="text is an English rewrite of original_text")
    original_text: str | None = Field(default=None, description="The evidence as written, when translated or rewritten")
    rewrites: list[str] = Field(default_factory=list, description="Shorthand expanded in text ('BP -> blood pressure')")


class RequirementResult(BaseModel):
    requirement: str
    item_type: str
    item_id: str
    status: Status
    similarity: float
    credit: float = Field(description="0-1 contribution: met -> min(1, evidence strength / required level)")
    weight: float
    required_level: float
    provenance: Provenance
    reason: Literal["alias", "semantic", "implied_by_role", "answered", "none"]
    evidence: EvidenceRef | None = None
    advice: str | None = None
    flags: list[str] = Field(default_factory=list)


class TypeScore(BaseModel):
    share: float = Field(description="Effective share after reliability and item-count scaling")
    coverage: float
    items: int


class ScoreBreakdown(BaseModel):
    skill_score: float = Field(description="Weighted coverage over core requirements")
    by_type: dict[str, TypeScore]
    experience_years: float | None
    experience_band: tuple[float, float] | None
    experience_band_source: Literal["india_postings", "job_zone", "none"]
    experience_factor: float = Field(description="1.0 inside or above the band; down to EXPERIENCE_MIN_FACTOR below it")


class Verdict(BaseModel):
    label: VerdictLabel
    reason: str
    suggested_role: RoleOption | None = Field(default=None, description="For over_qualified: a more senior fit")


class DrawsOnItem(BaseModel):
    name: str
    item_type: Literal["knowledge", "skill"]
    importance: float
    inferred: bool = Field(description="Supported by your matched tasks/DWAs or education; never a gap")
    support: str | None = None


class WorkActivityItem(BaseModel):
    name: str
    status: Literal["evidenced", "not_evidenced", "no_dwa_data"]
    via: list[str] = Field(default_factory=list)


class FitIndicatorItem(BaseModel):
    name: str
    importance: float
    level: float


class NotApplicableItem(BaseModel):
    requirement: str
    item_type: str
    reason: str = Field(description="Why it's outside the scope of practice in India")


class RoleHistoryItem(BaseModel):
    title: str = Field(description="A past or current job title from the evidence")
    years: float | None
    soc_code: str
    occupation_title: str
    confidence: float
    applies_to_target: bool = Field(description="Gives the target's tasks implied partial credit")


class LaterItem(BaseModel):
    requirement: str
    item_type: str
    status: Status
    weight: float


class EvidenceVolumeOut(BaseModel):
    units: int = Field(description="Substantive evidence sentences or items")
    related_share: float = Field(description="Share of the core requirements with any related evidence")
    short: bool
    focus: float = Field(default=1.0, description="Share of the substantive units related to this occupation")
    other_role: str | None = Field(default=None, description="A past job title in an occupation not close to this one")


class FollowUpQuestionOut(BaseModel):
    requirement_id: str
    requirement: str
    item_type: str
    question: str


class FitRange(BaseModel):
    low: int
    high: int = Field(description="If the follow-up questions were all answered yes")


class CloseAlternative(BaseModel):
    soc_code: str
    title: str
    score: float
    source: Literal["related", "search"]
    message: str


class RoadmapItem(BaseModel):
    step: int
    requirement: str
    item_type: str
    status: Status
    provenance: Provenance
    weight: float
    implied_by_role: bool = Field(default=False, description="Only implied by a past title; listed after real gaps")
    prerequisites: list[str] = Field(default_factory=list, description="From the v1 taxonomy, not yet in your evidence")
    practice_ideas: list[str] = Field(default_factory=list, description="Nearest unmet O*NET tasks/DWAs to practise on")
    hours: HourRange = Field(description="Estimated range")
    weeks: HourRange | None = None


class GeneralRoadmap(BaseModel):
    items: list[RoadmapItem] = Field(description="Main roadmap, at most ROADMAP_MAX_ITEMS by weight")
    later: list[LaterItem] = Field(default_factory=list, description="The remaining gaps, for after the main roadmap")
    basics: list[LaterItem] = Field(default_factory=list,
                                    description="Generic office/productivity software (unless a market skill here)")
    total_hours: HourRange = Field(description="Main roadmap only")
    total_weeks: HourRange | None = None
    hours_per_week: float | None = None
    note: str


class ProvenanceSummary(BaseModel):
    scored_items: dict[str, int] = Field(description="Core requirements per provenance")
    weight_share: dict[str, float] = Field(description="Share of the scored weight per provenance")
    note: str


class GapAnalysisV2Response(BaseModel):
    resolution: RoleResolution
    match_score: float = Field(ge=0, le=1)
    fit_percent: int = Field(ge=0, le=100, description="User-facing: threshold -> 50, typical full profile -> 80")
    fit_label: str = Field(description="Strong fit / Good fit / Developing / Early stage")
    fit_provisional: bool = Field(default=False, description="True with insufficient_evidence: see fit_range")
    fit_range: FitRange | None = None
    verdict: Verdict
    evidence_volume: EvidenceVolumeOut
    follow_up_questions: list[FollowUpQuestionOut] = Field(
        default_factory=list, description="With insufficient_evidence: answer them in `answers` to re-score")
    score_breakdown: ScoreBreakdown
    strengths: list[RequirementResult]
    gaps: list[RequirementResult]
    gaps_total: int
    not_applicable_in_india: list[NotApplicableItem] = Field(
        default_factory=list, description="Excluded from scoring: outside the scope of practice in India")
    role_history: list[RoleHistoryItem] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list, description="Degree/institution lines (not task evidence)")
    draws_on: list[DrawsOnItem]
    work_activities: list[WorkActivityItem]
    fit_indicators: list[FitIndicatorItem]
    close_alternatives: list[CloseAlternative]
    roadmap: GeneralRoadmap
    provenance_summary: ProvenanceSummary
    m1_version: str
    warnings: list[str] = Field(default_factory=list)


class AnalyzeResumeV2Response(BaseModel):
    profile: UserProfile
    gap_analysis: GapAnalysisV2Response


class MatchTextRequest(EvidenceSources):
    """For module 3: one job's text against the user's evidence."""
    job_text: str = Field(min_length=20, max_length=MAX_TEXT_CHARS)
    soc_code: str | None = Field(default=None, pattern=SOC_PATTERN,
                                 description="The job's occupation; blends in its core requirements")
    job_title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> "MatchTextRequest":
        if not self.has_evidence():
            raise ValueError("give free_text, skills or profile")
        return self


class MatchTextResponse(BaseModel):
    match_score: float = Field(ge=0, le=1)
    job_text_score: float
    occupation_score: float | None
    blend: float = Field(description="Weight of the job text in match_score (JOB_TEXT_BLEND, or 1 without soc_code)")
    soc_code: str | None
    occupation_title: str | None
    met: list[RequirementResult]
    missing: list[RequirementResult]
    job_requirements: int
    m1_version: str | None
    warnings: list[str] = Field(default_factory=list)


EXPORTED_MODELS = [GapAnalysisV2Request, GapAnalysisV2Response, AnalyzeResumeV2Response, MatchTextRequest,
                   MatchTextResponse, ErrorResponse]


def export_json_schema() -> dict:
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(m, "validation") for m in EXPORTED_MODELS],
                                   title="Module 2 general engine (v2) contracts")
    return schema


if __name__ == "__main__":
    import json
    from pathlib import Path

    out = Path(__file__).resolve().parents[1] / "models" / "schema_m2_v2.json"
    out.write_text(json.dumps(export_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
