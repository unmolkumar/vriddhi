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
SkillsSource = Literal["m2", "jsearch_full", "unavailable", "none"]
ExperienceSource = Literal["posting", "request", "title_heuristic", "unknown"]
Classification = Literal["Strong", "Good", "Partial", "Weak"]


MANUAL_SKILL_LEVEL = 2


def slug(name: str) -> str:
    """'Power BI' -> 'power_bi', the id style modules 1 and 2 use. Module 2's profile already has ids."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


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
    skills_source: SkillsSource = Field(default="none", description=(
        "'m2' = extracted by module 2 from this listing; 'jsearch_full' = taken from the same job's full JSearch "
        "description; 'unavailable' = module 2 unreachable"))
    skill_parents: dict[str, str] = Field(default_factory=dict, description="Coarser id per skill, from module 2 (postgresql -> sql)")
    skill_display: dict[str, str] = Field(default_factory=dict, description="Display name per skill id, from module 2 (pytorch -> PyTorch)")
    skill_is_category: dict[str, bool] = Field(
        default_factory=dict, description="Module 2's is_category per skill id; empty when module 2 didn't send it")
    skills_inferred: bool = Field(default=False, description="Some skills were filled from the role market profile")
    inferred_skills: list[str] = Field(default_factory=list, description="Which skills were inferred (also listed in skills)")
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
    needs_verification: bool = Field(default=False, description="Module 2: claimed level not backed by the resume")


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


class ExperienceBand(BaseModel):
    min: float = Field(ge=0.0, le=60.0)
    max: float = Field(ge=0.0, le=60.0)

    @model_validator(mode="after")
    def _ordered(self) -> "ExperienceBand":
        if self.max < self.min:
            raise ValueError("max must be >= min")
        return self


class PercentileBand(BaseModel):
    """One of module 1's salary percentile bands, in INR lakh per annum (LPA)."""
    model_config = ConfigDict(extra="ignore")
    p25: float = Field(gt=0)
    p50: float = Field(gt=0)
    p75: float = Field(gt=0)
    sample_size: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "PercentileBand":
        if not self.p25 <= self.p50 <= self.p75:
            raise ValueError("percentiles must satisfy p25 <= p50 <= p75")
        return self


class MarketPercentiles(BaseModel):
    """Module 1's market_salary_percentiles object, as POST /api/v1/career/analyze returns it (values in LPA).
    overall_usd is accepted and ignored. Older module 1 sends no remote_inr_lpa (remote mixed into overall)."""
    model_config = ConfigDict(extra="ignore")
    overall_inr_lpa: PercentileBand | None = Field(default=None, description="On-site and hybrid (all roles in older module 1)")
    remote_inr_lpa: PercentileBand | None = Field(
        default=None, description="Pure remote roles; used only when the request accepts remote work only")
    by_experience_inr_lpa: dict[str, PercentileBand] = Field(
        default_factory=dict, description="'entry' (<3 years), 'mid' (3-5), 'senior' (>5)")
    by_city_inr_lpa: dict[str, PercentileBand] = Field(
        default_factory=dict, description="'Bengaluru', 'Hyderabad', 'Pune', 'Mumbai', 'Delhi NCR'")


class SourceCheck(BaseModel):
    """Module 1's percentiles against JSearch's estimate, when both were available (INR per year)."""
    module_1_p25: int
    module_1_median: int
    module_1_p75: int
    module_1_sample_size: int
    jsearch_min: int
    jsearch_median: int
    jsearch_max: int
    jsearch_sample_size: int
    gap_pct: float = Field(description="(JSearch - module 1) / module 1 x 100")
    agree: bool = Field(description="Within SALARY_SOURCE_TOLERANCE")
    primary: Literal["module_1", "jsearch"] = Field(description="The source the estimate uses")
    rule: str = Field(description="'module_1_agrees' or 'PREFER_LARGER_INDIA_SAMPLE'")


