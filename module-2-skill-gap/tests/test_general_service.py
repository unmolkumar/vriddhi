"""General engine (v2) - the engine: resolution, evidence and experience, gap analysis output, alternatives,
over-qualified, advice, provenance, match_text. Word-overlap encoder and fixture / fake module 1 clients."""
import pytest

from src.general import scoring
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client, M1Error, OccupationMatch, resolution
from src.general.schemas import GapAnalysisV2Request, MatchTextRequest
from src.general.service import ALTERNATIVE_MARGIN, GeneralEngine, RoleNotResolved, job_requirements
from test_general_matcher import WordEncoder

RN = "29-1141.00"
NURSE = """Staff Nurse

Experience
Staff Nurse | Apollo Hospitals | Jan 2018 - Dec 2023
- Administer medications to patients and monitor patients for reactions.
- Record patients' medical information and vital signs.

Skills
Patient care, infection control
"""


@pytest.fixture(scope="module")
def engine():
    return GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=WordEncoder())


def test_resolution_alias_soc_and_failures(engine):
    r = engine.resolve("staff nurse", None)
    assert (r.soc_code, r.confidence, r.method, r.low_confidence) == (RN, 1.0, "india_alias_exact", False)
    r = engine.resolve(None, RN)
    assert (r.title, r.method) == ("Registered Nurses", "soc_code")
    with pytest.raises(RoleNotResolved):
        engine.resolve("xyzzy plover", None)
    with pytest.raises(M1Error):
        engine.resolve(None, "99-9999.00")
    tie = engine.resolve("engineers", None)                     # Civil and Mechanical Engineers tie
    assert tie.low_confidence and tie.did_you_mean


def test_experience_years_parsed_overridden_or_unknown(engine):
    ev = engine.evidence(GapAnalysisV2Request(soc_code=RN, free_text=NURSE))
    assert ev.years == 6.0 and not ev.warnings
    assert ev.history and ev.history[0][0] == "Staff Nurse"
    assert engine.evidence(GapAnalysisV2Request(soc_code=RN, free_text=NURSE, experience_years=2)).years == 2
    ev = engine.evidence(GapAnalysisV2Request(soc_code=RN, skills=["nursing"]))
    assert ev.years is None and "Experience years unknown" in ev.warnings[0]


def test_gap_analysis_output(engine):
    r = engine.analyze(GapAnalysisV2Request(target_role="staff nurse", free_text=NURSE, hours_per_week=10))
    assert r.resolution.soc_code == RN and 0 < r.match_score <= 1 and r.m1_version == "2.2.0"
    assert r.score_breakdown.experience_band_source == "job_zone" and r.score_breakdown.experience_years == 6.0
    assert set(r.score_breakdown.by_type) <= {"market_skill", "tech", "tool", "task", "dwa"}
    assert r.strengths and all(s.status == "met" and s.evidence for s in r.strengths)
    assert all(g.status != "met" for g in r.gaps) and r.gaps_total >= len(r.gaps)
    assert [g.weight for g in r.gaps] == sorted((g.weight for g in r.gaps), reverse=True)
    assert {s.provenance for s in r.strengths + r.gaps} <= {"onet", "india_postings", "curated"}
    assert r.provenance_summary.scored_items["curated"] > 0 and "half weight" in r.provenance_summary.note
    assert all(d.item_type in ("knowledge", "skill") for d in r.draws_on) and r.fit_indicators
    assert {w.status for w in r.work_activities} <= {"evidenced", "not_evidenced", "no_dwa_data"}
    assert 0 < len(r.roadmap.items) <= 10 and r.roadmap.total_weeks is not None
    assert all(i.status != "met" and i.hours.low < i.hours.high for i in r.roadmap.items)
    med = next(s for s in r.strengths if s.requirement.startswith("Administer medications"))
    assert med.evidence.evidence_type == "work" and NURSE[med.evidence.span[0]:med.evidence.span[1]] == med.evidence.text


def test_advice_when_met_only_by_self_reported_evidence(engine):
    r = engine.analyze(GapAnalysisV2Request(soc_code=RN, skills=["Record patients' medical information and vital signs."]))
    met = next(s for s in r.strengths if s.requirement.startswith("Record patients"))
    assert met.evidence.evidence_type == "self" and met.credit < 1
    assert met.advice.startswith("You mention") and "work or projects" in met.advice


# --- alternatives and over-qualified: a two-occupation fake module 1 ---------------------------------------

