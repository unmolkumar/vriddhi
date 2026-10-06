"""General engine (v2) - requirement rows -> weighted items: layers, provenance, every named filter, no-ops on clean
data, and the module 1 v2.1 and v2.0 exports."""
from pathlib import Path

import pytest

from src.general.m1_client import FixtureM1Client
from src.general.requirements import (
    CURATED_WEIGHT, DEFAULT_LEVEL, LAYER_WEIGHT, MARKET_CAP, MARKET_MIN_SHARE, MARKET_OFF_DOMAIN_MAX_SHARE,
    OFF_DOMAIN_FACTOR, OFF_DOMAIN_SIM, SCORED_TYPES, domain_texts, noise_reason, normalise, provenance_of,
)

SOC = "13-2011.00"
V20 = Path(__file__).parent / "mocks" / "m1_occupation_requirements_export_v2.0.json"
V21 = Path(__file__).parent / "mocks" / "m1_occupation_requirements_export_v2.1.json"


def row(item_type, name, **kw):
    base = {"soc_code": SOC, "item_type": item_type, "item_id": name, "item_name": name, "item_description": "",
            "importance_norm": 0.8, "level_norm": 0.5, "hot_technology": 0, "in_demand": 0,
            "india_demand_share": None, "source": "onet", "reliable": 1}
    base.update(kw)
    return base


def market(name, share, **kw):
    return row("market_skill", name, india_demand_share=share, source="india_postings", **kw)


def test_layers_weights_and_levels():
    items, _ = normalise([row("task", "Prepare tax returns", importance_norm=0.9, level_norm=None),
                          row("skill", "Active Listening", importance_norm=0.6, level_norm=0.4),
                          row("knowledge", "Economics and Accounting"), row("ability", "Oral Expression"),
                          row("dwa", "Reconcile accounts"), row("tool", "Calculator")])
    by = {i.item_type: i for i in items}
    assert SCORED_TYPES == ("market_skill", "tech", "tool", "task", "dwa")
    assert by["task"].layer == "core" and by["task"].weight == pytest.approx(0.9 * LAYER_WEIGHT["core"])
    assert by["task"].level == DEFAULT_LEVEL["task"]                       # level_norm null -> default per type
    assert by["skill"].layer == by["knowledge"].layer == "transferable"   # inferred, never scored
    assert by["skill"].weight == pytest.approx(0.6 * 0.5) and by["skill"].level == 0.4
    assert by["ability"].layer == "fit_indicator" and by["ability"].weight == 0.0
    assert by["dwa"].layer == by["tool"].layer == "core"


def test_embedding_text_skips_empty_or_generic_descriptions():
    items, _ = normalise([row("skill", "Active Listening", item_description="Giving full attention."),
                          market("Tally ERP", 0.2, item_description="Extracted from Indian job postings"),
                          row("market_skill", "GST Filing", source="curated", item_description="Curated domain competency"),
                          row("task", "Process invoices.", item_description="Process invoices.")])
    assert [i.text for i in items] == ["Active Listening: Giving full attention.", "Tally ERP", "GST Filing",
                                       "Process invoices."]


@pytest.mark.parametrize("r, version, provenance", [
    (row("task", "Audit books"), "2.2.0", "onet"),
    (market("Tally ERP", 0.15), "2.2.0", "india_postings"),
    (row("market_skill", "GST Filing", source="curated"), "2.2.0", "curated"),
    (row("tool", "Cash counting machine", item_id="tool_cash_counting_machine"), "2.1.0", "curated"),  # hand-written
    (row("tool", "Autoclaves", item_id="tool_autoclaves"), "2.2.0", "onet"),          # real O*NET Tools Used from v2.2
    (row("tool", "Autoclaves", item_id="tool_autoclaves"), "2.2.0+f81d5eb087e03531", "onet"),
    (row("tool", "Autoclaves", item_id="tool_autoclaves"), None, "onet"),             # unknown version: the latest
    (row("tool", "Calculator", item_id="43211503"), "2.1.0", "onet"),
    (row("tech", "Microsoft Excel", item_id="tech_microsoft_excel"), "2.1.0", "onet")])
def test_provenance(r, version, provenance):
    assert provenance_of(r, version) == provenance


