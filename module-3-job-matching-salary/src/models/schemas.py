"""Pydantic contracts for module 3 (job matching & salary).

Module 2's profile and module 1's market baseline arrive as plain data (no imports): the integration
layer passes them through. Field names follow context/MODULE-3-JOB-MATCHING-SALARY.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["adzuna", "jsearch"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "temporary", "unknown"]
WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
SkillsSource = Literal["m2", "unavailable", "none"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- jobs -------------------------------------------------------------------------------

class Job(BaseModel):
    """Every provider maps into this one shape (spec: Job Normalization)."""
    job_id: str = Field(description="'<source>:<provider id>'")
    title: str
    company: str | None = None
    description: str = Field(default="", description="As provided; Adzuna truncates to ~500 characters")
    description_quality: Literal["ok", "short", "missing"] = "ok"
    location: str | None = Field(default=None, description="Normalised city, e.g. 'Bengaluru'")
    location_raw: str | None = None
    employment_type: EmploymentType = "unknown"
    work_mode: WorkMode = "unknown"
    experience_min: float | None = Field(default=None, description="Years, parsed from title/description")
    experience_max: float | None = None
    skills: list[str] = Field(default_factory=list, description="Module 2 skill ids for this job")
    skills_source: SkillsSource = Field(default="none", description="'m2' = extracted by module 2; 'unavailable' = module 2 unreachable")
    salary_min: int | None = Field(default=None, description="INR per year")
    salary_max: int | None = None
    currency: Literal["INR"] = "INR"
    salary_is_predicted: bool = Field(default=False, description="Adzuna's own estimate, not the employer's figure")
    posted_at: datetime | None = None
    source: Source
    publisher: str | None = Field(default=None, description="Underlying site for JSearch results (LinkedIn, Naukri, ...)")
    source_url: str | None = None
    last_observed_at: datetime
    stale: bool = Field(default=False, description="Served from the snapshot after the cache TTL")
    age_hours: float | None = Field(default=None, description="Hours since last_observed_at, when stale")


class ProviderAttempt(BaseModel):
    provider: Literal["adzuna", "jsearch", "snapshot"]
    city: str
    status: Literal["ok", "cache_hit", "empty", "not_configured", "not_subscribed", "timeout", "error", "used", "missing"]
    detail: str | None = None
    jobs: int = 0


class FetchResult(BaseModel):
    role: str
    cities: list[str]
    jobs: list[Job]
    total_available: int | None = Field(default=None, description="Provider's total match count across cities, when known")
    sources: list[str] = Field(default_factory=list)
    from_cache: bool = False
    stale: bool = False
    attempts: list[ProviderAttempt] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- candidate input -----------------------------------------------------------------------

class CandidateSkill(BaseModel):
    """Module 2's skills[] entry (extra fields allowed and ignored)."""
    model_config = ConfigDict(extra="ignore")
    name: str = Field(description="Module 2 skill id, e.g. 'python'")
    level: int = Field(default=1, ge=0, le=5)
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    maps_to: str | None = None


class CandidateProfile(BaseModel):
    """Module 2's UserProfile, the fields module 3 uses (see module-2-skill-gap/HANDOFF_TO_M3.md)."""
    model_config = ConfigDict(extra="ignore")
    skills: list[CandidateSkill] = Field(default_factory=list)
    experience_years: float = Field(default=0.0, ge=0.0, le=60.0)
    education: list[str] = Field(default_factory=list)
    location: str | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    target_occupation: str | None = None


class GapSummary(BaseModel):
    """Optional slice of module 2's GapAnalysisResult."""
    model_config = ConfigDict(extra="ignore")
    match_score: float | None = None
    verdict: str | None = None
    learning_priorities: list[str] = Field(default_factory=list)
    critical_missing: list[str] = Field(default_factory=list)


class MarketBaseline(BaseModel):
    """Optional: module 1's regional_breakdown.india, passed through by integration."""
    model_config = ConfigDict(extra="ignore")
    median_salary_inr_lpa: float | None = None
    top_locations: list[str] = Field(default_factory=list)
    top_skills: list[str] = Field(default_factory=list)
    posting_volume: int | None = None


class SalaryPreference(BaseModel):
    min_lpa: float | None = Field(default=None, ge=0)
    max_lpa: float | None = Field(default=None, ge=0)


class JobSearchRequest(BaseModel):
    """Spec 'Job Search Inputs'. Send module 2's `profile`, or the minimal manual fields."""
    target_role: str = Field(min_length=1, max_length=120)
    profile: CandidateProfile | None = None
    skills: list[str] = Field(default_factory=list, description="Manual input: skill ids or names")
    experience_years: float | None = Field(default=None, ge=0.0, le=60.0)
    education: list[str] = Field(default_factory=list)
    location: str | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    work_mode: list[WorkMode] = Field(default_factory=list, description="Accepted modes; empty = any")
    salary_preference: SalaryPreference | None = None
    employment_type: list[EmploymentType] = Field(default_factory=list, description="Accepted types; empty = any")
    gap_analysis: GapSummary | None = None
    market_baseline: MarketBaseline | None = None

    @model_validator(mode="after")
    def _has_location(self) -> "JobSearchRequest":
        if not (self.location or self.preferred_locations or (self.profile and self.profile.location)):
            raise ValueError("give a location, preferred_locations, or a profile with a location")
        return self

    def candidate(self) -> CandidateProfile:
        """One candidate shape, whether module 2's profile or manual fields were sent."""
        if self.profile is not None:
            return self.profile
        return CandidateProfile(skills=[CandidateSkill(name=s) for s in self.skills],
                                experience_years=self.experience_years or 0.0, education=self.education,
                                location=self.location, preferred_locations=self.preferred_locations,
                                target_occupation=self.target_role)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    module: str
    version: str
    providers: dict[str, bool] = Field(description="Whether each provider's key is configured")
    m2_base_url: str
    cache: dict[str, int]
