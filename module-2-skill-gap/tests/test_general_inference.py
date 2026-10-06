"""General engine (v2) - generic O*NET layers inferred, not matched: GWAs from met DWAs, knowledge/skills as
"draws on" (never gaps), abilities as fit indicators."""
import pytest

from src.general.evidence import EvidenceUnit
from src.general.inference import DRAWS_ON_TOP, FIT_TOP, gwa_of, infer
from src.general.matcher import match
from src.general.requirements import normalise
from test_general_matcher import WordEncoder


@pytest.mark.parametrize("item_id, gwa", [
    ("4.A.4.a.5.c.3", "4.A.4.a.5"),              # module 1's DWA ids
    ("4.A.2.a.4.I01.D03", "4.A.2.a.4"),          # O*NET DWA under an IWA
    ("4.A.2.a.4.I01", "4.A.2.a.4"),              # IWA
    ("4.A.1.b.3", "4.A.1.b.3")])
def test_gwa_of(item_id, gwa):
    assert gwa_of(item_id) == gwa


def r(item_type, item_id, name, importance=0.8):
    return {"soc_code": "s", "item_type": item_type, "item_id": item_id, "item_name": name, "item_description": name,
            "importance_norm": importance, "reliable": 1}


ROWS = [
    r("work_activity", "4.A.4.a.5", "Assisting and Caring for Others", 0.98),
    r("work_activity", "4.A.3.b.6", "Documenting Recording Information", 0.9),
    r("work_activity", "4.A.1.a.1", "Getting Information", 0.7),
    r("dwa", "4.A.4.a.5.c.6", "Administer non-intravenous medications"),
    r("dwa", "4.A.3.b.6.a.1", "Maintain inventory records of medical supplies"),
    r("task", "t1", "Record patients vital signs"),
    r("knowledge", "2.C.5.a", "Administer medications", 0.84),
    r("knowledge", "2.C.4.b", "Physics", 0.2),
    r("skill", "2.A.1.b", "Active Listening", 0.75),
    r("ability", "1.A.1.a.1", "Oral Comprehension", 0.7),
    r("ability", "1.A.1.a.2", "Near Vision", 0.6),
]
UNITS = [EvidenceUnit(text="Administer non-intravenous medications to ward patients", evidence_type="work",
                      section="experience"),
         EvidenceUnit(text="Record patients vital signs every hour", evidence_type="work", section="experience")]


@pytest.fixture
def inferred():
    items, _ = normalise(ROWS)
    enc = WordEncoder()
    return infer(items, match(items, UNITS, enc), UNITS, enc)


def test_work_activity_evidenced_through_its_dwas(inferred):
    by = {w.item.name: w for w in inferred.work_activities}
    assert by["Assisting and Caring for Others"].status == "evidenced"
    assert by["Assisting and Caring for Others"].via == ["Administer non-intravenous medications"]
    assert by["Documenting Recording Information"].status == "not_evidenced"      # its DWA isn't met
    assert by["Getting Information"].status == "no_dwa_data"                        # module 1 sent none under it
    assert [w.item.name for w in inferred.work_activities][0] == "Assisting and Caring for Others"   # by importance


def test_knowledge_and_skills_are_reported_as_draws_on_never_as_gaps(inferred):
    by = {d.item.name: d for d in inferred.draws_on}
    medicine = by["Administer medications"]
    assert medicine.inferred and medicine.support_kind == "dwa" and "medications" in medicine.support
    assert not by["Active Listening"].inferred and by["Active Listening"].support is None
    assert len([d for d in inferred.draws_on if d.item.item_type == "knowledge"]) <= DRAWS_ON_TOP["knowledge"]
    assert not hasattr(inferred.draws_on[0], "status")                              # no met/missing for these


def test_abilities_are_fit_indicators_by_importance(inferred):
    assert [f.name for f in inferred.fit_indicators] == ["Oral Comprehension", "Near Vision"]
    assert len(inferred.fit_indicators) <= FIT_TOP