def test_curated_rows_are_down_weighted_flagged_and_never_carry_a_share():
    items, report = normalise([row("market_skill", "GST Filing", source="curated", importance_norm=0.8,
                                   india_demand_share=0.9),
                               row("tool", "Cash counting machine", item_id="tool_cash_counting_machine",
                                   importance_norm=0.65),
                               market("Accounting", 0.25, importance_norm=0.5)], m1_version="2.1.0")
    by = {i.name: i for i in items}
    gst, tool, acc = by["GST Filing"], by["Cash counting machine"], by["Accounting"]
    assert gst.provenance == tool.provenance == "curated" and acc.provenance == "india_postings"
    assert gst.weight == pytest.approx(0.8 * CURATED_WEIGHT) and gst.flags == ["curated"]
    assert gst.india_demand_share is None                                  # never presented as market data
    assert tool.weight == pytest.approx(0.65 * CURATED_WEIGHT) and acc.weight == pytest.approx(0.5)
    assert report.down_weighted == {"curated": ["GST Filing", "Cash counting machine"]}


def test_duplicates_and_unreliable_rows_are_dropped():
    items, report = normalise([row("tech", "Microsoft Excel", importance_norm=0.5),
                               row("tech", "microsoft  excel", importance_norm=0.85),
                               row("knowledge", "Biology", reliable=0)])
    assert [(i.name, i.importance) for i in items] == [("microsoft  excel", 0.85)]   # the stronger copy is kept
    assert report.dropped == {"duplicate": ["microsoft  excel"], "unreliable": ["Biology"]}


def test_market_support_floor_is_a_safety_net_and_switches_to_posting_count():
    items, report = normalise([market("GST Filing", 0.114), market("Contractor billing", 0.016),
                               market("one posting", MARKET_MIN_SHARE - 0.002),
                               row("market_skill", "Curated skill", source="curated")])   # no share: exempt
    assert {i.name for i in items} == {"GST Filing", "Contractor billing", "Curated skill"}
    assert report.dropped["low_support"] == ["one posting"]
    # module 1 sends posting_count: it decides, whatever the share
    items, report = normalise([market("Ind AS", 0.001, posting_count=4), market("Big share", 0.3, posting_count=2)])
    assert [i.name for i in items] == ["Ind AS"] and report.dropped["low_support"] == ["Big share"]


@pytest.mark.parametrize("name, reason", [
    ("Hotels", "industry_label"), ("IT Hardware", "industry_label"), ("ITES", "industry_label"),
    ("Medical", "industry_label"), ("IT Software - eCommerce", "industry_label"), ("Ahmedabad", "place"),
    ("Gujarat", "place"), ("Senior", "seniority"), ("Entry level", "seniority"), ("Site Engineer", "job_title"),
    ("Health insurance", "benefit"), ("VISION", "benefit"), ("Accountant", "occupation_title"),
    ("GST Filing & Compliance", None), ("Site Engineering", None), ("Teaching", None)])
def test_noise_list(name, reason):
    assert noise_reason(name, {"accountants and auditors", "accountant", "auditor"}) == reason


def test_noise_is_dropped_and_reported_for_market_skills_only():
    items, report = normalise([market("Hotels", 1.0), market("Accounting", 0.245), market("Accountants", 0.3),
                               row("knowledge", "Medical")], title="Accountants and Auditors")
    assert {i.name for i in items} == {"Accounting", "Medical"}       # 'Medical' as knowledge is not a label
    assert report.dropped == {"industry_label": ["Hotels"], "occupation_title": ["Accountants"]}


def test_posting_market_skills_capped_by_share_curated_never():
    rows = [market(f"skill {n}", 0.03 + n / 1000) for n in range(MARKET_CAP + 5)]
    rows.append(row("market_skill", "Curated", source="curated"))
    items, report = normalise(rows)
    assert len(items) == MARKET_CAP + 1 and len(report.dropped["market_cap"]) == 5
    assert "Curated" in {i.name for i in items}


def test_off_domain_down_weights_tech_and_low_share_posting_skills_only():
    sims = {"Intuit QuickBooks": 0.44, "Epic Systems": OFF_DOMAIN_SIM - 0.06, "HTML": 0.05, "Accounting": 0.05,
            "Curated thing": 0.05}
    items, report = normalise([row("tech", "Intuit QuickBooks", importance_norm=0.85),
                               row("tech", "Epic Systems", importance_norm=0.85),
                               market("HTML", MARKET_OFF_DOMAIN_MAX_SHARE - 0.016),   # low share: checked
                               market("Accounting", MARKET_OFF_DOMAIN_MAX_SHARE + 0.2),   # high share: exempt
                               row("market_skill", "Curated thing", source="curated"),   # curated: exempt
                               row("task", "Audit books")],
                              domain_similarity=lambda texts: [sims[t] for t in texts])
    by = {i.name: i for i in items}
    assert by["Intuit QuickBooks"].weight == pytest.approx(0.85) and not by["Intuit QuickBooks"].flags
    assert by["Epic Systems"].weight == pytest.approx(0.85 * OFF_DOMAIN_FACTOR)
    assert by["Epic Systems"].flags == ["off_domain(0.12)"]
    assert by["HTML"].flags == ["off_domain(0.05)"] and not by["Accounting"].flags
    assert by["Curated thing"].flags == ["curated"]
    assert report.down_weighted["off_domain"] == ["Epic Systems", "HTML"]


