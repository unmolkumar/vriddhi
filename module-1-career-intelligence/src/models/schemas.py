"""
Pydantic Data Models & Schemas for Module 1 (Career Intelligence Engine).
Complies with INTEGRATION.md and MODULE-1-CAREER-INTELLIGENCE.md specifications.
"""
from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field


class CareerAnalysisRequest(BaseModel):
    occupation: str = Field(..., description="Target occupation title (e.g. 'Data Engineer', 'Software Developer')")
    region: Literal["india", "global", "all"] = Field("all", description="Geographic scope: 'india', 'global', or 'all'")


class TaskExposureDetail(BaseModel):
    task_description: str
    ai_impact_score: float
    transformation_type: str  # e.g., 'Augmentation', 'Automation', 'Human-Centric'
    rationale: str


class RegionMetricDetail(BaseModel):
    region: str
    posting_volume: int
    posting_growth_yoy_pct: float
    median_salary_usd: Optional[float] = None
    median_salary_inr_lpa: Optional[float] = None
    top_locations: List[str] = []
    top_skills: List[str] = []


class CareerAnalysisResponse(BaseModel):
    occupation: str = Field(..., description="Target occupation name")
    soc_code: Optional[str] = Field(None, description="Standard O*NET SOC code if mapped")
    current_demand_score: float = Field(..., ge=0.0, le=1.0, description="Normalized current market demand (0-1)")
    growth_score: float = Field(..., ge=0.0, le=1.0, description="Projected 5-year growth trajectory score (0-1)")
    ai_exposure_score: float = Field(..., ge=0.0, le=1.0, description="AI automation/transformation exposure score (0-1)")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Statistical confidence in predictions (0-1)")
    outlook: str = Field(..., description="Summary outlook (e.g. 'Strong Growth', 'Moderate Growth', 'Stable', 'Transforming')")
    top_skills: List[str] = Field(default_factory=list, description="Top skills in demand for this occupation")
    drivers: List[str] = Field(default_factory=list, description="Supporting evidence drivers explaining the outlook")
    tasks_analyzed: int = Field(0, description="Count of granular O*NET tasks evaluated")
    sample_tasks: List[TaskExposureDetail] = Field(default_factory=list, description="Sample task transformation breakdown")
    regional_breakdown: Dict[str, RegionMetricDetail] = Field(default_factory=dict, description="Side-by-side India vs Global metrics")


class RankingWeightConfig(BaseModel):
    current_demand: float = Field(0.30, ge=0.0, le=1.0)
    growth: float = Field(0.30, ge=0.0, le=1.0)
    ai_resilience: float = Field(0.20, ge=0.0, le=1.0)
    salary_level: float = Field(0.10, ge=0.0, le=1.0)
    confidence: float = Field(0.10, ge=0.0, le=1.0)


class CareerRankRequest(BaseModel):
    occupations: List[str] = Field(..., min_length=1, description="List of occupations to rank")
    weights: Optional[Dict[str, float]] = Field(None, description="Custom scoring weights (keys: current_demand, growth, ai_resilience, salary_level, confidence)")
    region: Literal["india", "global", "all"] = Field("all", description="Geographic scope for ranking")
    top_k: Optional[int] = Field(None, description="Limit return to top K ranked careers")


class RankedCareerItem(BaseModel):
    rank: int
    occupation: str
    composite_score: float
    current_demand_score: float
    growth_score: float
    ai_exposure_score: float
    confidence_score: float
    outlook: str
    top_skills: List[str]
    primary_driver: str


class CareerRankResponse(BaseModel):
    rankings: List[RankedCareerItem]
    weights_applied: Dict[str, float]
    total_evaluated: int