class TwoRoles:
    """A (job zone 2) and B (job zone 4), related to each other, sharing most tasks."""
    def __init__(self, b_tasks):
        self.data = {"11-0001.00": ("Ward Assistants", 2, ["Change patient dressings", "Record vital signs",
                                                              "Clean ward equipment"]),
                     "11-0002.00": ("Ward Managers", 4, b_tasks)}

    def version(self):
        return "test"

    def profile(self, soc):
        if soc not in self.data:
            raise M1Error("not_found", soc, 404)
        title, zone, _ = self.data[soc]
        return {"soc_code": soc, "title": title, "job_zone": {"job_zone": zone}}

    def requirements(self, soc):
        return [{"soc_code": soc, "item_type": "task", "item_id": f"{soc}-{k}", "item_name": t, "importance_norm": 0.8,
                 "reliable": 1} for k, t in enumerate(self.data[soc][2])]

    def related(self, soc, limit=20):
        other = "11-0002.00" if soc == "11-0001.00" else "11-0001.00"
        return [{"related_soc_code": other}, {"related_soc_code": "99-0000.00"}]     # second one unknown: skipped

    def search(self, q, k=5):
        return resolution(q, [OccupationMatch(soc_code="11-0001.00", title="Ward Assistants", confidence=0.6),
                              OccupationMatch(soc_code="11-0002.00", title="Ward Managers", confidence=0.58)])


EVIDENCE = "Change patient dressings. Record vital signs. Clean ward equipment. Supervise ward staff."


def test_close_alternative_and_low_confidence_suggestions():
    e = GeneralEngine(client=TwoRoles(["Change patient dressings", "Record vital signs", "Supervise ward staff"]),
                      encoder=WordEncoder())
    # 2 years: inside both bands, so the senior role isn't penalised for experience
    r = e.analyze(GapAnalysisV2Request(target_role="ward", free_text=EVIDENCE, experience_years=2))
    related = [a for a in r.close_alternatives if a.source == "related"]
    assert [a.soc_code for a in related] == ["11-0002.00"] and related[0].score >= r.match_score - ALTERNATIVE_MARGIN
    assert "fit for Ward Managers" in related[0].message
    assert any(a.source == "search" and a.message == "Did you mean Ward Managers?" for a in r.close_alternatives)
    assert r.verdict.label == "good_fit"                     # 2 years: not over-qualified


def test_over_qualified_suggests_the_more_senior_related_role():
    e = GeneralEngine(client=TwoRoles(["Change patient dressings", "Record vital signs", "Supervise ward staff"]),
                      encoder=WordEncoder())
    band_high = scoring.JOB_ZONE_YEARS[2][1]
    r = e.analyze(GapAnalysisV2Request(soc_code="11-0001.00", free_text=EVIDENCE,
                                       experience_years=band_high + scoring.OVERQUALIFIED_EXTRA_YEARS + 2))
    assert r.verdict.label == "over_qualified"
    assert r.verdict.suggested_role.soc_code == "11-0002.00" and "Ward Managers" in r.verdict.reason


def test_unrelated_low_scoring_roles_are_not_alternatives():
    e = GeneralEngine(client=TwoRoles(["Prepare annual budgets", "Negotiate supplier contracts", "Hire nurses"]),
                      encoder=WordEncoder())
    r = e.analyze(GapAnalysisV2Request(soc_code="11-0001.00", free_text=EVIDENCE, experience_years=20))
    assert [a for a in r.close_alternatives if a.source == "related"] == []
    assert r.verdict.label == "good_fit"                     # senior, but the senior role isn't a fit


# --- match_text --------------------------------------------------------------------------------------------

JOB = """Staff Nurse - ICU, Pune
We need a nurse to administer medications to patients, record vital signs and maintain patient records.
Full time.
Must coordinate with doctors on care plans."""


def test_job_requirements_are_clauses():
    items = job_requirements(JOB)
    names = [i.name for i in items]
    assert "We need a nurse to administer medications to patients" in names and "record vital signs" in names
    assert "Full time." not in names                         # under JOB_MIN_WORDS
    assert not any(n.startswith("We need a nurse to administer medications to patients, record") for n in names)
    assert all(i.provenance == "job_text" and i.item_type == "task" for i in items)


def test_match_text_scores_the_job_and_blends_the_occupation(engine):
    alone = engine.match_text(MatchTextRequest(job_text=JOB, free_text=NURSE))
    assert alone.blend == 1.0 and alone.occupation_score is None and alone.match_score == alone.job_text_score
    assert any(m.requirement == "record vital signs" and m.provenance == "job_text" for m in alone.met)
    blended = engine.match_text(MatchTextRequest(job_text=JOB, free_text=NURSE, soc_code=RN))
    assert blended.blend == 0.6 and blended.occupation_title == "Registered Nurses"
    assert blended.match_score == pytest.approx(0.6 * blended.job_text_score + 0.4 * blended.occupation_score, abs=1e-4)
    assert {m.provenance for m in blended.met + blended.missing} >= {"job_text", "onet"}


def test_experience_penalty_applies_to_alternatives_too():
    e = GeneralEngine(client=TwoRoles(["Change patient dressings", "Record vital signs", "Clean ward equipment"]),
                      encoder=WordEncoder())
    r = e.analyze(GapAnalysisV2Request(soc_code="11-0001.00", free_text=EVIDENCE, experience_years=0))
    # same tasks, but Ward Managers' band (job zone 4: 2-6 years) costs 0 years of experience 2/3 of the penalty
    assert [a for a in r.close_alternatives if a.source == "related"] == []
