"""General engine (v2) - roadmap: ordering with taxonomy prerequisites, hours, weeks, practice ideas."""
import numpy as np
import pytest

from src.general import roadmap
from src.general.matcher import RequirementMatch
from src.general.requirements import normalise


def gap(name, item_type="tech", importance=0.85, status="missing", item_id=None):
    items, _ = normalise([{"soc_code": "s", "item_type": item_type, "item_id": item_id or name, "item_name": name,
                           "importance_norm": importance, "reliable": 1}])
    return RequirementMatch(item=items[0], provenance="onet", status=status, similarity=0.2, reason="none")


def test_prerequisites_come_first_and_are_listed():
    spark, python = gap("Apache Spark", importance=0.9), gap("Python", importance=0.5)
    ordered = roadmap.order([spark, python], known=set())
    assert [m.item.name for m in ordered] == ["Python", "Apache Spark"]          # python is a prerequisite of spark
    assert roadmap.prerequisites(spark, known=set()) == ["Python", "SQL"]
    assert roadmap.prerequisites(spark, known={"python"}) == ["SQL"]             # already in the evidence
    assert roadmap.order([spark, python], known={"python"})[0].item.name == "Apache Spark"


def test_hours_from_taxonomy_tier_or_type_table():
    excel = roadmap.hours(gap("Microsoft Excel"), job_zone=3)                    # taxonomy tier 1: 10-25 h
    assert (excel.low, excel.high) == (10, 25)
    task = roadmap.hours(gap("Prepare tax returns", item_type="task"), job_zone=3)
    mid = roadmap.HOURS_PER_LEVEL["task"] * 0.6 * roadmap.JOB_ZONE_FACTOR[3]     # level 0.6 still to reach
    assert (task.low, task.high) == (roadmap._round(mid * 0.7), roadmap._round(mid * 1.3)) == (25, 45)
    senior = roadmap.hours(gap("Prepare tax returns", item_type="task"), job_zone=5)
    partial = roadmap.hours(gap("Prepare tax returns", item_type="task", status="partial"), job_zone=3)
    assert senior.high > task.high > partial.high


def test_weeks_from_hours_per_week():
    h = roadmap.hours(gap("Microsoft Excel"), job_zone=3)
    assert roadmap.weeks(h, None) is None
    w = roadmap.weeks(h, 5)
    assert (w.low, w.high) == (2, 5)


def test_practice_ideas_are_other_unmet_tasks_above_the_bar():
    a, b, c = (gap(n, item_type="task", item_id=k) for n, k in (("Audit ledgers", "a"), ("Reconcile banks", "b"),
                                                               ("Bake bread", "c")))
    vecs = {"a": np.array([1.0, 0.0]), "b": np.array([0.8, 0.6]), "c": np.array([0.0, 1.0])}
    ideas = roadmap.practice_ideas([a, b, c], vecs)
    assert ideas["a"] == ["Reconcile banks"]                                   # itself excluded; 'Bake bread' too far
    assert ideas["c"] == ["Reconcile banks"] and roadmap.PRACTICE_MIN_SIM <= 0.6
    tech = gap("Microsoft Excel", item_id="x")
    assert roadmap.practice_ideas([tech, a], {"x": np.array([1.0, 0.0]), "a": vecs["a"]})["x"] == ["Audit ledgers"]


@pytest.mark.parametrize("status", ["missing", "partial"])
def test_roadmap_hours_are_ranges(status):
    h = roadmap.hours(gap("Prepare tax returns", item_type="task", status=status), job_zone=4)
    assert 0 < h.low < h.high and h.low % roadmap.HOURS_ROUND == 0
