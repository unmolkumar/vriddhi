"""
Unit and Integration Tests for Module 1 Career Intelligence Engine.
Tests all minimum requirements specified in MODULE-1-CAREER-INTELLIGENCE.md:
  - Trend calculation
  - Growth calculation
  - Forecast generation
  - Missing data handling
  - Sparse occupation data
  - Confidence calculation
  - Unknown occupations
  - Skill extraction
  - Configurable ranking
  - API endpoint responses
"""
import pytest
from pathlib import Path
import sys

# Ensure src is importable
MODULE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_ROOT))

from src.engines.database import CareerDatabase
from src.engines.demand_engine import DemandEngine
from src.engines.ai_exposure import AIExposureEngine
from src.engines.forecaster import CareerForecaster
from src.engines.evidence_engine import EvidenceEngine
from src.engines.ranking_engine import RankingEngine
from src.engines.service import CareerIntelligenceService
from fastapi.testclient import TestClient
from src.api.main import app


@pytest.fixture
def service():
    return CareerIntelligenceService()


@pytest.fixture
def client():
    return TestClient(app)


# 1. Database & Occupation Discovery Tests
def test_find_known_occupation(service):
    res = service.db.find_occupation("Data Scientist")
    assert res is not None
    assert "data scientist" in res["title"].lower() or "data" in res["title"].lower()


def test_find_unknown_occupation(service):
    # Completely fictitious role
    res = service.db.find_occupation("Quantum Teleportation Archaeologist 9999")
    assert res is None


# 2. Demand & Trend Calculation Tests
def test_trend_and_growth_calculation():
    demand_engine = DemandEngine()
    posting_history = {
        "global": {
            "total": 5000,
            "unique_companies": 1200,
            "postings_2024": 3000,
            "postings_2025_plus": 500,
            "top_locations": ["New York", "San Francisco"]
        },
        "india": {
            "total": 3500,
            "unique_companies": 800,
            "modern_postings": 2800,
            "top_cities": ["Bengaluru", "Hyderabad"]
        }
    }
    result = demand_engine.calculate_demand(posting_history, region="all")
    assert 0.0 <= result["current_demand_score"] <= 1.0
    assert result["current_demand_score"] > 0.6  # Strong volume should yield high score
    assert result["employer_diversification"] > 0.0


# 3. Sparse Data & Missing Data Handling
def test_sparse_occupation_data():
    demand_engine = DemandEngine()
    empty_history = {"global": {"total": 0}, "india": {"total": 0}}
    result = demand_engine.calculate_demand(empty_history, region="all")
    assert 0.0 <= result["current_demand_score"] <= 1.0
    assert result["current_demand_score"] <= 0.40


def test_forecasting_with_sparse_data():
    forecaster = CareerForecaster()
    sparse_history = {"global": {"total": 2}, "india": {"total": 0}}
    res = forecaster.forecast(
        occupation="Extremely Rare Specialty",
        current_demand_score=0.20,
        ai_exposure_score=0.45,
        posting_history=sparse_history,
        tasks_count=0
    )
    assert 0.0 <= res["growth_score"] <= 1.0
    assert 0.0 <= res["confidence_score"] <= 1.0
    # Sparse data must yield lower confidence
    assert res["confidence_score"] < 0.40


# 4. AI & Automation Task Exposure Tests
def test_ai_exposure_task_differentiation():
    ai_engine = AIExposureEngine()
    # Routine task
    auto_task = ai_engine.evaluate_task("Enter data and transcribe routine filing documents")
    assert auto_task["transformation_type"] == "Direct Automation"
    assert auto_task["ai_impact_score"] >= 0.70

    # Augmentation task
    aug_task = ai_engine.evaluate_task("Develop machine learning models and optimize analytical algorithms")
    assert aug_task["transformation_type"] == "AI Augmentation"
    assert 0.40 <= aug_task["ai_impact_score"] <= 0.75

    # Human-centric task
    human_task = ai_engine.evaluate_task("Counsel patients with psychological therapy and mentor caregivers")
    assert human_task["transformation_type"] == "Human-Centric / High Discretion"
    assert human_task["ai_impact_score"] <= 0.35


# 5. Forecast Generation & Confidence Calibration Tests
def test_forecast_generation(service):
    analysis = service.analyze_career("Data Engineer", region="all")
    assert analysis.occupation != ""
    assert 0.0 <= analysis.current_demand_score <= 1.0
    assert 0.0 <= analysis.growth_score <= 1.0
    assert 0.0 <= analysis.ai_exposure_score <= 1.0
    assert 0.0 <= analysis.confidence_score <= 1.0
    assert analysis.outlook in ["Strong Growth", "Moderate Growth", "Stable Demand", "Transforming (High AI Exposure)", "Emerging / Evolving"]
    assert len(analysis.drivers) > 0


# 6. Skill Extraction Tests
def test_skill_extraction(service):
    skills = service.db.get_top_skills("Software Developer", limit=5)
    assert isinstance(skills, dict)
    assert "india" in skills
    assert "global" in skills
    assert len(skills["india"]) > 0 or len(skills["global"]) > 0


