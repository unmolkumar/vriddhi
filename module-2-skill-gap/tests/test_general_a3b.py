"""General engine (v2) - A3b: evidence volume and insufficient_evidence, follow-up questions and answers,
education lines, roadmap ranking by expected gain with a tech cap and a basics list. Word-overlap encoder."""
import sys

import pytest

from src.general import roadmap, scoring, verdict
from src.general.evidence import EvidenceUnit, from_text, is_education_line
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client, M1Error
from src.general.matcher import RequirementMatch
from src.general.requirements import normalise
from src.general.schemas import GapAnalysisV2Request
from src.general.service import GeneralEngine
from test_general_matcher import WordEncoder

RN = "29-1141.00"


def match(name, item_type="task", status="missing", similarity=0.1, importance=0.8, item_id=None, **kw):
    items, _ = normalise([{"soc_code": "s", "item_type": item_type, "item_id": item_id or name, "item_name": name,
                           "importance_norm": importance, "reliable": 1}])
    return RequirementMatch(item=items[0], provenance="onet", status=status, similarity=similarity,
                            reason="none" if status == "missing" else "semantic", **kw)


# --- evidence volume and the verdict rule ----------------------------------------------------------------

def test_volume_counts_sentences_not_clauses_titles_education_or_answers():
    units = from_text("Experience\nNurse | Apollo | 2019 - 2024\n- Gave IV medicines, charted vitals and dressed wounds.\n"
                      "Education\nB.Sc Nursing, 2019\n")
    units.append(EvidenceUnit(text="I give injections", evidence_type="self", section="answers"))
    vol = verdict.volume(units, [match("Give medicines", status="met", similarity=0.9), match("Bake bread")])
    assert vol.units == 1                                   # the bullet; its clauses, the title, education, answer: no
    assert 0 < vol.related_share < 1 and vol.short == (1 < verdict.SHORT_UNITS)


@pytest.mark.parametrize("score, units, related, other, label", [
    (0.30, 1, 0.6, False, "good_fit"),                 # above the threshold
    (0.21, 1, 0.6, False, "good_fit"),                 # short: the short-description threshold applies
    (0.10, 1, 0.6, False, "insufficient_evidence"),    # short, below, related evidence
    (0.10, 1, 0.0, False, "under_skilled"),            # short but nothing related: someone from another field
    (0.10, 9, 0.3, False, "under_skilled"),            # long description, below the threshold, little related
    (0.10, 9, 0.6, False, "insufficient_evidence"),    # A4: long but oblique (much related, little clearly met)
    (0.10, 9, 0.6, True, "under_skilled"),             # A4: ... with past titles in another field
    (0.10, 1, 0.6, True, "under_skilled")])
def test_label_rule(score, units, related, other, label):
    assert verdict.label_for(score, units, related, 1.0, other, *verdict.params()) == label


def test_decide_matches_label_rule():
    vol = verdict.EvidenceVolume(units=1, related_share=0.5, short=True)
    label, reason = verdict.decide(0.05, vol, None, None, None)
    assert label == "insufficient_evidence" and "Answer the questions" in reason


# --- follow-up questions ----------------------------------------------------------------------------------

def test_template_questions_by_type():
    assert verdict.template_question(match("Epic Systems", "tech")) == "Have you used Epic Systems in your work?"
    assert verdict.template_question(match("Tally ERP", "market_skill")) == "Do you have experience with Tally ERP?"
    q = verdict.template_question(match("Plan layout and installation of electrical wiring, equipment, or fixtures, "
                                        "based on job specifications and local codes."))
    assert q == "In your work, do you plan layout and installation of electrical wiring, equipment, or fixtures?"


def test_questions_pick_heaviest_unevidenced_tasks_first():
    ms = [match("Light task", importance=0.3), match("Heavy task", importance=0.9),
          match("Met task", status="met", similarity=0.9), match("Some software", "tech", importance=0.95),
          match("Answered task", importance=0.95)]
    qs = verdict.follow_up_questions(ms, "Nurses", answered={"Answered task"})
    assert [q.requirement for q in qs] == ["Heavy task", "Light task", "Some software"]


def test_rephraser_is_used_and_fail_safe(tmp_path, monkeypatch):
    ms = [match("Heavy task", importance=0.9), match("Light task", importance=0.3)]
    qs = verdict.follow_up_questions(ms, "Nurses", rephrase=lambda q, occ: ["Do you do heavy work?", None])
    assert [q.question for q in qs] == ["Do you do heavy work?", "In your work, do you light task?"]

    class Boom:
        def __init__(self, *a, **k):
            raise TimeoutError
    monkeypatch.setitem(sys.modules, "groq", type(sys)("groq"))
    sys.modules["groq"].Groq = Boom
    assert verdict.GroqRephraser("key", cache_dir=tmp_path)(["Q1?", "Q2?"], "Nurses") == [None, None]


# --- answers ----------------------------------------------------------------------------------------------

def test_answers_become_weak_labelled_self_evidence():
    ms = [match("A task", item_id="a"), match("B task", item_id="b"), match("C task", item_id="c"),
          match("D task", item_id="d", status="met", similarity=0.9, evidence_type="work")]
    out = {m.item.item_id: m for m in verdict.apply_answers(
        ms, {"a": ("yes", "on the ward"), "b": ("some", None), "c": ("no", None), "d": ("some", None)})}
    assert (out["a"].status, out["a"].reason, out["a"].evidence_type) == ("met", "answered", "self")
    assert scoring.credit(out["a"]) == pytest.approx(scoring.EVIDENCE_STRENGTH["self"] / 0.6)
    assert out["a"].evidence_text == "You answered yes: on the ward"
    assert out["b"].status == "partial" and scoring.credit(out["b"]) == verdict.ANSWER_SOME_CREDIT
    assert out["c"].status == "missing" and out["d"].reason == "semantic"     # no -> unchanged; real evidence kept


