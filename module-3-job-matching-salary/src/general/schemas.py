"""v2 contracts: POST /api/v2/jobs/search (any occupation). Exported to src/models/schema_m3_v2.json
(python -m src.general.schemas); a test checks it doesn't drift."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.models.schemas import ErrorResponse, ProviderAttempt

SOC_PATTERN = r"^\d{2}-\d{4}\.\d{2}$"
MAX_TEXT_CHARS = 50_000

RelevanceLabel = Literal["on_target", "adjacent", "off_target", "unknown"]
Classification = Literal["Strong", "Good", "Partial", "Weak"]


class JobSearchV2Request(BaseModel):
    target_role: str | None = Field(default=None, min_length=2, max_length=120,
                                    description="Free-text role, e.g. 'staff nurse'; resolved through module 1")
    soc_code: str | None = Field(default=None, pattern=SOC_PATTERN, description="O*NET-SOC code, e.g. 47-2111.00")
    location: str | None = Field(default=None, max_length=60, description="City or region, e.g. 'Delhi NCR', 'Pune'")
    preferred_locations: list[str] = Field(default_factory=list, max_length=10)
    free_text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS,
                                  description="Resume text or a description of the user's work, any style")
    skills: list[str] = Field(default_factory=list, max_length=200)
    profile: dict | None = Field(default=None, description="Module 2's UserProfile, passed to module 2 unchanged")
    experience_years: float | None = Field(default=None, ge=0, le=60)
    max_jobs: int = Field(default=50, ge=1, le=100, description="Listings matched and returned (relevant first)")
    force_refresh: bool = Field(default=False, description="Ignore the cache and call the providers")
    use_jsearch: bool = Field(default=True, description="Allow JSearch when Adzuna fails (200 calls/month)")
    include_dropped: bool = Field(default=False, description="List the off-target listings that were dropped")

    @model_validator(mode="after")
    def _check(self) -> "JobSearchV2Request":
        if not (self.target_role or self.soc_code):
            raise ValueError("give target_role or soc_code")
        if not ((self.free_text or "").strip() or [s for s in self.skills if s.strip()] or self.profile):
            raise ValueError("give free_text, skills or profile")
        if not (self.location or (self.profile or {}).get("location")):
            raise ValueError("give location (or a profile with one)")
        return self


class RoleOptionV2(BaseModel):
    soc_code: str
    title: str
    confidence: float


class RoleResolutionV2(BaseModel):
    query: str | None
    soc_code: str | None
    title: str | None
    confidence: float
    low_confidence: bool
    did_you_mean: list[RoleOptionV2] = Field(default_factory=list)


class ListingRelevance(BaseModel):
    label: RelevanceLabel
    soc_code: str | None = Field(description="The listing's occupation when module 1 resolved its title confidently")
    occupation_title: str | None = None
    confidence: float
    reason: str


class RequirementGap(BaseModel):
    requirement: str
    requirement_id: str
    item_type: str
    status: str
    score_gain_if_met: float | None = Field(description="How much this job's match_score would rise if it were met")


class JobMatchV2(BaseModel):
    method: Literal["m2_match_texts", "keywords"]
    match_score: float = Field(ge=0, le=1)
    classification: Classification
    job_text_score: float | None = None
    occupation_score: float | None = None
    soc_code: str | None = Field(description="Occupation blended into the match (the listing's, else the target's)")
    match_confidence: Literal["ok", "low"] = Field(description="low: description too short for a reliable match")
    job_requirements: int = 0
    met: list[str] = Field(default_factory=list, description="Top requirements the user's evidence meets")
    missing: list[RequirementGap] = Field(default_factory=list, description="Top missing/partial, by score gain")


class ExperienceFit(BaseModel):
    band: tuple[float, float] | None
    source: Literal["posting", "india_postings", "job_zone", "unknown"]
    candidate_years: float | None
    fit: float


class JobV2Result(BaseModel):
    job_id: str
    title: str
    company: str | None
    location: str | None
    work_mode: str
    employment_type: str
    posted_at: datetime | None
    source: str
    source_url: str | None
    description: str = Field(description="As provided (Adzuna truncates to ~500 characters)")
    salary_min: int | None
    salary_max: int | None
    salary_is_predicted: bool
    relevance: ListingRelevance
    match: JobMatchV2
    experience: ExperienceFit
    rank: int
    rank_score: float
    rank_components: dict[str, float]
    stale: bool = False


class DroppedListing(BaseModel):
    job_id: str
    title: str
    reason: str


class RelevanceSummary(BaseModel):
    listings: int = Field(description="After dedupe and expiry")
    on_target: int
    adjacent: int
    off_target_dropped: int
    unknown: int = 0
    on_target_share: float
    beyond_max_jobs: int = Field(default=0, description="Relevant listings not matched because of max_jobs")


class UnlockV2(BaseModel):
    requirement: str
    requirement_ids: list[str] = Field(description="Grouped near-identical requirements")
    item_type: str
    jobs_unlocked: int = Field(description="Listings that would move to Good or Strong if this were met")
    job_ids: list[str]
    message: str


class JobSearchV2Response(BaseModel):
    resolution: RoleResolutionV2
    queries: list[str]
    cities: list[str]
    jobs: list[JobV2Result]
    relevance_summary: RelevanceSummary
    dropped: list[DroppedListing] = Field(default_factory=list, description="Off-target listings (include_dropped)")
    unlocks: list[UnlockV2]
    thresholds: dict[str, float] = Field(description="match_score floors for Strong / Good / Partial")
    experience_band: tuple[float, float] | None = None
    experience_band_source: str = "unknown"
    provider_trace: list[ProviderAttempt]
    sources: list[str]
    stale: bool
    fetched_at: datetime | None = None
    age_hours: float | None = None
    module_1_available: bool
    module_2_available: bool
    warnings: list[str] = Field(default_factory=list)


EXPORTED_MODELS = [JobSearchV2Request, JobSearchV2Response, ErrorResponse]


def export_json_schema() -> dict:
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(m, "validation") for m in EXPORTED_MODELS],
                                   title="Module 3 general engine (v2) contracts")
    return schema


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "models" / "schema_m3_v2.json"
    out.write_text(json.dumps(export_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
