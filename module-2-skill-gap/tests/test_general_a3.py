"""General engine (v2) - A3: requirement clauses, language check and translation, title lines as role history,
India not-applicable list, fit_percent, roadmap focus, prewarm. Word-overlap encoder and fixture module 1."""
import sys

import pytest

from src.api import main as api_main
from src.general import scoring
from src.general.evidence import ENGLISH_MIN_SHARE, EvidenceUnit, english_share, from_text, is_english, role_history
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client
from src.general.matcher import RequirementMatch, classify, score
from src.general.requirements import normalise, requirement_clauses
from src.general.roadmap import ROADMAP_MAX_ITEMS
from src.general.schemas import GapAnalysisV2Request
from src.general.service import GeneralEngine, apply_role_history, india_not_applicable
from src.general.translate import FixtureTranslator, GroqTranslator
from test_general_matcher import WordEncoder

RN, ELECTRICIANS = "29-1141.00", "47-2111.00"


@pytest.fixture(scope="module")
def engine():
    return GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=WordEncoder(), translator=None)


# --- requirement-side clauses ------------------------------------------------------------------------------

def test_requirement_clauses():
    wiring = ("Assemble, install, test, or maintain electrical or electronic wiring, equipment, appliances, apparatus, "
              "or fixtures, using hand tools or power tools.")
    assert requirement_clauses(wiring) == [
        "Assemble, install, test, or maintain electrical or electronic wiring, equipment, appliances, apparatus, or fixtures",
        "Assemble electrical wiring", "install electrical wiring", "test electrical wiring", "maintain electrical wiring"]
    assert requirement_clauses("Administer medications to patients and monitor patients for reactions or side effects.") \
        == ["Administer medications to patients", "monitor patients for reactions or side effects"]
    assert requirement_clauses("Record patients' medical information and vital signs.") == []


def test_a_requirement_is_met_by_its_best_clause():
    items, _ = normalise([{"soc_code": "s", "item_type": "task", "item_name": "Prepare, examine, or analyze accounting "
                           "records, financial statements, or other financial reports to assess accuracy.",
                           "importance_norm": 0.9, "reliable": 1}])
    unit = EvidenceUnit(text="analyze accounting records", evidence_type="work", section="experience")
    [m] = classify(items, [unit], score(items, [unit], WordEncoder()))
    assert m.status == "met" and m.similarity == pytest.approx(1.0)        # whole sentence alone would be far lower


# --- language check and translation -------------------------------------------------------------------------

def test_english_share():
    assert english_share("Lay conduits and pull wires for lighting circuits.") == 1.0
    assert english_share("Maal ko rassi aur tarpal se achhe se baandhta hoon") < ENGLISH_MIN_SHARE
    assert english_share("मैं ट्रक चलाता हूँ") == 0.0 and not is_english("मैं ट्रक चलाता हूँ")
    assert is_english("handled GST returns for 30+ clients and Tally entries")


def test_translation_keeps_original_text_and_span():
    text = "Experience\nSafdarjung Hospital | 2019 - 2024\n- Mareezon ka BP aur pulse har ghante check karti hoon.\n"
    table = {"Mareezon ka BP aur pulse har ghante check karti hoon.": "I check patients' BP and pulse every hour."}
    warnings = []
    units = from_text(text, translator=lambda xs: [table.get(x) for x in xs], warnings=warnings)
    tr = next(u for u in units if u.translated)
    assert tr.text == "I check patients' BP and pulse every hour." and tr.original_text.startswith("Mareezon")
    assert text[tr.span[0]:tr.span[1]] == tr.original_text and not warnings


def test_no_translator_matches_as_written_with_a_warning():
    warnings = []
    units = from_text("Gaadi nikalne se pehle tyre aur brake check karta hoon.", warnings=warnings)
    assert not any(u.translated for u in units) and "non-English" in warnings[0]


