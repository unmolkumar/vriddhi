"""General engine (v2) - requirement rows -> weighted items: layers, every named filter, and no-ops on clean data."""
import pytest

from src.general.m1_client import FixtureM1Client
from src.general.requirements import (
    DEFAULT_LEVEL, LAYER_WEIGHT, MARKET_CAP, MARKET_MIN_SHARE, OFF_DOMAIN_FACTOR, OFF_DOMAIN_SIM, domain_texts,
    noise_reason, normalise,
)

SOC = "13-2011.00"


def row(item_type, name, **kw):
    base = {"soc_code": SOC, "item_type": item_type, "item_id": name, "item_name": name, "item_description": "",
            "importance_norm": 0.8, "level_norm": 0.5, "hot_technology": 0, "in_demand": 0,
            "india_demand_share": None, "source": "onet", "reliable": 1}
    base.update(kw)
    return base


def test_layers_weights_and_levels():
    items, _ = normalise([row("task", "Prepare tax returns", importance_norm=0.9, level_norm=None),
                          row("skill", "Active Listening", importance_norm=0.6, level_norm=0.4),
                          row("ability", "Oral Expression"),
                          row("dwa", "Reconcile accounts"), row("tool", "Calculator")])
    by = {i.item_type: i for i in items}
    assert by["task"].layer == "core" and by["task"].weight == pytest.approx(0.9 * LAYER_WEIGHT["core"])
    assert by["task"].level == DEFAULT_LEVEL["task"]                       # level_norm null -> default per type
    assert by["skill"].layer == "transferable" and by["skill"].weight == pytest.approx(0.6 * 0.5) and by["skill"].level == 0.4
    assert by["ability"].layer == "fit_indicator" and by["ability"].weight == 0.0
    assert by["dwa"].layer == by["tool"].layer == "core"                   # supported though the fixture has none


def test_embedding_text_skips_empty_or_generic_descriptions():
    items, _ = normalise([row("skill", "Active Listening", item_description="Giving full attention."),
                          row("market_skill", "Tally ERP", item_description="Extracted from Indian job postings",
                              india_demand_share=0.2, source="india_postings"),
                          row("task", "Process invoices.", item_description="Process invoices.")])
    assert [i.text for i in items] == ["Active Listening: Giving full attention.", "Tally ERP", "Process invoices."]


def test_duplicates_and_unreliable_rows_are_dropped():
    items, report = normalise([row("tech", "Microsoft Excel", importance_norm=0.5),
                               row("tech", "microsoft  excel", importance_norm=0.85),
                               row("knowledge", "Biology", reliable=0)])
    assert [(i.name, i.importance) for i in items] == [("microsoft  excel", 0.85)]   # the stronger copy is kept
    assert report.dropped == {"duplicate": ["microsoft  excel"], "unreliable": ["Biology"]}


def test_market_skill_support_floor_and_posting_count():
    items, report = normalise([
        row("market_skill", "GST Filing", india_demand_share=0.114),
        row("market_skill", "Contractor billing", india_demand_share=MARKET_MIN_SHARE - 0.004),
        row("market_skill", "Ind AS", india_demand_share=0.01, posting_count=4)])     # few by share, enough postings
    assert {i.name for i in items} == {"GST Filing", "Ind AS"}
    assert report.dropped["low_support"] == ["Contractor billing"]


@pytest.mark.parametrize("name, reason", [
    ("Hotels", "industry_label"), ("IT Hardware", "industry_label"), ("ITES", "industry_label"),
    ("Medical", "industry_label"), ("IT Software - eCommerce", "industry_label"), ("Ahmedabad", "place"),
    ("Gujarat", "place"), ("Senior", "seniority"), ("Entry level", "seniority"), ("Site Engineer", "job_title"),
    ("Health insurance", "benefit"), ("VISION", "benefit"), ("Accountant", "occupation_title"),
    ("GST Filing & Compliance", None), ("Site Engineering", None), ("Teaching", None)])
def test_noise_list(name, reason):
    assert noise_reason(name, {"accountants and auditors", "accountant", "auditor"}) == reason


def test_noise_is_dropped_and_reported_for_market_skills_only():
    items, report = normalise([row("market_skill", "Hotels", india_demand_share=1.0),
                               row("market_skill", "Accounting", india_demand_share=0.245),
                               row("market_skill", "Accountants", india_demand_share=0.3),
                               row("knowledge", "Medical")], title="Accountants and Auditors")
    assert {i.name for i in items} == {"Accounting", "Medical"}       # 'Medical' as knowledge is not a label
    assert report.dropped == {"industry_label": ["Hotels"], "occupation_title": ["Accountants"]}


def test_market_skills_capped_by_share():
    rows = [row("market_skill", f"skill {n}", india_demand_share=0.03 + n / 1000) for n in range(MARKET_CAP + 5)]
    items, report = normalise(rows)
    assert len(items) == MARKET_CAP and len(report.dropped["market_cap"]) == 5
    assert min(i.india_demand_share for i in items) > max(0.03 + n / 1000 for n in range(5)) - 1e-9


def test_off_domain_tech_is_down_weighted_not_deleted():
    sims = {"Intuit QuickBooks": 0.44, "Epic Systems": OFF_DOMAIN_SIM - 0.06}
    items, report = normalise([row("tech", "Intuit QuickBooks", importance_norm=0.85),
                               row("tech", "Epic Systems", importance_norm=0.85), row("task", "Audit books")],
                              domain_similarity=lambda texts: [sims[t] for t in texts])
    by = {i.name: i for i in items}
    assert by["Intuit QuickBooks"].weight == pytest.approx(0.85) and not by["Intuit QuickBooks"].flags
    assert by["Epic Systems"].weight == pytest.approx(0.85 * OFF_DOMAIN_FACTOR)
    assert by["Epic Systems"].flags == ["off_domain(0.12)"] and report.down_weighted == {"off_domain": ["Epic Systems"]}


def test_domain_texts_use_module_1_labels():
    assert domain_texts("Accountants", {"major_group_title": "Business and Financial Operations",
                                        "career_cluster": None, "india_industry": "Banking"}, "desc") == \
        ["Accountants", "Business and Financial Operations", "Banking"]
    assert domain_texts("Accountants", None, "Examine records.") == ["Accountants", "Examine records."]


def test_clean_data_passes_through_untouched():
    clean = [row("market_skill", "Tally ERP", india_demand_share=0.15), row("tech", "Intuit QuickBooks"),
             row("task", "Prepare tax returns"), row("skill", "Critical Thinking")]
    items, report = normalise(clean, title="Accountants", domain_similarity=lambda texts: [0.9] * len(texts))
    assert len(items) == 4 and report.dropped == {} and report.down_weighted == {}
    assert all(i.flags == [] for i in items)


def test_fixture_filter_report_for_accountants():
    f = FixtureM1Client()
    items, report = normalise(f.requirements(SOC), title=f.occupations[SOC]["title"])
    assert report.rows_in == 674
    assert report.dropped["industry_label"] == ["Hotels"]
    assert len(report.dropped["duplicate"]) == 237                         # module 1 v2.0 exports each tech row twice
    names = {i.name for i in items if i.item_type == "market_skill"}
    assert {"Tally ERP", "GST Filing & Compliance", "Accounting"} <= names
