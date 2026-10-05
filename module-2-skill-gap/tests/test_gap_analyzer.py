"""Gap matrix, importance, match score (worked example), verdicts and advice."""
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.engines.skill_extractor as se
from src.engines import similarity
from src.engines.gap_analyzer import analyze_gap, next_level_role, typical_experience
from src.engines.profile_builder import build_profile
from src.models.schemas import GapAnalysisRequest, ManualProfileInput
from src.parsers.resume_parser import parse_document

HERE = Path(__file__).parent
MOCKS = json.loads((HERE / "mocks" / "m1_contract.json").read_text(encoding="utf-8"))
DS = MOCKS["career_analysis_examples"]["Data Scientists"]
DE = MOCKS["career_analysis_examples"]["Data Engineer"]


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(se, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real LLM called")))


@pytest.fixture(scope="module")
def resume_profile():
    """The text-PDF fixture: 7.5 years, Python L4, SQL/Excel/pandas/R/Tableau L3, ..."""
    return build_profile(parse_document((HERE / "fixtures" / "resume_text.pdf").read_bytes()),
                         use_llm=False, today=date(2026, 10, 1))


def manual(skills, years=1.0, **kw):
    return GapAnalysisRequest(manual_profile=ManualProfileInput(skills=skills, experience_years=years), **kw)


def row(result, skill):
    return next(g for g in result.gap_matrix if g.skill == skill)


# --- worked example (WORKING.md §5.4 reproduces these numbers) ----------------------------

def test_worked_example_data_scientist(resume_profile):
    r = analyze_gap(GapAnalysisRequest(target_role=DS["occupation"], required_skills=DS["top_skills"],
                                       profile=resume_profile))
    expected = {  # skill: (status, reason, via, current, required, importance)
        "python": ("matched", "exact", "python", 4, 3, 1.0),
        "machine_learning": ("matched", "maps_to", "scikit_learn", 2, 3, 0.8696),
        "sql": ("matched", "exact", "sql", 3, 3, 0.7692),
        "deep_learning": ("adjacent", "maps_to", "scikit_learn", 0, 2, 0.6897),
        "cloud": ("missing", None, None, 0, 2, 0.625),
        "docker": ("matched", "exact", "docker", 1, 2, 0.5714),
    }
    for skill, (status, reason, via, current, required, w) in expected.items():
        g = row(r, skill)
        assert (g.status, g.reason, g.via, g.current_level, g.required_level, g.importance) == \
            (status, reason, via, current, required, w), skill
    assert (row(r, "deep_learning").gap, row(r, "deep_learning").related_level) == (2, 2)
    assert row(r, "python").related_level is None and row(r, "cloud").related_level is None
    assert r.importance_source == "rank_decay"
    assert r.score_breakdown.coverage == 0.6432  # adjacent still earns ADJACENT_CREDIT, not a level ratio
    assert r.score_breakdown.experience_factor == 1.0
    assert r.match_score == 0.6967 and r.match_percent == 70
    assert r.verdict == "good_fit"
    assert r.skills.matched == ["python", "sql"]
    assert r.skills.weak == ["machine_learning", "docker"]
    assert r.skills.adjacent == ["deep_learning"]
    assert r.skills.critical_missing == ["cloud"]
    assert r.skills.above_requirement == ["python"]
    assert r.strengths == ["Python", "SQL"]


# --- matching reasons -------------------------------------------------------------------

def test_matched_via_finer_skill():
    r = analyze_gap(manual(["PostgreSQL", "AWS"], target_role="Backend Developer", required_skills=["sql", "cloud"]))
    assert (row(r, "sql").status, row(r, "sql").reason, row(r, "sql").via) == ("matched", "maps_to", "postgresql")
    assert (row(r, "cloud").reason, row(r, "cloud").via) == ("maps_to", "aws")


def test_adjacent_via_maps_to_sibling_and_broader():
    r = analyze_gap(manual(["MySQL"], target_role="Backend Developer", required_skills=["postgresql"]))
    assert (row(r, "postgresql").status, row(r, "postgresql").reason, row(r, "postgresql").via) == ("adjacent", "maps_to", "mysql")
    r = analyze_gap(manual(["SQL"], target_role="Backend Developer", required_skills=["postgresql"]))
    assert (row(r, "postgresql").status, row(r, "postgresql").via) == ("adjacent", "sql")


def test_adjacent_via_prerequisite():
    r = analyze_gap(manual(["Docker"], target_role="DevOps Engineer", required_skills=["kubernetes"]))
    g = row(r, "kubernetes")
    assert (g.status, g.reason, g.via, g.relation) == ("adjacent", "prerequisite", "docker", "builds on Docker")
    r = analyze_gap(manual(["Kubernetes"], target_role="DevOps Engineer", required_skills=["docker"]))
    g = row(r, "docker")
    assert (g.status, g.reason, g.via, g.relation) == ("adjacent", "prerequisite", "kubernetes", "is a foundation of Kubernetes")


@pytest.mark.parametrize("skills, required, relation", [
    (["MySQL"], "postgresql", "is related to MySQL"),                        # shared maps_to parent
    (["Docker"], "kubernetes", "builds on Docker"),                          # prerequisite
    (["Data Visualization"], "data_visualisation_tools", "is similar to Data Visualization"),  # semantic
])
def test_adjacent_wording_follows_reason(skills, required, relation):
    r = analyze_gap(manual(skills, target_role="X", required_skills=[required]))
    g = row(r, required)
    assert g.relation == relation
    milestone = next(m for m in r.roadmap.milestones if m.skill == required)
    assert milestone.reason == f"{g.display} {relation}, which you know."


def test_adjacent_via_semantic_similarity():
    # "data_visualisation_tools" isn't in the taxonomy; MiniLM puts it at ~0.88 from Data Visualization.
    r = analyze_gap(manual(["Data Visualization"], target_role="Data Analyst", required_skills=["data_visualisation_tools"]))
    g = row(r, "data_visualisation_tools")
    assert (g.status, g.reason, g.via, g.in_taxonomy) == ("adjacent", "semantic", "data_visualization", False)
    assert g.similarity >= similarity.SEMANTIC_THRESHOLD
    assert r.similarity_backend == "minilm"
    assert any("not in the skill taxonomy" in w for w in r.warnings)


def test_near_identical_wording_counts_as_matched():
    # "data_visualisations" isn't in the taxonomy; MiniLM puts it at ~0.97 from Data Visualization.
    r = analyze_gap(manual(["Data Visualization"], target_role="Data Analyst", required_skills=["data_visualisations"]))
    g = row(r, "data_visualisations")
    assert (g.status, g.reason, g.via) == ("matched", "semantic", "data_visualization")
    assert g.similarity >= similarity.SEMANTIC_MATCH_THRESHOLD
    assert g.current_level == 1 and r.score_breakdown.coverage == 0.3333  # matched credit = level ratio 1/3


def test_related_tools_below_threshold_stay_missing():
    r = analyze_gap(manual(["Tableau"], target_role="Data Analyst", required_skills=["looker_enterprise_suite"]))
    assert row(r, "looker_enterprise_suite").status == "missing"


def test_tfidf_fallback(monkeypatch):
    monkeypatch.setenv(similarity.DISABLE_ENV, "1")
    similarity._model.cache_clear()
    try:
        assert similarity.backend() == "tfidf" and similarity.threshold() == similarity.TFIDF_THRESHOLD
        m = similarity.similarity_matrix(["Tableau Desktop", "Kafka"], ["Tableau", "Excel"])
        assert m[0][0] > m[0][1] and m[1][0] < 0.5
        r = analyze_gap(manual(["Tableau"], target_role="Data Analyst", required_skills=["tableau_desktop_v2"]))
        assert r.similarity_backend == "tfidf"
    finally:
        monkeypatch.delenv(similarity.DISABLE_ENV)
        similarity._model.cache_clear()


def test_missing_ranked_by_importance():
    r = analyze_gap(manual(["Excel"], target_role="Data Engineer", required_skills=DE["top_skills"]))
    assert r.skills.critical_missing == ["python", "sql", "spark", "cloud"]
    assert [row(r, s).priority for s in ("python", "sql", "spark", "cloud")] == ["High", "High", "Medium", "Medium"]


# --- importance ---------------------------------------------------------------------------

def test_rank_decay_and_required_levels():
    r = analyze_gap(manual(["python"], target_role="Data Engineer", required_skills=DE["top_skills"]))
    assert [g.importance for g in r.gap_matrix] == [1.0, 0.8696, 0.7692, 0.6897]
    assert [g.required_level for g in r.gap_matrix] == [3, 3, 3, 2]


def test_skill_importance_wins():
    r = analyze_gap(manual(["python"], target_role="Data Engineer", required_skills=DE["top_skills"],
                           skill_importance={"cloud": 0.9, "apache_spark": 0.5, "python": 2.0}))
    assert r.importance_source == "skill_importance"
    assert (row(r, "cloud").importance, row(r, "cloud").required_level) == (0.9, 4)
    assert (row(r, "spark").importance, row(r, "spark").required_level) == (0.5, 2)
    assert row(r, "python").importance == 1.0           # clamped to [0, 1]
    assert row(r, "sql").importance == 0.8696           # not given -> rank decay


def test_knowledge_graph_importance():
    r = analyze_gap(manual(["python"], target_role=DS["occupation"], required_skills=DS["top_skills"],
                           knowledge_graph=DS["knowledge_graph"]))
    assert r.importance_source == "knowledge_graph"
    assert (row(r, "python").importance, row(r, "python").required_level) == (1.0, 4)
    assert (row(r, "docker").importance, row(r, "docker").required_level) == (0.65, 2)
    assert row(r, "machine_learning").importance == 0.8696  # no node -> rank decay


def test_malformed_knowledge_graph_is_ignored():
    r = analyze_gap(manual(["python"], target_role="X", required_skills=["python"], knowledge_graph={"edges": []}))
    assert r.importance_source == "rank_decay" and any("knowledge_graph" in w for w in r.warnings)


# --- verdicts -----------------------------------------------------------------------------

def test_under_skilled():
    r = analyze_gap(manual(["python", "excel"], target_role="Data Engineer", required_skills=DE["top_skills"]))
    assert r.verdict == "under_skilled" and r.match_score < 0.60
    assert r.verdict_message.startswith("You cover an estimated") and "yet to learn" in r.verdict_message


def test_good_fit(resume_profile):
    r = analyze_gap(GapAnalysisRequest(target_role=DS["occupation"], required_skills=DS["top_skills"], profile=resume_profile))
    assert r.verdict == "good_fit" and "Apply now" in r.verdict_message and r.suggested_role is None


ANALYST = ["sql", "excel", "tableau", "power_bi", "statistics"]


def test_over_qualified(resume_profile):
    r = analyze_gap(GapAnalysisRequest(target_role="Data Analyst", required_skills=ANALYST, profile=resume_profile))
    assert r.match_score >= 0.85 and r.experience_years > r.typical_experience.max
    assert r.experience_source == "title_heuristic"
    assert r.verdict == "over_qualified" and r.suggested_role == "Senior Data Analyst"
    assert "Python" in r.verdict_message and "pandas" in r.verdict_message


def test_one_year_control_is_good_fit(resume_profile):
    young = resume_profile.model_copy(update={"experience_years": 1.0})
    r = analyze_gap(GapAnalysisRequest(target_role="Data Analyst", required_skills=ANALYST, profile=young))
    assert r.verdict == "good_fit" and r.suggested_role is None


def test_explicit_experience_range_changes_verdict(resume_profile):
    r = analyze_gap(GapAnalysisRequest(target_role="Data Analyst", required_skills=ANALYST, profile=resume_profile,
                                       typical_experience={"min": 3, "max": 10}))
    assert r.verdict == "good_fit" and r.experience_source == "request"
    assert (r.typical_experience.min, r.typical_experience.max) == (3, 10)


def test_old_typical_experience_years_name_still_accepted(resume_profile):
    r = analyze_gap(GapAnalysisRequest(target_role="Data Analyst", required_skills=ANALYST, profile=resume_profile,
                                       typical_experience_years={"min": 3, "max": 10}))
    assert r.experience_source == "request" and r.typical_experience.max == 10


def test_invalid_experience_band_rejected(resume_profile):
    with pytest.raises(ValidationError):
        GapAnalysisRequest(target_role="X", required_skills=["python"], profile=resume_profile,
                           typical_experience={"min": 8, "max": 2})


def test_experience_factor_below_role_minimum():
    r = analyze_gap(manual(["python"], years=1, target_role="Senior Data Engineer", required_skills=["python"]))
    assert r.typical_experience.min == 4 and r.score_breakdown.experience_factor == 0.4  # (1+1)/(4+1)


def test_seniority_helpers():
    assert typical_experience("Data Analyst") == (0.0, 5.0)
    assert typical_experience("Junior Developer") == (0.0, 2.0)
    assert typical_experience("Senior ML Engineer") == (4.0, 8.0)
    assert typical_experience("Engineering Manager") == (6.0, 12.0)
    assert next_level_role("Data Analyst") == "Senior Data Analyst"
    assert next_level_role("Sr. Data Analyst") == "Lead Data Analyst"
    assert next_level_role("Lead Data Analyst") == "Principal Data Analyst"
    assert next_level_role("Principal Data Analyst") is None


# --- advice and input handling -------------------------------------------------------------

def test_evidence_based_advice(resume_profile):
    r = analyze_gap(GapAnalysisRequest(target_role=DS["occupation"], required_skills=DS["top_skills"], profile=resume_profile))
    assert row(r, "docker").advice == ("You list Docker, but nothing in your work or projects shows it. "
                                       "Build a project with Docker.")
    assert row(r, "python").advice is None  # work-supported
    assert row(r, "deep_learning").advice == "Deep Learning is related to scikit-learn, which you know, so it's a quick win."


def test_unverified_claim_advice():
    r = analyze_gap(GapAnalysisRequest(target_role="Cloud Engineer", required_skills=["cloud"],
                                       manual_profile={"skills": [{"name": "AWS", "level": 5}]}))
    assert "rate yourself level 5 in AWS" in row(r, "cloud").advice


def test_duplicate_required_skills_counted_once():
    r = analyze_gap(manual(["python"], target_role="X", required_skills=["spark", "apache_spark", "Apache Spark"]))
    assert [g.skill for g in r.gap_matrix] == ["spark"] and len(r.warnings) == 2


def test_request_needs_exactly_one_profile(resume_profile):
    with pytest.raises(ValidationError):
        GapAnalysisRequest(target_role="X", required_skills=["python"])
    with pytest.raises(ValidationError):
        GapAnalysisRequest(target_role="X", required_skills=["python"], profile=resume_profile,
                           manual_profile=ManualProfileInput())
    with pytest.raises(ValidationError):
        GapAnalysisRequest(target_role="X", required_skills=[], manual_profile=ManualProfileInput())


def test_empty_profile_is_all_missing():
    r = analyze_gap(manual([], target_role="Data Engineer", required_skills=DE["top_skills"]))
    assert r.match_score == round(0.15 * 1.0, 4) and r.verdict == "under_skilled"
    assert r.skills.critical_missing == ["python", "sql", "spark", "cloud"]