class MatchWeights(BaseModel):
    """Overrides for the spec's starting weights; normalised to sum to 1."""
    skills: float = Field(default=0.40, ge=0)
    experience: float = Field(default=0.20, ge=0)
    education: float = Field(default=0.10, ge=0)
    location: float = Field(default=0.10, ge=0)
    seniority: float = Field(default=0.10, ge=0)
    preference: float = Field(default=0.10, ge=0)

    @model_validator(mode="after")
    def _positive(self) -> "MatchWeights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("weights must not all be zero")
        return self

    def normalised(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}


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
    typical_experience: ExperienceBand | None = Field(
        default=None, description="Role's usual experience band (module 1); used for jobs that don't state one")
    weights: MatchWeights | None = Field(default=None, description="Optional match weight overrides")
    market_salary_percentiles: MarketPercentiles | None = Field(
        default=None, description="Module 1's salary percentiles; the primary market source when the sample is big enough")
    jsearch_salary: bool = Field(
        default=False, description="Also fetch JSearch's salary estimate when not cached (uses the 200/month JSearch quota)")
    jsearch_enrichment: bool = Field(
        default=False, description="Also query JSearch for full descriptions (uses the 200/month JSearch quota)")
    limit: int = Field(default=20, ge=1, le=100, description="Jobs to return after ranking")

    @model_validator(mode="after")
    def _has_location(self) -> "JobSearchRequest":
        if not (self.location or self.preferred_locations or (self.profile and self.profile.location)):
            raise ValueError("give a location, preferred_locations, or a profile with a location")
        return self

    def candidate(self) -> CandidateProfile:
        """One candidate shape, whether module 2's profile or manual fields were sent."""
        if self.profile is not None:
            return self.profile
        # Typed skills have no resume evidence: treated as level 2 ("basic") for matching.
        return CandidateProfile(skills=[CandidateSkill(name=slug(s), level=MANUAL_SKILL_LEVEL) for s in self.skills],
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


# --- search results (step 2) ------------------------------------------------------------

class SalaryRange(BaseModel):
    min: int | None = None
    max: int | None = None
    currency: Literal["INR"] = "INR"
    is_predicted: bool = Field(default=False, description="Adzuna's own estimate, not the employer's figure")


class MatchBreakdown(BaseModel):
    skills: float
    experience: float
    education: float
    location: float
    seniority: float
    preference: float
    weights: dict[str, float]
    skill_method: Literal["skills", "keywords"] = Field(description="'keywords' when the job has no extracted skills")


class JobResult(BaseModel):
    job_id: str
    title: str
    company: str | None
    location: str | None
    work_mode: WorkMode
    employment_type: EmploymentType
    posted_at: datetime | None
    source: str
    publisher: str | None
    source_url: str | None
    stale: bool
    fetched_at: datetime = Field(description="When this listing was fetched from its provider")
    age_hours: float = Field(description="Hours since fetched_at")
    salary: SalaryRange
    match_score: int = Field(ge=0, le=100, description="Overall match, percent")
    classification: Classification
    match: MatchBreakdown
    matched_skills: list[str]
    missing_skills: list[str]
    inferred_skills: list[str] = Field(default_factory=list, description="Job skills filled from the market profile")
    skills_source: SkillsSource
    skills_confidence: Literal["extracted", "partly_inferred", "inferred"] = Field(
        description="How much of this job's skill list came from the listing itself")
    skills_note: str | None = Field(default=None, description="e.g. 'Skills inferred from similar Data Scientist jobs in Bengaluru'")
    experience_required: ExperienceBand | None = None
    experience_source: ExperienceSource
    rank: int
    rank_score: float
    rank_components: dict[str, float]


class ProfileSkill(BaseModel):
    skill: str
    share: float = Field(description="Share of fetched jobs (with extracted skills) that ask for it")
    jobs: int


class RoleMarketProfile(BaseModel):
    role: str
    cities: list[str]
    jobs_analysed: int = Field(description="Jobs whose skills were extracted (not inferred)")
    top_skills: list[ProfileSkill]


class SalaryEstimate(BaseModel):
    estimated_min: int | None
    estimated_median: int | None
    estimated_max: int | None
    currency: Literal["INR"] = "INR"
    confidence: float = Field(ge=0.0, le=1.0)
    sample_size: int
    sources_used: list[str]
    excluded: dict[str, int] = Field(default_factory=dict, description="Salaries left out and why")
    method: Literal["experience_bucket", "experience_x_city_ratio", "overall", "remote"] | None = Field(
        default=None, description="How module 1's percentiles were applied, when they were used")
    source_check: SourceCheck | None = Field(
        default=None, description="Module 1 vs JSearch medians, when both were available")
    display: str | None = Field(default=None, description="e.g. 'Estimated market range: 12-18 LPA'")
    note: str


class CandidateValue(BaseModel):
    estimated_min: int | None
    estimated_max: int | None
    currency: Literal["INR"] = "INR"
    confidence: float
    adjustment: float = Field(description="Match multiplier applied to the candidate's position in the market range")
    market_position: float | None = Field(default=None, description="0 = bottom of the market range, 1 = top; from experience within the band")
    display: str | None = None
    reasons: list[str]


class Negotiation(BaseModel):
    job_id: str | None
    posted_salary: str | None
    market_range: str | None
    candidate_range: str | None
    recommended_target: str | None
    reasonable_minimum: str | None
    recommended_target_inr: int | None
    reasonable_minimum_inr: int | None
    confidence: float
    reasons: list[str]
    note: str


class SkillUnlock(BaseModel):
    skill: str
    jobs_unlocked: int = Field(description="Fetched jobs that would move to Good or Strong with this skill")
    city: str | None
    example_job_ids: list[str] = Field(default_factory=list)
    message: str


class JobSearchResponse(BaseModel):
    target_role: str
    location: str | None
    cities: list[str]
    total_found: int = Field(description="Jobs considered after dedupe and filters")
    total_available: int | None = Field(description="Provider's total match count, when known")
    jobs: list[JobResult]
    role_market_profile: RoleMarketProfile
    market_salary: SalaryEstimate
    candidate_value: CandidateValue
    negotiation: Negotiation | None = Field(description="For the top-ranked job")
    skill_unlocks: list[SkillUnlock]
    provider_trace: list[ProviderAttempt]
    sources: list[str]
    stale: bool
    fetched_at: datetime | None = Field(description="When the oldest returned listing was fetched")
    age_hours: float | None = Field(description="Hours since fetched_at")
    data_age: str | None = Field(description="e.g. 'fetched 10 h ago'")
    warnings: list[str]


class SalaryEstimateRequest(JobSearchRequest):
    """Same inputs as a search; returns the market and candidate estimates only."""


class SalaryEstimateResponse(BaseModel):
    target_role: str
    cities: list[str]
    market_salary: SalaryEstimate
    candidate_value: CandidateValue
    warnings: list[str]


class NegotiateRequest(JobSearchRequest):
    """Search inputs plus the offer to negotiate: a cached job_id, or a posted salary."""
    job_id: str | None = None
    posted_salary_min: int | None = Field(default=None, ge=0)
    posted_salary_max: int | None = Field(default=None, ge=0)


class NegotiateResponse(BaseModel):
    target_role: str
    negotiation: Negotiation
    market_salary: SalaryEstimate
    candidate_value: CandidateValue
    match_score: int | None
    warnings: list[str]


EXPORTED_MODELS = [Job, JobSearchRequest, JobSearchResponse, SalaryEstimateRequest, SalaryEstimateResponse,
                   NegotiateRequest, NegotiateResponse, HealthResponse, ErrorResponse]


def export_json_schema() -> dict:
    """JSON Schema for module 3's public models (written to src/models/schema_m3.json)."""
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(m, "validation") for m in EXPORTED_MODELS], title="Module 3 Job Matching & Salary contracts")
    return schema


if __name__ == "__main__":
    import json
    from pathlib import Path

    out = Path(__file__).with_name("schema_m3.json")
    out.write_text(json.dumps(export_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
