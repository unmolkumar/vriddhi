"""City normalisation, schemas, and the health endpoint."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.main import PORT, app
from src.engines.locations import city_from_parts, expand_cities, normalise_city, provider_query_name
from src.models.schemas import JobSearchRequest


@pytest.mark.parametrize("raw, city", [
    ("Bangalore", "Bengaluru"), ("bengaluru, karnataka", "Bengaluru"), ("Gurgaon", "Gurugram"),
    ("Bombay", "Mumbai"), ("New Delhi", "Delhi"), ("Delhi-NCR", "Delhi NCR"), ("NCR", "Delhi NCR"),
    ("Secunderabad", "Hyderabad"), ("Work from home", "Remote"), ("surat", "Surat"), ("  ", None), (None, None),
])
def test_normalise_city(raw, city):
    assert normalise_city(raw) == city


def test_expand_and_provider_names():
    assert expand_cities("Delhi-NCR") == ["Delhi", "Noida", "Gurugram"]
    assert expand_cities("bangalore") == ["Bengaluru"] and expand_cities(None) == []
    assert [provider_query_name(c) for c in ("Bengaluru", "Gurugram", "Delhi", "Pune")] == ["Bangalore", "Gurgaon", "New Delhi", "Pune"]
    assert city_from_parts(["India", "Karnataka", "Bangalore"]) == "Bengaluru"
    assert city_from_parts(["India", "Haryana", "Gurgaon"]) == "Gurugram"


def test_search_request_accepts_module_2_profile_or_manual_fields():
    m2_profile = {"skills": [{"name": "python", "level": 4, "evidence": ["work_supported"], "display": "Python",
                              "confidence": 0.9, "maps_to": None}],
                  "experience_years": 7.5, "location": "Bengaluru", "education": ["B.Tech Computer Science"],
                  "source": {"format": "pdf"}, "warnings": []}   # extra module 2 fields are ignored
    req = JobSearchRequest(target_role="Data Scientist", profile=m2_profile)
    assert req.candidate().skills[0].name == "python" and req.candidate().experience_years == 7.5
    manual = JobSearchRequest(target_role="Backend Developer", skills=["python", "sql"], experience_years=2,
                              location="Bengaluru")
    assert [s.name for s in manual.candidate().skills] == ["python", "sql"] and manual.candidate().location == "Bengaluru"
    with pytest.raises(ValidationError):
        JobSearchRequest(target_role="X", skills=["python"])          # no location anywhere
    baseline = JobSearchRequest(target_role="X", location="Pune",
                                market_baseline={"region": "india", "posting_volume": 4200, "median_salary_inr_lpa": 18.5,
                                                 "top_locations": ["Bengaluru"], "posting_growth_yoy_pct": 12.5})
    assert baseline.market_baseline.median_salary_inr_lpa == 18.5


def test_health():
    assert PORT == 8003
    body = TestClient(app).get("/api/v1/health").json()
    assert body["status"] == "ok" and body["module"] == "module-3-job-matching-salary"
    assert body["providers"] == {"adzuna": True, "jsearch": True} and "jobs" in body["cache"]