def test_groq_translator_is_fail_safe_and_cached(tmp_path, monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise TimeoutError("no network")
    monkeypatch.setitem(sys.modules, "groq", type(sys)("groq"))
    sys.modules["groq"].Groq = Boom
    t = GroqTranslator("key", cache_dir=tmp_path)
    assert t(["Kaam karta hoon"]) == [None]                                # failure: original kept by the caller
    t._store("Kaam karta hoon", "I work")
    assert t(["Kaam karta hoon"]) == ["I work"]                             # served from the cache, no call


def test_fixture_translations_cover_the_hinglish_profiles():
    table = FixtureTranslator().table
    assert len(table) >= 40 and any("ट्रक" in k or "ट्रेलर" in k for k in table)


# --- job-title lines -> role history --------------------------------------------------------------------------

RESUME = """Ramesh Gowda
Electrician (ITI), Bengaluru

Experience
Maintenance Electrician | Brigade Group | Jan 2014 - Dec 2024
- Lay conduits and pull wires for lighting circuits.
"""


def test_title_lines_are_role_history_not_evidence():
    units = from_text(RESUME)
    titles = [u for u in units if u.role_title]
    assert {u.text for u in titles} >= {"Maintenance Electrician | Brigade Group | Jan 2014 - Dec 2024",
                                         "Electrician (ITI), Bengaluru"}
    assert all(not u.skill_ids for u in titles)
    assert any(u.matchable and u.text.startswith("Lay conduits") for u in units)
    assert role_history(RESUME)[0] == ("Maintenance Electrician", 11.0)
    assert ("Electrician", None) in role_history(RESUME)


def test_title_units_never_match_semantically():
    items, _ = normalise([{"soc_code": "s", "item_type": "task", "item_name": "Staff Nurse", "importance_norm": 0.8,
                           "reliable": 1}])
    title = EvidenceUnit(text="Staff Nurse", evidence_type="work", section="experience", role_title=True)
    [m] = classify(items, [title], score(items, [title], WordEncoder()))
    assert m.status == "missing"


def _missing(item_type="task"):
    items, _ = normalise([{"soc_code": "s", "item_type": item_type, "item_name": "Install wiring", "importance_norm": 0.8,
                           "reliable": 1}])
    return RequirementMatch(item=items[0], provenance="onet", status="missing", similarity=0.1, reason="none")


@pytest.mark.parametrize("years, credit", [(2, 2 * scoring.IMPLIED_CREDIT_PER_YEAR), (30, scoring.IMPLIED_CREDIT_MAX),
                                           (None, scoring.IMPLIED_CREDIT_UNKNOWN_YEARS)])
def test_implied_credit_by_years_capped_below_partial(years, credit):
    from src.general.service import Role
    [m] = apply_role_history([_missing()], [Role("Electrician", years, ELECTRICIANS, "Electricians", 1.0)])
    assert (m.status, m.reason) == ("partial", "implied_by_role") and scoring.credit(m) == pytest.approx(credit)
    assert scoring.credit(m) < scoring.PARTIAL_CREDIT and m.evidence_section == "role_history"
    if years:
        assert m.evidence_text == f"{years:g} years as Electrician"


def test_real_evidence_wins_and_names_are_not_implied():
    from src.general.service import Role
    role = [Role("Electrician", 10, ELECTRICIANS, "Electricians", 1.0)]
    real = _missing().model_copy(update={"status": "partial", "reason": "semantic"})
    [kept] = apply_role_history([real], role)
    assert kept.reason == "semantic" and scoring.credit(kept) == scoring.PARTIAL_CREDIT
    [tech] = apply_role_history([_missing("tech")], role)
    assert tech.status == "missing"                                        # only tasks and DWAs are implied


def test_engine_applies_role_history_only_to_the_matching_occupation(engine):
    text = "Experience\nStaff Nurse | Apollo Hospitals | Jan 2018 - Dec 2023\n- Gave injections.\n"
    r = engine.analyze(GapAnalysisV2Request(soc_code=RN, free_text=text))
    assert [(h.title, h.soc_code, h.applies_to_target) for h in r.role_history] == [("Staff Nurse", RN, True)]
    implied = [g for g in r.gaps if g.reason == "implied_by_role"]
    assert implied and implied[0].evidence.text == "6 years as Staff Nurse" and implied[0].status == "partial"
    other = engine.analyze(GapAnalysisV2Request(soc_code=ELECTRICIANS, free_text=text))
    assert not any(g.reason == "implied_by_role" for g in other.gaps)
    assert all(not i.implied_by_role for i in r.roadmap.items[:1])         # real gaps come first


# --- India not applicable -----------------------------------------------------------------------------------

def test_prescribing_is_not_applicable_for_nurses(engine):
    assert ("29-1141.00", "task", "1859") in india_not_applicable()
    r = engine.analyze(GapAnalysisV2Request(soc_code=RN, free_text="Gave IV medicines and charted vital signs."))
    na = {n.requirement: n.reason for n in r.not_applicable_in_india}
    assert any(k.startswith("Prescribe or recommend drugs") for k in na)
    assert all("rescri" in reason for reason in na.values())               # module 1's india_relevant reason (v2.2)
    shown = [g.requirement for g in r.gaps] + [i.requirement for i in r.roadmap.items] + [i.requirement for i in r.roadmap.later]
    assert not any(s.startswith("Prescribe") for s in shown)


# --- fit percent and roadmap focus ----------------------------------------------------------------------------

def test_fit_percent_piecewise_and_labels():
    t, m = scoring.GOOD_FIT_THRESHOLD, scoring.FIT_MEDIAN_FULL
    assert scoring.fit_percent(0) == 0 and scoring.fit_percent(t) == 50 and scoring.fit_percent(m) == 80
    assert scoring.fit_percent(t / 2) == 25 and scoring.fit_percent(1.0) == 100
    assert [scoring.fit_label(p) for p in (90, 60, 30, 10)] == ["Strong fit", "Good fit", "Developing", "Early stage"]


def test_roadmap_main_is_capped_and_the_rest_is_later(engine):
    r = engine.analyze(GapAnalysisV2Request(soc_code=RN, free_text="Gave injections.", hours_per_week=10))
    assert len(r.roadmap.items) == ROADMAP_MAX_ITEMS == 8 and r.roadmap.later
    assert r.roadmap.total_hours.low == sum(i.hours.low for i in r.roadmap.items)
    assert r.fit_percent == scoring.fit_percent(r.match_score) and r.fit_label


# --- prewarm --------------------------------------------------------------------------------------------------

def test_prewarm_prepares_occupations_and_related(engine):
    done = GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=WordEncoder(),
                         translator=None).prewarm([ELECTRICIANS])
    assert done[0] == ELECTRICIANS and "47-2152.00" in done                 # Plumbers: same major group in the fixture


def test_startup_prewarm_from_env(monkeypatch):
    calls = []
    monkeypatch.setenv(api_main.PREWARM_ENV, "29-1141.00, 47-2111.00")
    monkeypatch.setattr(api_main, "get_engine", lambda: type("E", (), {"prewarm": lambda self, s: calls.append(s)})())
    monkeypatch.setattr(api_main.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: target()})())
    api_main._prewarm()
    assert calls == [["29-1141.00", "47-2111.00"]]
