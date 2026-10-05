"""
Pydantic Data Models & Schemas for Module 1 (Career Intelligence Engine).
Complies with INTEGRATION.md and MODULE-1-CAREER-INTELLIGENCE.md specifications.
Enhanced with multi-year historical + 5-year forecast trajectory and knowledge graph schemas.
"""
from typing import List, Dict, Optional, Any, Literal
from pydantic import BaseModel, Field


class CareerAnalysisRequest(BaseModel):
    occupation: str = Field(..., description="Target occupation title (e.g. 'Data Engineer', 'Software Developer')")
    region: Literal["india", "global", "all"] = Field("all", description="Geographic scope: 'india', 'global', or 'all'")


class TaskExposureDetail(BaseModel):
    task_description: str
    ai_impact_score: float
    transformation_type: str  # 'Direct Automation', 'AI Augmentation', 'Human-Centric / High Discretion'
    rationale: str


class RegionMetricDetail(BaseModel):
    region: str
    posting_volume: int
    posting_growth_yoy_pct: float
    median_salary_usd: Optional[float] = None
    median_salary_inr_lpa: Optional[float] = None
    top_locations: List[str] = []
    top_skills: List[str] = []

class SalaryPercentileBand(BaseModel):
    p25: float = Field(..., description="25th percentile salary")
    p50: float = Field(..., description="50th percentile (median) salary")
    p75: float = Field(..., description="75th percentile salary")
    currency: str = Field(..., description="'INR_LPA' or 'USD'")
    sample_size: int = Field(..., description="Count of empirical salary observations")


class MarketSalaryPercentiles(BaseModel):
    overall_inr_lpa: Optional[SalaryPercentileBand] = Field(None, description="On-site and hybrid India salary percentiles (excluding pure remote)")
    remote_inr_lpa: Optional[SalaryPercentileBand] = Field(None, description="Pure remote India salary percentiles")
    overall_usd: Optional[SalaryPercentileBand] = None
    by_experience_inr_lpa: Dict[str, SalaryPercentileBand] = Field(default_factory=dict, description="Percentiles by tier for on-site/hybrid: 'entry' (0-2y), 'mid' (3-5y), 'senior' (5+y)")
    by_city_inr_lpa: Dict[str, SalaryPercentileBand] = Field(default_factory=dict, description="Percentiles by metro: 'Bengaluru', 'Hyderabad', 'Pune', 'Mumbai', 'Delhi NCR'")


class YearlyDataPoint(BaseModel):
    year: int
    status: Literal["historical", "forecast"]
    india_index: float = Field(..., description="Normalized demand index for India (0-100 base)")
    global_index: float = Field(..., description="Normalized demand index for Global (0-100 base)")
    india_lower_bound: float
    india_upper_bound: float
    global_lower_bound: float
    global_upper_bound: float


class YearlyTrajectory(BaseModel):
    historical_years: List[int]
    forecast_years: List[int]
    cutoff_year: int
    series: List[YearlyDataPoint]


class GraphNode(BaseModel):
    id: str
    label: str
    type: Literal["occupation", "task", "skill", "technology", "domain"]
    weight: float
    metadata: Optional[Dict[str, Any]] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    relationship: str
    weight: float


class KnowledgeGraph(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class CareerAnalysisResponse(BaseModel):
    occupation: str = Field(..., description="Target occupation name")
    soc_code: Optional[str] = Field(None, description="Standard O*NET SOC code if mapped")
    current_demand_score: float = Field(..., ge=0.0, le=1.0, description="Normalized current market demand (0-1)")
    growth_score: float = Field(..., ge=0.0, le=1.0, description="Projected 5-year growth trajectory score (0-1)")
    ai_exposure_score: float = Field(..., ge=0.0, le=1.0, description="AI automation/transformation exposure score (0-1)")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Statistical confidence in predictions (0-1)")
    outlook: str = Field(..., description="Summary outlook (e.g. 'Strong Growth', 'Moderate Growth', 'Stable', 'Transforming')")
    top_skills: List[str] = Field(default_factory=list, description="Top skills in demand for this occupation")
    top_skill_weights: Dict[str, float] = Field(default_factory=dict, description="Normalized demand weights (0.0-1.0) for top skills based on posting frequency")
    drivers: List[str] = Field(default_factory=list, description="Supporting evidence drivers explaining the outlook")
    tasks_analyzed: int = Field(0, description="Count of granular O*NET tasks evaluated")
    sample_tasks: List[TaskExposureDetail] = Field(default_factory=list, description="Sample task transformation breakdown")
    regional_breakdown: Dict[str, RegionMetricDetail] = Field(default_factory=dict, description="Side-by-side India vs Global metrics")
    typical_experience: Dict[str, float] = Field(default_factory=dict, description="Typical experience years band {min, max} derived from empirical postings")
    market_salary_percentiles: Optional[MarketSalaryPercentiles] = Field(None, description="Empirical salary percentiles (p25, p50, p75) by experience tier and top metros")
    yearly_trajectory: Optional[YearlyTrajectory] = Field(None, description="Past year-wise trend + 5-year forecast points")
    knowledge_graph: Optional[KnowledgeGraph] = Field(None, description="Career knowledge graph (nodes and edges)")


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


class DomainSearchRequest(BaseModel):
    domain_query: str = Field(..., min_length=2, description="User interest domain or keywords (e.g. 'Artificial Intelligence', 'FinTech', 'Cloud Cybersecurity')")
    top_k: int = Field(6, ge=1, le=25, description="Number of relevant career pathways to return")


class DomainSearchItem(BaseModel):
    occupation: str
    soc_code: str
    domain: str
    relevance_score: float
    matching_skills: List[str]
    outlook: str
    growth_score: float


class DomainSearchResponse(BaseModel):
    domain_query: str
    results: List[DomainSearchItem]
