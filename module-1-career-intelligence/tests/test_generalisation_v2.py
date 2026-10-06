"""
Tests for Module 1 Cross-Industry Generalisation (v2.0).
Verifies acceptance criteria from Chaitanya (M2 & M3):
- Full O*NET 31.0 ingestion (35 skills, 33 knowledge, 52 abilities, job zones, DWAs)
- Additive endpoints (/occupations/search, /{soc}/requirements, /{soc}/profile, /{soc}/related)
- Free-text Indian phrasing resolution (staff nurse, CA, site engineer, telecaller, ITI electrician)
- Clean skill demand view (0 EEO junk words)
- Empirical Indian experience and unflagged salary benchmarks
- Mock export file validation for 15 test occupations
- Strict non-breaking additive rule (existing table counts intact)
"""
import pytest
import sqlite3
import json
from pathlib import Path
import sys

MODULE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_ROOT))

from src.engines.database import CareerDatabase
from fastapi.testclient import TestClient
from src.api.main import app

DB_PATH = MODULE_ROOT / "data" / "career_intel.db"
EXPORT_PATH = MODULE_ROOT / "data" / "m1_occupation_requirements_export.json"

TEST_OCCUPATIONS = [
    ("29-1141.00", "Registered Nurses"),
    ("29-1051.00", "Pharmacists"),
    ("31-9092.00", "Medical Assistants"),
    ("13-2011.00", "Accountants and Auditors"),
    ("13-2072.00", "Loan Officers"),
    ("25-2031.00", "Secondary School Teachers"),
    ("41-4012.00", "Sales Representatives, Wholesale & Manufacturing"),
    ("43-4051.00", "Customer Service Representatives"),
    ("17-2141.00", "Mechanical Engineers"),
    ("17-2051.00", "Civil Engineers"),
    ("47-2111.00", "Electricians"),
    ("35-1011.00", "Chefs and Head Cooks"),
    ("53-3032.00", "Heavy and Tractor-Trailer Truck Drivers"),
    ("27-1024.00", "Graphic Designers"),
    ("15-2051.00", "Data Scientists"),
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    return CareerDatabase()


# 1. Acceptance Queries: O*NET Content Model Coverage
def test_onet_tables_content_coverage():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # O*NET 35 Skills
    c.execute("SELECT COUNT(DISTINCT element_name) FROM onet_skills")
    assert c.fetchone()[0] == 35

    # O*NET 33 Knowledge areas
    c.execute("SELECT COUNT(DISTINCT element_name) FROM onet_knowledge")
    assert c.fetchone()[0] == 33

    # O*NET 52 Abilities
    c.execute("SELECT COUNT(DISTINCT element_name) FROM onet_abilities")
    assert c.fetchone()[0] == 52

    # Job Zones populated for ~all occupations
    c.execute("SELECT COUNT(*) FROM onet_job_zones")
    assert c.fetchone()[0] >= 900

    # Task ratings and DWAs populated
    c.execute("SELECT COUNT(*) FROM onet_task_ratings")
    assert c.fetchone()[0] > 15000

    c.execute("SELECT COUNT(*) FROM onet_dwa")
    assert c.fetchone()[0] > 20000

    conn.close()


# 2. Acceptance Queries: Clean Skill Demand (No EEO / Numeric noise)
def test_clean_skill_demand_no_junk():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Zero EEO noise terms in clean view
    c.execute("""
        SELECT COUNT(*) FROM v_skill_demand_clean 
        WHERE skill_normalized IN ('gender', 'religion', 'color', 'national_origin', 'race', 'disability')
    """)
    assert c.fetchone()[0] == 0

    # Accountant top skills must be professional accounting skills, not EEO words
    c.execute("""
        SELECT skill_normalized FROM skill_demand_by_soc
        WHERE soc_code = '13-2011.00'
        ORDER BY mentions DESC LIMIT 10
    """)
    accountant_skills = [row[0] for row in c.fetchall()]
    assert len(accountant_skills) > 0
    # Must contain accounting / financial terms
    assert any(term in accountant_skills for term in ['accounting', 'tally', 'taxation', 'gst', 'excel', 'finance', 'auditing'])
    # Must NOT contain EEO terms
    for eeo in ['gender', 'religion', 'color', 'national_origin']:
        assert eeo not in accountant_skills

    conn.close()


# 3. Acceptance Queries: Non-breaking Additive Rule (Zero row modifications to existing tables)
def test_existing_tables_unmodified():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM occupations")
    assert c.fetchone()[0] == 1016

    c.execute("SELECT COUNT(*) FROM job_postings_india")
    assert c.fetchone()[0] == 72691

    c.execute("SELECT COUNT(*) FROM job_postings_global")
    assert c.fetchone()[0] == 115000

    c.execute("SELECT COUNT(*) FROM salary_benchmarks")
    assert c.fetchone()[0] == 43374

    c.execute("SELECT COUNT(*) FROM skill_demand")
    assert c.fetchone()[0] == 371141

    conn.close()


# 4. Search Endpoint with Indian Colloquial Phrasing
@pytest.mark.parametrize("query,expected_soc", [
    ("staff nurse", "29-1141.00"),
    ("CA", "13-2011.00"),
    ("chartered accountant", "13-2011.00"),
    ("site engineer", "17-2051.00"),
    ("telecaller", "43-4051.00"),
    ("iti electrician", "47-2111.00"),
    ("data scientist", "15-2051.00"),
])
def test_search_occupations_indian_phrasing(client, query, expected_soc):
    res = client.get(f"/api/v1/occupations/search?q={query}&k=5")
    assert res.status_code == 200
    data = res.json()
    assert len(data) > 0
    # The top candidate or top-2 candidates must match the expected SOC code
    candidate_socs = [item["soc_code"] for item in data[:2]]
    assert expected_soc in candidate_socs
    top = data[0]
    assert 0.0 <= top["confidence"] <= 1.0
    assert "method" in top
    assert "matched_term" in top


# 5. Requirements Endpoint: Unified Contract Verification
def test_requirements_endpoint(client):
    res = client.get("/api/v1/occupations/29-1141.00/requirements")
    assert res.status_code == 200
    reqs = res.json()
    assert len(reqs) >= 200

    item_types = {r["item_type"] for r in reqs}
    assert "skill" in item_types
    assert "knowledge" in item_types
    assert "ability" in item_types
    assert "task" in item_types

    # Validate schema fields
    first = reqs[0]
    required_keys = [
        "soc_code", "item_type", "item_id", "item_name", "item_description",
        "importance_norm", "level_norm", "hot_technology", "in_demand",
        "india_demand_share", "source", "reliable"
    ]
    for key in required_keys:
        assert key in first


# 6. Profile Endpoint: 360-degree Occupation Intelligence
def test_profile_endpoint(client):
    res = client.get("/api/v1/occupations/13-2011.00/profile")
    assert res.status_code == 200
    profile = res.json()

    assert profile["soc_code"] == "13-2011.00"
    assert "Accountants" in profile["title"]
    assert "domain" in profile
    assert profile["domain"]["major_group_title"] == "Business and Financial Operations"

    assert "job_zone" in profile
    assert profile["job_zone"]["job_zone"] == 4

    assert "indian_education" in profile
    assert "primary_qualification" in profile["indian_education"]
    assert len(profile["indian_education"]["distribution"]) > 0

    assert "indian_experience" in profile
    assert "typical_min" in profile["indian_experience"]

    assert "salary_percentiles_india" in profile
    assert len(profile["salary_percentiles_india"]) > 0

    assert "related_occupations" in profile
    assert len(profile["related_occupations"]) > 0


# 7. Related Occupations Endpoint
def test_related_occupations_endpoint(client):
    res = client.get("/api/v1/occupations/17-2141.00/related?limit=5")
    assert res.status_code == 200
    related = res.json()
    assert len(related) > 0
    assert all("related_soc_code" in r for r in related)
    assert all("related_title" in r for r in related)


# 8. City Aliases Normalization
def test_city_aliases_normalization():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT city_canonical FROM city_aliases WHERE LOWER(raw_city) IN ('bengaluru', 'bangalore', 'bengaluru/bangalore')")
    canonical_cities = {row[0] for row in c.fetchall()}
    assert canonical_cities == {"Bengaluru"}

    c.execute("SELECT city_canonical FROM city_aliases WHERE LOWER(raw_city) IN ('gurgaon', 'gurugram')")
    canonical_gurugram = {row[0] for row in c.fetchall()}
    assert canonical_gurugram == {"Gurugram"}

    conn.close()


# 9. Verify 15 Test Occupations Across Diverse Industries
@pytest.mark.parametrize("soc,title", TEST_OCCUPATIONS)
def test_cross_industry_15_test_occupations(db, soc, title):
    # Verify each test occupation has full requirement coverage
    reqs = db.get_occupation_requirements(soc)
    assert len(reqs) >= 150

    skills = [r for r in reqs if r["item_type"] == "skill"]
    knowledge = [r for r in reqs if r["item_type"] == "knowledge"]
    abilities = [r for r in reqs if r["item_type"] == "ability"]

    assert len(skills) >= 20
    assert len(knowledge) >= 20
    assert len(abilities) >= 20

    # Verify profile is complete
    profile = db.get_occupation_profile(soc)
    assert profile is not None
    assert profile["job_zone"] is not None
    assert len(profile["indian_education"]["distribution"]) > 0


# 10. Verify Static Mock Export File
def test_static_export_file_validity():
    assert EXPORT_PATH.exists()
    with open(EXPORT_PATH, "r", encoding="utf-8") as f:
        export_data = json.load(f)

    assert "metadata" in export_data
    assert export_data["metadata"]["version"] in ["2.0.0", "2.1.0"]
    assert "occupations" in export_data
    assert len(export_data["occupations"]) == 15

    for soc, _ in TEST_OCCUPATIONS:
        assert soc in export_data["occupations"]
        occ_entry = export_data["occupations"][soc]
        assert occ_entry["total_requirements"] > 150
        assert len(occ_entry["requirements"]) == occ_entry["total_requirements"]
        assert occ_entry["requirements_breakdown"]["dwa"] > 0
        assert occ_entry["requirements_breakdown"]["tools"] > 0
