"""Profile building: evidence tags, evidence-based levels, manual entry, contract shape, privacy."""
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.engines.skill_extractor as se
from src.engines.profile_builder import MAX_DERIVED_LEVEL, build_profile, normalise_location, profile_from_manual
from src.models.schemas import ManualProfileInput
from src.parsers.resume_parser import parse_document

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 1)


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real LLM called")))
    monkeypatch.setattr(se, "LLM_CACHE_DIR", tmp_path / "llm")


def profile(name, **kw):
    return build_profile(parse_document((FIXTURES / name).read_bytes(), name), use_llm=False, today=TODAY, **kw)


@pytest.fixture(scope="module")
def text_profile():
    return build_profile(parse_document((FIXTURES / "resume_text.pdf").read_bytes()), use_llm=False, today=TODAY,
                         location="bangalore", target_occupation="Data Analyst")


def skill(p, sid):
    return next(s for s in p.skills if s.name == sid)


def test_evidence_tags(text_profile):
    p = text_profile
    assert skill(p, "python").evidence == ["work_supported", "project_supported", "resume_mentioned"]
    assert skill(p, "sql").evidence == ["work_supported", "resume_mentioned"]
    assert skill(p, "xgboost").evidence == ["project_supported"]
    assert skill(p, "docker").evidence == ["resume_mentioned"]
    assert skill(p, "go").evidence == ["resume_mentioned"]       # from the skills list, not "go the extra mile"
    assert skill(p, "statistics").evidence == ["resume_mentioned"]  # "statistical analysis" in the summary
    names = {s.name for s in p.skills}
    assert "github" not in names           # profile URL, not a skill
    assert "data_science" not in names     # job title "Data Science Intern" is not skill evidence


def test_levels_follow_evidence_not_mentions(text_profile):
    p = text_profile
    assert (skill(p, "python").level, skill(p, "sql").level, skill(p, "xgboost").level, skill(p, "docker").level) == (4, 3, 2, 1)
    assert skill(p, "python").confidence > skill(p, "xgboost").confidence > skill(p, "docker").confidence
    for s in p.skills:
        assert s.level <= MAX_DERIVED_LEVEL
        if s.evidence == ["resume_mentioned"]:
            assert s.level == 1  # mentioned != expert (AGENTS.md §14)


def test_profile_fields(text_profile):
    p = text_profile
    assert (p.experience_years, p.internship_years) == (7.5, 0.5)
    assert p.education == ["B.Tech Computer Science"]
    assert p.education_details[0].institution == "NIT Trichy"
    assert [w.company for w in p.work_history] == ["Swiggy", "Mu Sigma", "Fractal Analytics"]
    assert (p.location, p.target_occupation) == ("Bengaluru", "Data Analyst")
    assert p.source.format == "pdf" and p.source.text_sha1 and p.warnings == []
    assert set(p.source.sections_found) >= {"summary", "experience", "projects", "skills", "education"}


def test_integration_profile_shape(text_profile):
    """Superset of the common user profile in context/INTEGRATION.md."""
    d = text_profile.model_dump()
    assert {"user_id", "skills", "experience_years", "education", "location", "preferred_locations",
            "target_occupation"} <= set(d)
    assert {"name", "level", "evidence"} <= set(d["skills"][0])
    assert all(isinstance(e, str) for e in d["education"])


def test_resume_text_is_not_kept(text_profile):
    dumped = text_profile.model_dump_json()
    assert "Automated weekly reports" not in dumped and "aarav.mehta@example.com" not in dumped


def test_docx_and_txt_give_the_same_profile(text_profile):
    for name in ("resume.docx", "resume.txt"):
        p = profile(name)
        assert [(s.name, s.level, s.evidence) for s in p.skills] == [(s.name, s.level, s.evidence) for s in text_profile.skills]
        assert p.experience_years == text_profile.experience_years


def test_scanned_pdf_profile_close_to_text_version(text_profile):
    p = profile("resume_scanned.pdf")
    assert p.source.ocr_pages == [1]
    assert p.experience_years == text_profile.experience_years
    text_ids, ocr_ids = {s.name for s in text_profile.skills}, {s.name for s in p.skills}
    assert len(text_ids & ocr_ids) >= 0.9 * len(text_ids)  # OCR may misread a name (e.g. "Power Bl")


def test_missing_sections_warn():
    p = build_profile(parse_document(b"Python and SQL person"), use_llm=False)
    assert p.experience_years == 0 and {s.name for s in p.skills} == {"python", "sql"}
    assert "No Experience section found." in p.warnings and "No Skills section found." in p.warnings


def test_manual_entry():
    p = profile_from_manual(ManualProfileInput(
        skills=["python", "ML", "k8s", "Quantum Basket Weaving", {"name": "AWS", "level": 5}, {"name": "sql", "level": 2}],
        experience_years=2, location="Gurgaon", preferred_locations=["bangalore", "Pune"]))
    by = {s.name: s for s in p.skills}
    assert set(by) == {"python", "machine_learning", "kubernetes", "quantum_basket_weaving", "aws", "sql"}
    assert all(s.evidence == ["self_reported"] for s in p.skills)
    assert by["python"].level == 1 and not by["python"].needs_verification
    assert (by["aws"].claimed_level, by["aws"].level, by["aws"].needs_verification) == (5, 2, True)
    assert (by["sql"].level, by["sql"].needs_verification) == (2, False)
    assert by["quantum_basket_weaving"].in_taxonomy is False
    assert any("Quantum Basket Weaving" in w for w in p.warnings)
    assert (p.location, p.preferred_locations, p.source.format) == ("Gurugram", ["Bengaluru", "Pune"], "manual")


def test_manual_entry_ambiguous_names_resolve():
    p = profile_from_manual(ManualProfileInput(skills=["Go", "R", "C", "Excel"]))
    assert {s.name for s in p.skills} == {"go", "r", "c", "excel"}


def test_manual_entry_empty_and_invalid():
    p = profile_from_manual(ManualProfileInput())
    assert p.skills == [] and "No skills entered." in p.warnings
    with pytest.raises(ValidationError):
        ManualProfileInput(experience_years=-1)
    with pytest.raises(ValidationError):
        ManualProfileInput(skills=[{"name": "python", "level": 9}])


def test_normalise_location():
    assert normalise_location("bangalore, karnataka") == "Bengaluru"
    assert normalise_location("Bombay") == "Mumbai"
    assert normalise_location("surat") == "Surat"
    assert normalise_location("  ") is None
