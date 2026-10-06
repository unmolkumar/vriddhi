"""v2 API: /api/v2/skills/gap_analysis, /analyze_resume, /match_text over the module 1 fixture (word-overlap encoder).
Errors in the integration shape; module 1 down -> 503 for v2 while v1 keeps working; schema drift."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes_v2 import get_engine
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client, M1Error
from src.general.schemas import export_json_schema
from src.general.service import GeneralEngine
from test_general_matcher import WordEncoder

HERE = Path(__file__).parent
RN = "29-1141.00"
NURSE = ("Staff nurse for six years. I administer medications to patients and monitor patients for reactions, "
         "record patients' medical information and vital signs, and follow infection control.")
ENGINE = GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=WordEncoder())


class DownClient:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise M1Error("unreachable", "module 1 unreachable at http://localhost:8001: ConnectError")
        return fail


@pytest.fixture
def client():
    app.dependency_overrides[get_engine] = lambda: ENGINE
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_gap_analysis(client):
    r = client.post("/api/v2/skills/gap_analysis", json={"target_role": "staff nurse", "free_text": NURSE,
                                                          "experience_years": 6, "hours_per_week": 8})
    assert r.status_code == 200
    body = r.json()
    assert body["resolution"]["soc_code"] == RN and body["verdict"]["label"] in {"under_skilled", "insufficient_evidence", "good_fit", "over_qualified"}
    for key in ("match_score", "score_breakdown", "strengths", "gaps", "draws_on", "fit_indicators",
                "close_alternatives", "roadmap", "provenance_summary", "m1_version", "warnings"):
        assert key in body
    assert body["roadmap"]["hours_per_week"] == 8 and body["m1_version"] == "2.2.0"


def test_gap_analysis_with_soc_code_skills_and_v1_profile(client):
    r = client.post("/api/v2/skills/gap_analysis", json={"soc_code": RN, "skills": ["patient care", "IV cannulation"]})
    assert r.status_code == 200 and r.json()["resolution"]["method"] == "soc_code"
    with open(HERE / "fixtures" / "resume.txt", "rb") as f:                   # v1 profile -> v2
        profile = client.post("/api/v1/skills/analyze_resume", files={"file": ("resume.txt", f, "text/plain")},
                              data={"use_llm": "false"}).json()["profile"]
    r = client.post("/api/v2/skills/gap_analysis", json={"soc_code": "15-2051.00", "profile": profile})
    assert r.status_code == 200
    body = r.json()
    assert body["strengths"] and {s["evidence"]["section"] for s in body["strengths"]} == {"profile"}
    assert body["score_breakdown"]["experience_years"] == profile["experience_years"]


@pytest.mark.parametrize("payload, code", [
    ({"free_text": NURSE}, "INVALID_REQUEST"),                                  # no role
    ({"target_role": "staff nurse"}, "INVALID_REQUEST"),                        # no evidence
    ({"soc_code": "29-1141", "free_text": NURSE}, "INVALID_REQUEST")])          # malformed SOC
def test_validation_errors(client, payload, code):
    r = client.post("/api/v2/skills/gap_analysis", json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_unknown_role_and_occupation(client):
    r = client.post("/api/v2/skills/gap_analysis", json={"target_role": "xyzzy plover", "free_text": NURSE})
    assert r.status_code == 404 and r.json()["error"]["code"] == "ROLE_NOT_RESOLVED"
    r = client.post("/api/v2/skills/gap_analysis", json={"soc_code": "99-9999.00", "free_text": NURSE})
    assert r.status_code == 404 and r.json()["error"]["code"] == "OCCUPATION_NOT_FOUND"


def test_module_1_down_is_503_for_v2_only():
    app.dependency_overrides[get_engine] = lambda: GeneralEngine(client=DownClient(), encoder=WordEncoder())
    try:
        c = TestClient(app)
        r = c.post("/api/v2/skills/gap_analysis", json={"target_role": "staff nurse", "free_text": NURSE})
        assert r.status_code == 503 and r.json()["error"]["code"] == "M1_UNAVAILABLE"
        assert c.get("/api/v1/health").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_analyze_resume_v2(client):
    with open(HERE / "fixtures" / "resume.txt", "rb") as f:
        r = client.post("/api/v2/skills/analyze_resume", files={"file": ("resume.txt", f, "text/plain")},
                        data={"soc_code": "15-2051.00", "hours_per_week": "6"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["skills"] and body["gap_analysis"]["resolution"]["soc_code"] == "15-2051.00"


def test_analyze_resume_v2_uses_v1_file_checks(client):
    with open(HERE / "fixtures" / "resume_corrupt.pdf", "rb") as f:
        r = client.post("/api/v2/skills/analyze_resume", files={"file": ("resume.pdf", f, "application/pdf")},
                        data={"soc_code": RN})
    assert r.status_code == 400 and r.json()["error"]["code"] == "CORRUPT_FILE"
    with open(HERE / "fixtures" / "resume.txt", "rb") as f:
        r = client.post("/api/v2/skills/analyze_resume", files={"file": ("resume.txt", f, "text/plain")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"        # no role


def test_match_text(client):
    job = ("ICU staff nurse wanted. Administer medications to patients, record vital signs and coordinate with "
           "doctors. BLS certification required.")
    r = client.post("/api/v2/skills/match_text", json={"job_text": job, "soc_code": RN, "free_text": NURSE})
    assert r.status_code == 200
    body = r.json()
    assert body["blend"] == 0.6 and body["occupation_title"] == "Registered Nurses" and body["job_requirements"] > 2
    assert any(m["provenance"] == "job_text" for m in body["met"] + body["missing"])
    r = client.post("/api/v2/skills/match_text", json={"job_text": "too short", "free_text": NURSE})
    assert r.status_code == 422


def test_openapi_lists_v2_and_v1(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/v2/skills/gap_analysis", "/api/v2/skills/analyze_resume", "/api/v2/skills/match_text",
            "/api/v1/skills/gap_analysis"} <= set(paths)


def test_v2_schema_export_is_current():
    committed = json.loads((HERE.parent / "src" / "models" / "schema_m2_v2.json").read_text(encoding="utf-8"))
    assert committed == export_json_schema(), "run: python -m src.general.schemas"
