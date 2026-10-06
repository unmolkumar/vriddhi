"""General engine (v2) - A2 scoring: evidence-strength credit, experience factor, verdict, and the worked example
in WORKING.md section 12.1."""
import pytest

from src.general import scoring
from src.general.matcher import RequirementMatch
from src.general.requirements import normalise


def _items():
    rows = [{"soc_code": "s", "item_type": "task", "item_id": f"t{k}", "item_name": f"Task {k}", "importance_norm": 0.8,
             "reliable": 1} for k in range(1, 6)]
    rows += [{"soc_code": "s", "item_type": "market_skill", "item_id": f"m{k}", "item_name": f"Skill {k}",
              "source": "india_postings", "india_demand_share": 0.2, "importance_norm": 0.8, "reliable": 1}
             for k in range(1, 6)]
    items, _ = normalise(rows)
    return {i.item_id: i for i in items}


def m(item, status, evidence_type=None):
    return RequirementMatch(item=item, provenance=item.provenance, status=status, similarity=0.7,
                            reason="semantic" if status != "missing" else "none",
                            evidence_text="x" if evidence_type else None, evidence_type=evidence_type)


@pytest.mark.parametrize("status, evidence, level, expected", [
    ("met", "work", 0.6, 1.0),              # 0.90 / 0.6 capped at 1
    ("met", "project", 0.6, 1.0),           # 0.75 / 0.6
    ("met", "mentioned", 0.6, 0.5 / 0.6),
    ("met", "self", 0.5, 0.8),
    ("partial", "work", 0.6, scoring.PARTIAL_CREDIT),
    ("missing", None, 0.6, 0.0)])
def test_credit_by_evidence_strength_and_required_level(status, evidence, level, expected):
    item = _items()["t1"].model_copy(update={"level": level})
    assert scoring.credit(m(item, status, evidence)) == pytest.approx(expected)


def test_evidence_strength_reuses_v1_confidence():
    assert scoring.EVIDENCE_STRENGTH == {"work": 0.90, "project": 0.75, "mentioned": 0.50, "self": 0.40}


def test_worked_example():
    """WORKING.md 12.1: 5 tasks and 5 market skills (importance 0.8), job zone 3, no experience."""
    i = _items()
    matches = [m(i["t1"], "met", "work"), m(i["t2"], "met", "work"), m(i["t3"], "met", "project"),
               m(i["t4"], "partial", "work"), m(i["t5"], "missing"),
               m(i["m1"], "met", "self"), m(i["m2"], "met", "work"),
               m(i["m3"], "missing"), m(i["m4"], "missing"), m(i["m5"], "missing")]
    # shares: task 0.30, market 0.40 (5 items each, full reliability) -> 0.4286 / 0.5714
    # task coverage (1 + 1 + 1 + 0.5 + 0) / 5 = 0.70; market (0.4/0.5 = 0.8, 1, 0, 0, 0) / 5 = 0.36
    skill = scoring.skill_score(matches)
    assert skill == pytest.approx(0.3 / 0.7 * 0.70 + 0.4 / 0.7 * 0.36)          # 0.5057
    band = scoring.experience_band({"job_zone": {"job_zone": 3}})
    assert band == (1.0, 4.0, "job_zone")
    factor = scoring.experience_factor(0.0, band)                                # 1 year short of the band
    assert factor == pytest.approx(1 - 0.2 * (1 / 3))                             # 0.9333
    assert skill * factor == pytest.approx(0.4720, abs=1e-4)
    by_type = scoring.by_type(matches)
    assert by_type["task"] == {"share": 0.4286, "coverage": 0.7, "items": 5}
    assert by_type["market_skill"] == {"share": 0.5714, "coverage": 0.36, "items": 5}


def test_experience_band_prefers_indian_postings():
    profile = {"indian_experience": {"typical_min": 2, "typical_max": 5, "sample_size": 40}, "job_zone": {"job_zone": 4}}
    assert scoring.experience_band(profile) == (2.0, 5.0, "india_postings")
    fallback = {"indian_experience": {"typical_min": 1, "typical_max": 4, "sample_size": 0, "fallback_to_job_zone": True},
                "job_zone": {"job_zone": 4}}
    assert scoring.experience_band(fallback) == (2.0, 6.0, "job_zone")
    assert scoring.experience_band({}) is None and scoring.experience_band(None) is None


def test_experience_factor():
    band = scoring.Band(2.0, 6.0, "job_zone")
    assert scoring.experience_factor(None, band) == 1.0              # unknown: no adjustment
    assert scoring.experience_factor(3.0, band) == 1.0 and scoring.experience_factor(15.0, band) == 1.0
    assert scoring.experience_factor(0.5, band) == pytest.approx(1 - 0.2 * 0.5)
    assert scoring.experience_factor(-10.0, band) == pytest.approx(scoring.EXPERIENCE_MIN_FACTOR)


def test_verdicts():
    band = scoring.Band(2.0, 6.0, "job_zone")
    t = scoring.GOOD_FIT_THRESHOLD
    assert scoring.verdict(t - 0.01, 4, band, None)[0] == "under_skilled"
    assert scoring.verdict(t + 0.1, 4, band, None)[0] == "good_fit"
    senior = 6 + scoring.OVERQUALIFIED_EXTRA_YEARS + 1
    assert scoring.verdict(t + 0.1, senior, band, None)[0] == "good_fit"     # senior, but no more senior fit found
    label, reason = scoring.verdict(t + 0.1, senior, band, ("11-9111.00", "Medical and Health Services Managers", 0.5))
    assert label == "over_qualified" and "Medical and Health Services Managers" in reason