@pytest.fixture(scope="module")
def engine():
    return GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=WordEncoder(),
                         translator=None, rephraser=None)


def test_short_description_gets_questions_and_answers_rescore(engine):
    req = GapAnalysisV2Request(soc_code=RN, free_text="Administer medications to patients, record vital signs")
    r = engine.analyze(req)
    assert r.verdict.label == "insufficient_evidence" and r.fit_provisional
    assert verdict.QUESTIONS_MIN <= len(r.follow_up_questions) <= verdict.QUESTIONS_MAX
    assert r.fit_range.low == r.fit_percent <= r.fit_range.high
    answers = [{"requirement_id": q.requirement_id, "answer": "yes"} for q in r.follow_up_questions[:3]]
    answered = engine.analyze(GapAnalysisV2Request(soc_code=RN, free_text=req.free_text, answers=answers))
    assert answered.match_score > r.match_score
    assert answered.evidence_volume.units == r.evidence_volume.units          # answers don't make it "long"
    assert {q.requirement_id for q in answered.follow_up_questions}.isdisjoint({a["requirement_id"] for a in answers})
    assert any(s.reason == "answered" for s in answered.strengths)


def test_long_unrelated_description_is_under_skilled_not_insufficient(engine):
    text = "\n".join(f"- Wrote Java microservices and Kubernetes deployment scripts number {k}." for k in range(8))
    r = engine.analyze(GapAnalysisV2Request(soc_code=RN, free_text="Experience\nDeveloper | Infosys | 2015 - 2024\n" + text))
    assert r.verdict.label == "under_skilled" and r.follow_up_questions == [] and not r.fit_provisional


# --- education lines --------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, education", [
    ("B.Sc Nursing, Government College of Nursing, 2019", True), ("Diploma in Hotel Management", True),
    ("Education: B.A., Delhi University, 2020.", True),
    ("B.Sc Nursing graduate with five years on medical-surgical and ICU floors of a hospital.", False),
    ("Install and connect DBs, MCBs, switches", False)])
def test_education_lines(line, education):
    assert is_education_line("summary", line) is education


def test_education_is_not_task_evidence_but_is_listed(engine):
    text = "Experience\n- Gave IV medicines.\nEducation\nB.Sc Nursing, supervise nursing personnel college, 2019\n"
    units = from_text(text)
    edu = next(u for u in units if u.education)
    assert not edu.matchable and not edu.skill_ids
    r = engine.analyze(GapAnalysisV2Request(soc_code=RN, free_text=text))
    assert r.qualifications == ["B.Sc Nursing, supervise nursing personnel college, 2019"]
    assert all(not (s.evidence and "B.Sc" in s.evidence.text) for s in r.strengths + r.gaps)


# --- roadmap: expected gain, tech cap, basics -------------------------------------------------------------

def test_expected_gain_uses_type_shares_and_missing_credit():
    ms = [match("Big task", importance=0.9), match("Small task", importance=0.3),
          match("Half task", importance=0.9, status="partial", similarity=0.5)] + \
         [match(f"Tool {k}", "tool", importance=0.9) for k in range(10)]
    gain = roadmap.expected_gain(ms, scoring.credit)
    assert gain["task:Big task"] > gain["task:Half task"] > gain["task:Small task"]      # keyed by requirement_id
    assert gain["task:Big task"] > gain["tool:Tool 0"]                                  # tools share 0.05 over 10 items


def test_plan_caps_tech_and_moves_office_software_to_basics():
    tasks = [match(f"Task {k}", importance=0.5) for k in range(3)]
    tech = [match(n, "tech", importance=0.9) for n in ("Epic Systems", "MEDITECH software", "Kronos Workforce",
                                                         "Cerner Millennium", "Microsoft Outlook", "Microsoft Excel")]
    ms = tasks + tech
    main, later, basics = roadmap.plan(ms, ms, scoring.credit, set(), market_skills=[])
    assert sum(m.item.item_type in ("tech", "tool") for m in main) == roadmap.ROADMAP_MAX_TECH
    assert {m.item.name for m in basics} == {"Microsoft Outlook", "Microsoft Excel"}
    assert any(m.item.item_type == "tech" for m in later)
    _, _, basics = roadmap.plan(ms, ms, scoring.credit, set(), market_skills=["Advanced Excel"])
    assert {m.item.name for m in basics} == {"Microsoft Outlook"}             # Excel is a market skill here


def test_v22_meta_and_tools_through_the_client(tmp_path):
    import httpx

    from src.general.m1_client import M1Client

    def handler(request):
        if request.url.path == "/api/v1/meta":
            return httpx.Response(200, json={"schema_version": "2.2.0", "built_at": "2026-10-06T12:22:02Z"})
        return httpx.Response(200, json=[])
    client = M1Client("http://m1", client=httpx.Client(transport=httpx.MockTransport(handler)), cache_dir=tmp_path)
    assert client.version() == "2.2.0+2026-10-06T12:22"            # build time cut to 16 characters
    with pytest.raises(M1Error):
        FixtureM1Client().requirements("00-0000.00")