def test_domain_texts_use_module_1_labels():
    assert domain_texts("Accountants", {"major_group_title": "Business and Financial Operations",
                                        "career_cluster": None, "india_industry": "Banking"}, "desc") == \
        ["Accountants", "Business and Financial Operations", "Banking"]
    assert domain_texts("Accountants", None, "Examine records.") == ["Accountants", "Examine records."]


def test_clean_data_passes_through_untouched():
    clean = [market("Tally ERP", 0.15), row("tech", "Intuit QuickBooks"), row("task", "Prepare tax returns"),
             row("dwa", "Reconcile accounts"), row("skill", "Critical Thinking")]
    items, report = normalise(clean, title="Accountants", domain_similarity=lambda texts: [0.9] * len(texts))
    assert len(items) == 5 and report.dropped == {} and report.down_weighted == {}
    assert all(i.flags == [] and i.reliability == 1.0 for i in items)


def test_v22_export_accountants():
    """Module 1 v2.2: real O*NET tools (tool_* ids are onet now), curated market skills at importance 0.50,
    posting-derived market skills with posting_count, india_relevant flags."""
    f = FixtureM1Client()
    assert f.version() == "2.2.0"
    items, report = normalise(f.requirements(SOC), title=f.occupations[SOC]["title"], m1_version=f.version())
    tools = [i for i in items if i.item_type == "tool"]
    assert len(tools) > 10 and all(i.provenance == "onet" and not i.flags for i in tools)
    curated = [i for i in items if i.provenance == "curated"]
    assert curated and all(i.item_type == "market_skill" and i.importance == 0.5 for i in curated)
    rows = FixtureM1Client().requirements("15-2051.00")
    ds, ds_report = normalise(rows, title="Data Scientists", m1_version="2.2.0")
    assert all(r.get("posting_count", 0) >= 3 for r in rows if r["source"] == "india_postings")
    assert "low_support" not in ds_report.dropped                      # module 1 already keeps >= 3 postings
    assert any(i.name == "Machine Learning" and i.india_demand_share for i in ds)


def test_v21_export_accountants():
    f = FixtureM1Client(V21)
    assert f.version() == "2.1.0"
    items, report = normalise(f.requirements(SOC), title=f.occupations[SOC]["title"], m1_version=f.version())
    assert report.rows_in == 472 and "duplicate" not in report.dropped and "industry_label" not in report.dropped
    by_type = {t: [i for i in items if i.item_type == t] for t in ("market_skill", "tool", "dwa")}
    assert len(by_type["dwa"]) == 28 and all(i.provenance == "onet" for i in by_type["dwa"])
    assert by_type["tool"] and all(i.provenance == "curated" for i in by_type["tool"])     # tool_* rows
    assert {i.name for i in by_type["market_skill"]} >= {"Tally ERP", "GST Filing & Compliance"}
    assert all(i.provenance == "curated" and i.india_demand_share is None for i in by_type["market_skill"])


def test_v20_export_filters_still_catch_its_noise():
    """Regression on module 1 v2.0's export: duplicates, industry labels and one-posting tails."""
    f = FixtureM1Client(V20)
    assert f.version() == "2.0.0"
    items, report = normalise(f.requirements(SOC), title=f.occupations[SOC]["title"])
    assert report.rows_in == 674 and len(report.dropped["duplicate"]) == 237
    assert report.dropped["industry_label"] == ["Hotels"]
    ds = "15-2051.00"
    ds_items, ds_report = normalise(f.requirements(ds), title=f.occupations[ds]["title"])
    assert len(ds_report.dropped["low_support"]) > 1000                  # single-posting tail (< 0.005)
    assert 7 < sum(i.item_type == "market_skill" for i in ds_items) <= MARKET_CAP