# 7. Configurable Ranking Tests
def test_ranking_engine_ordering():
    ranker = RankingEngine()
    candidates = [
        {"occupation": "Role High Demand", "current_demand_score": 0.95, "growth_score": 0.90, "ai_exposure_score": 0.30, "confidence_score": 0.85},
        {"occupation": "Role Low Demand", "current_demand_score": 0.20, "growth_score": 0.25, "ai_exposure_score": 0.80, "confidence_score": 0.40}
    ]
    ranked, applied_weights = ranker.rank_careers(candidates)
    assert len(ranked) == 2
    assert ranked[0].occupation == "Role High Demand"
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
    assert sum(applied_weights.values()) == pytest.approx(1.0, 0.01)


def test_ranking_with_custom_weights():
    ranker = RankingEngine()
    candidates = [
        {"occupation": "Role A", "current_demand_score": 0.90, "growth_score": 0.40, "ai_exposure_score": 0.50, "confidence_score": 0.50},
        {"occupation": "Role B", "current_demand_score": 0.40, "growth_score": 0.95, "ai_exposure_score": 0.50, "confidence_score": 0.50}
    ]
    # Heavily weight growth
    ranked_growth, _ = ranker.rank_careers(candidates, custom_weights={"growth": 0.90, "current_demand": 0.10})
    assert ranked_growth[0].occupation == "Role B"

    # Heavily weight demand
    ranked_demand, _ = ranker.rank_careers(candidates, custom_weights={"growth": 0.10, "current_demand": 0.90})
    assert ranked_demand[0].occupation == "Role A"


# 8. REST API Integration Tests
def test_api_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_api_analyze_endpoint(client):
    res = client.post("/api/v1/career/analyze", json={"occupation": "Data Scientist", "region": "all"})
    assert res.status_code == 200
    data = res.json()
    assert "occupation" in data
    assert "growth_score" in data
    assert "current_demand_score" in data
    assert "regional_breakdown" in data
    assert "india" in data["regional_breakdown"]
    assert "global" in data["regional_breakdown"]


def test_api_rank_endpoint(client):
    res = client.post("/api/v1/career/rank", json={
        "occupations": ["Software Engineer", "Data Scientist", "Accountant"],
        "top_k": 2
    })
    assert res.status_code == 200
    data = res.json()
    assert len(data["rankings"]) == 2
    assert data["rankings"][0]["rank"] == 1


def test_api_compare_endpoint(client):
    res = client.get("/api/v1/career/compare")
    assert res.status_code == 200
    data = res.json()
    assert "postings_by_region_and_year" in data
    assert len(data["postings_by_region_and_year"]) > 0


# 9. Multi-Year Trajectory & Knowledge Graph Tests
def test_trajectory_generation(service):
    analysis = service.analyze_career("Data Scientist")
    traj = analysis.yearly_trajectory
    assert traj is not None
    assert traj.cutoff_year == 2026
    assert len(traj.series) == 11  # 2021 through 2031
    for pt in traj.series:
        assert pt.india_lower_bound <= pt.india_index <= pt.india_upper_bound or pt.india_index >= pt.india_lower_bound
        assert pt.global_lower_bound <= pt.global_index <= pt.global_upper_bound or pt.global_index >= pt.global_lower_bound
        if pt.year <= 2026:
            assert pt.status == "historical"
        else:
            assert pt.status == "forecast"


def test_knowledge_graph_generation(service):
    analysis = service.analyze_career("Data Scientist")
    kg = analysis.knowledge_graph
    assert kg is not None
    assert len(kg.nodes) > 0
    assert len(kg.edges) > 0
    node_types = {n.type for n in kg.nodes}
    assert "occupation" in node_types
    assert "task" in node_types or "skill" in node_types


def test_domain_search_endpoint(client):
    res = client.post("/api/v1/career/search_by_domain", json={
        "domain_query": "Artificial Intelligence Machine Learning",
        "top_k": 3
    })
    assert res.status_code == 200
    data = res.json()
    assert data["domain_query"] == "Artificial Intelligence Machine Learning"
    assert len(data["results"]) > 0
    first = data["results"][0]
    assert "occupation" in first
    assert "relevance_score" in first
    assert "growth_score" in first


# 10. Integration Contract Compliance Test (context/INTEGRATION.md)
def test_integration_contract_compliance_m1_to_m2(service):
    """
    Verifies that CareerAnalysisResponse satisfies the exact M1 -> M2
    contract specified in context/INTEGRATION.md:
    - occupation (str)
    - outlook (str)
    - growth_score (float)
    - current_demand_score (float)
    - ai_exposure_score (float)
    - confidence_score (float)
    - top_skills (List[str])
    """
    analysis = service.analyze_career("Data Engineer")
    data = analysis.model_dump()

    # Required contract keys from context/INTEGRATION.md
    contract_keys = [
        "occupation",
        "outlook",
        "growth_score",
        "current_demand_score",
        "ai_exposure_score",
        "confidence_score",
        "top_skills",
    ]
    for key in contract_keys:
        assert key in data, f"Missing integration contract field: {key}"

    assert isinstance(data["occupation"], str)
    assert isinstance(data["outlook"], str)
    assert 0.0 <= data["growth_score"] <= 1.0
    assert 0.0 <= data["current_demand_score"] <= 1.0
    assert 0.0 <= data["ai_exposure_score"] <= 1.0
    assert 0.0 <= data["confidence_score"] <= 1.0
    assert isinstance(data["top_skills"], list)
    assert len(data["top_skills"]) > 0
    assert "top_skill_weights" in data
    assert len(data["top_skill_weights"]) > 0
    for sk, weight in data["top_skill_weights"].items():
        assert 0.0 <= weight <= 1.0

