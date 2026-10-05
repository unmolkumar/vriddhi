"""FastAPI endpoints via TestClient, and the exported JSON schema."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.engines.skill_extractor as se
from src.api.main import PORT, app
from src.models.schemas import GapAnalysisResult, export_json_schema
from src.parsers.resume_parser import MAX_FILE_BYTES

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
DS = json.loads((HERE / "mocks" / "m1_contract.json").read_text(encoding="utf-8"))["career_analysis_examples"]["Data Scientists"]
client = TestClient(app)


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(se, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real LLM called")))
    monkeypatch.setenv("GROQ_API_KEY", "")


def upload(name, content=None, **data):
    body = content if content is not None else (FIXTURES / name).read_bytes()
    return client.post("/api/v1/skills/analyze_resume", files={"file": (name, body)}, data={"use_llm": "false", **data})


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code and resp.json()["error"]["message"]


def test_port_is_distinct_from_module_1():
    assert PORT == 8002


def test_health():
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["module"] == "module-2-skill-gap"
    assert body["taxonomy_skills"] == 483 and body["similarity_backend"] in ("minilm", "tfidf")


@pytest.mark.parametrize("name", ["resume_text.pdf", "resume.docx", "resume.txt"])
def test_analyze_resume_formats(name):
    resp = upload(name, location="bangalore")
    assert resp.status_code == 200, resp.text
    profile = resp.json()["profile"]
    assert {"python", "sql", "tableau"} <= {s["name"] for s in profile["skills"]}
    assert profile["location"] == "Bengaluru" and resp.json()["gap_analysis"] is None


def test_analyze_resume_with_gap():
    resp = upload("resume_text.pdf", target_role=DS["occupation"], required_skills=",".join(DS["top_skills"]))
    gap = resp.json()["gap_analysis"]
    assert gap["target_role"] == "Data Scientists" and gap["verdict"] in ("under_skilled", "good_fit", "over_qualified")
    assert [g["skill"] for g in gap["gap_matrix"]] == DS["top_skills"]


@pytest.mark.parametrize("name, content, status, code", [
    ("resume_encrypted.pdf", None, 400, "ENCRYPTED_FILE"),
    ("resume_corrupt.pdf", None, 400, "CORRUPT_FILE"),
    ("big.pdf", b"%PDF-1.7" + b"0" * MAX_FILE_BYTES, 413, "FILE_TOO_LARGE"),
    ("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00binary", 415, "UNSUPPORTED_FORMAT"),
    ("empty.txt", b"", 400, "EMPTY_DOCUMENT"),
], ids=["encrypted", "corrupt", "too_large", "png", "empty"])
def test_analyze_resume_errors(name, content, status, code):
    assert_error(upload(name, content), status, code)


def test_analyze_resume_without_file_or_role():
    assert_error(client.post("/api/v1/skills/analyze_resume", data={"use_llm": "false"}), 422, "INVALID_REQUEST")
    assert_error(upload("resume.txt", required_skills="python"), 422, "INVALID_REQUEST")


def test_gap_analysis_manual():
    resp = client.post("/api/v1/skills/gap_analysis", json={
        "target_role": "Data Engineer", "required_skills": ["python", "sql", "spark", "cloud"],
        "manual_profile": {"skills": ["python", "excel"], "experience_years": 1}, "hours_per_week": 8})
    assert resp.status_code == 200, resp.text
    body = GapAnalysisResult.model_validate(resp.json())
    assert body.verdict == "under_skilled" and body.roadmap.hours_per_week == 8
    assert body.experience_source == "title_heuristic"
    resp = client.post("/api/v1/skills/gap_analysis", json={
        "target_role": "Data Engineer", "required_skills": ["python"], "typical_experience": {"min": 2, "max": 6},
        "manual_profile": {"skills": ["python"], "experience_years": 1}})
    assert resp.json()["experience_source"] == "request" and resp.json()["typical_experience"] == {"min": 2.0, "max": 6.0}


def test_profile_round_trip_from_resume_to_gap_analysis():
    """Integration scenario: /analyze_resume output feeds /gap_analysis unchanged."""
    profile = upload("resume_text.pdf").json()["profile"]
    resp = client.post("/api/v1/skills/gap_analysis", json={
        "target_role": DS["occupation"], "required_skills": DS["top_skills"], "profile": profile,
        "knowledge_graph": DS["knowledge_graph"]})
    assert resp.status_code == 200 and resp.json()["importance_source"] == "knowledge_graph"


@pytest.mark.parametrize("payload", [
    {"target_role": "X", "required_skills": []},
    {"target_role": "X", "required_skills": ["python"]},  # no profile
    {"target_role": "", "required_skills": ["python"], "manual_profile": {}},
    {"target_role": "X", "required_skills": ["python"], "manual_profile": {"experience_years": -2}},
])
def test_gap_analysis_invalid(payload):
    assert_error(client.post("/api/v1/skills/gap_analysis", json=payload), 422, "INVALID_REQUEST")


def test_exported_schema_is_current():
    committed = json.loads((HERE.parent / "src" / "models" / "schema_m2.json").read_text(encoding="utf-8"))
    assert committed == export_json_schema(), "run: python -m src.models.schemas"
    assert {"UserProfile", "GapAnalysisRequest", "GapAnalysisResult", "AnalyzeResumeResponse",
            "ErrorResponse"} <= set(committed["$defs"])


def test_openapi_lists_endpoints():
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/v1/skills/analyze_resume", "/api/v1/skills/gap_analysis", "/api/v1/health"} <= set(paths)


# --- /skills/extract ---------------------------------------------------------------------------

JD = ("Backend engineer: Java, Spring Boot, PostgreSQL, Docker and Kubernetes. "
      "Infrastructure automation with Terraform is a plus.")


def test_extract_job_description():
    resp = client.post("/api/v1/skills/extract", json={"text": JD})
    assert resp.status_code == 200
    skills = {s["id"]: s for s in resp.json()["skills"]}
    assert {"java", "spring_boot", "postgresql", "docker", "kubernetes", "automation", "terraform"} <= set(skills)
    assert skills["postgresql"]["maps_to"] == "sql" and skills["postgresql"]["in_taxonomy"]
    assert skills["java"]["source"] == "dictionary" and skills["java"]["matches"] == ["Java"]
    assert "level" not in skills["java"] and "evidence" not in skills["java"]  # text, not a resume
    assert resp.json()["warnings"] == []


def test_extract_warnings():
    body = client.post("/api/v1/skills/extract", json={"text": "We value teamwork.", "use_llm": True}).json()
    assert body["skills"] == []
    assert any("GROQ_API_KEY" in w for w in body["warnings"]) and any("No known skills" in w for w in body["warnings"])


@pytest.mark.parametrize("payload, status, code", [
    ({"text": "   "}, 422, "EMPTY_TEXT"),
    ({"text": "x" * 50_001}, 413, "TEXT_TOO_LARGE"),
    ({}, 422, "INVALID_REQUEST"),
    ({"text": 42}, 422, "INVALID_REQUEST"),
], ids=["empty", "too_large", "missing", "wrong_type"])
def test_extract_errors(payload, status, code):
    assert_error(client.post("/api/v1/skills/extract", json=payload), status, code)


def test_gap_analysis_with_only_categories_is_422():
    resp = client.post("/api/v1/skills/gap_analysis", json={
        "target_role": "X", "required_skills": ["data"], "manual_profile": {"skills": ["python"]}})
    assert_error(resp, 422, "INVALID_REQUEST")


def test_gap_analysis_accepts_m1_top_skill_weights():
    resp = client.post("/api/v1/skills/gap_analysis", json={
        "target_role": "DevOps Engineer", "required_skills": ["devops", "linux", "kubernetes", "automation", "docker", "aws"],
        "top_skill_weights": {"devops": 1.0, "linux": 0.8, "kubernetes": 0.7, "automation": 0.5, "docker": 0.6, "aws": 0.4},
        "manual_profile": {"skills": ["Linux", "Docker"], "experience_years": 2}})
    assert resp.status_code == 200 and resp.json()["importance_source"] == "m1_weights"
