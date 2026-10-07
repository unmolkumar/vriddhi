"""v2 job search (any occupation): relevance, queries, matching re-blend, ranking, unlocks, fallbacks, API.
Offline: listings and module 1 / module 2 responses recorded from live runs (tests/v2/fixtures, replay.py)."""
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from replay import ReplayTransport  # noqa: E402

from src.engines.job_store import JobStore  # noqa: E402
from src.general import engine, m1, relevance  # noqa: E402
from src.general.fetch import KEY_PREFIX, fetch  # noqa: E402
from src.general.m1 import Match  # noqa: E402
from src.general.schemas import JobSearchV2Request, JobSearchV2Response, export_json_schema  # noqa: E402
from src.models.schemas import Job  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def replay_clients(fixture: dict, tmp_path) -> tuple[engine.Clients, ReplayTransport]:
    """Listings seeded as a fresh cache, module 1 and 2 answered from the recording, no provider calls."""
    store = JobStore(tmp_path / "jobs.sqlite")
    for key, entry in fixture["listings"].items():
        query, city = key.split("|")
        store.save(KEY_PREFIX + query, city, entry["source"], [Job.model_validate(j) for j in entry["jobs"]],
                   entry["total"], datetime.fromisoformat(entry["fetched_at"]))
    transport = ReplayTransport(fixture["recorded"])
    client = httpx.Client(transport=transport)
    # Queries Adzuna answered with no listings aren't cached: replay that answer
    empty = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [], "count": 0})))
    return engine.Clients(providers=empty, m1=client, m2=client, store=store,
                          titles=m1.TitleCache(tmp_path / "titles.sqlite"), now=datetime.fromisoformat(fixture["now"])), transport


def run(fixture: dict, kind: str, tmp_path):
    clients, transport = replay_clients(fixture, tmp_path)
    r = engine.search(JobSearchV2Request(**fixture["profiles"][kind]), clients)
    assert not transport.misses, transport.misses[:3]
    return r


@pytest.fixture(scope="module")
def electrician():
    return load("electrician_delhi_ncr.json")


# --- recorded searches ----------------------------------------------------------------------------------------

def test_electrician_delhi_ncr_relevance_and_ranking(electrician, tmp_path):
    r = run(electrician, "full", tmp_path)
    s = r.relevance_summary
    assert (r.resolution.soc_code, r.queries, r.cities) == ("47-2111.00", ["electrician"], ["Delhi", "Noida", "Gurugram"])
    assert (s.listings, s.on_target, s.adjacent, s.off_target_dropped) == (117, 1, 20, 96)
    assert r.jobs[0].title == "Electrician - Noida" and r.jobs[0].relevance.label == "on_target"
    assert [j.rank for j in r.jobs] == list(range(1, len(r.jobs) + 1))
    assert [j.rank_score for j in r.jobs] == sorted((j.rank_score for j in r.jobs), reverse=True)
    for j in r.jobs:
        assert set(j.rank_components) == set(engine.RANK_WEIGHTS)
        assert j.rank_score == pytest.approx(sum(engine.RANK_WEIGHTS[k] * v for k, v in j.rank_components.items()), abs=1e-3)
        assert j.match.method == "m2_match_texts" and j.relevance.label != "off_target"
    assert all(d.reason for d in r.dropped) and len(r.dropped) == 96
    assert r.module_1_available and r.module_2_available and r.thresholds == engine.THRESHOLDS


def test_full_practitioner_outscores_beginner_and_other_field(electrician, tmp_path):
    full, partial, other = (run(electrician, k, tmp_path / k) for k in ("full", "partial", "other"))
    p = {j.job_id: j.match.match_score for j in partial.jobs}
    o = {j.job_id: j.match.match_score for j in other.jobs}
    assert all(j.match.match_score > p[j.job_id] >= 0 for j in full.jobs)
    assert max(o.values()) < engine.THRESHOLDS["Partial"]
    assert partial.unlocks and all(u.jobs_unlocked >= 1 and "electrician job" in u.message for u in partial.unlocks)


def test_recorded_nurse_and_teacher_searches(tmp_path):
    nurse = run(load("nurse_pune.json"), "full", tmp_path / "n")
    assert nurse.queries == ["staff nurse", "registered nurse"] and nurse.relevance_summary.on_target == 3
    teacher = run(load("teacher_jaipur.json"), "full", tmp_path / "t")
    assert teacher.resolution.soc_code == "25-2031.00" and teacher.queries[0] == "school teacher"


def test_api_route_and_schema(electrician, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api import routes_v2
    from src.api.main import app
    clients, _ = replay_clients(electrician, tmp_path)
    monkeypatch.setattr(routes_v2, "CLIENTS", clients)
    resp = TestClient(app).post("/api/v2/jobs/search", json=electrician["profiles"]["full"])
    assert resp.status_code == 200 and JobSearchV2Response.model_validate(resp.json()).jobs
    bad = TestClient(app).post("/api/v2/jobs/search", json={"target_role": "electrician", "location": "Delhi"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_REQUEST"
    committed = json.loads((Path(__file__).parents[2] / "src" / "models" / "schema_m3_v2.json").read_text(encoding="utf-8"))
    assert committed == export_json_schema(), "run: python -m src.general.schemas"


# --- queries and relevance --------------------------------------------------------------------------------------

@pytest.mark.parametrize("onet, board", [("Chefs and Head Cooks", "chef"), ("Accountants and Auditors", "accountant"),
                                         ("Heavy and Tractor-Trailer Truck Drivers", "tractor-trailer truck driver"),
                                         ("Secondary School Teachers, Except Special and Career/Technical Education",
                                          "secondary school teacher"),
                                         ("Sales Representatives, Wholesale and Manufacturing", "sales representative"),
                                         ("Helpers--Electricians", "helper electrician")])
def test_board_title(onet, board):
    assert relevance.board_title(onet) == board


def test_queries_deduplicate_and_take_the_targets_alias():
    aliases = [Match("29-1141.00", "Registered Nurses", 0.95, "onet_alt_title_exact", "Ward Sister"),
               Match("29-1141.01", "Acute Care Nurses", 0.95, "onet_alt_title_exact", "ICU Nurse")]
    assert relevance.build_queries("Staff Nurses", "Registered Nurses", aliases, "29-1141.00") == \
        ["staff nurses", "registered nurse", "ward sister"]
    assert relevance.build_queries("electrician", "Electricians", [], "47-2111.00") == ["electrician"]
    assert relevance.target_terms(["staff nurse", "data scientist", "sales executive"]) == {"nurse", "scientist", "sales"}


TELLERS, CASHIERS = "43-3071.00", "41-2011.00"


@pytest.mark.parametrize("title, matches, label", [
    ("Teller - SBI", [Match(CASHIERS, "Cashiers", 0.95)], "on_target"),                 # word beats misresolution
    ("Cash Officer", [Match(TELLERS, "Tellers", 0.9)], "on_target"),                     # resolves to the target
    ("Branch Cashier", [Match(CASHIERS, "Cashiers", 0.95)], "adjacent"),                 # a related SOC
    ("Senior Manager", [Match("11-9179.02", "Spa Managers", 0.70)], "off_target"),       # token-match noise
    ("Data Engineer", [Match("15-1243.00", "Database Architects", 0.95)], "off_target"),
    ("Tellering Associate", [], "adjacent")])                                            # shared word stem
def test_classify(title, matches, label):
    r = relevance.classify(title, matches, TELLERS, {TELLERS}, {TELLERS, CASHIERS}, {"teller"})
    assert r.label == label and r.reason


def test_classify_without_a_target_is_unknown():
    assert relevance.classify("Teller", [], None, set(), set(), set()).label == "unknown"


# --- job text, re-blend, experience, unlocks -----------------------------------------------------------------

def test_clean_description_keeps_requirements_only():
    text = ("Career Area: Technology. When you join Acme, you join a global team. Responsibilities: Organize, process "
            "and analyze large data sets. Salary: 6-8 LPA. Build forecasting models in Python. Collaborat�")
    assert engine.clean_description(text) == "Organize, process and analyze large data sets. Build forecasting models in Python."


def test_reblend_leans_on_the_occupation_when_the_job_text_is_thin():
    res = {"match_score": 0.5, "job_text_score": 0.8, "occupation_score": 0.2, "job_requirements": 2,
           "missing": [{"requirement": "a", "provenance": "job_text", "score_gain_if_met": 0.3},
                       {"requirement": "b", "provenance": "onet", "score_gain_if_met": 0.04}]}
    out = engine.reblend(res)
    b = engine.JOB_TEXT_BLEND_MAX * 2 / engine.FULL_CLAUSES
    assert out["blend"] == pytest.approx(b) and out["match_score"] == pytest.approx(b * 0.8 + (1 - b) * 0.2, abs=1e-4)
    gains = {x["requirement"]: x["score_gain_if_met"] for x in out["missing"]}
    assert gains["a"] == pytest.approx(0.3 * b / 0.6, abs=1e-4) and gains["b"] == pytest.approx(0.04 * (1 - b) / 0.4, abs=1e-4)


def test_experience_band_from_posting_reliable_india_band_or_job_zone():
    zone4 = {"job_zone": {"job_zone": 4}}
    stale = {"indian_experience": {"typical_min": 1.3, "typical_max": 3.3, "sample_size": 3, "years_covered": "2015-2016"}}
    fresh = {"indian_experience": {"typical_min": 1.3, "typical_max": 3.3, "sample_size": 80, "years_covered": "2023-2025"}}
    assert engine.india_band(stale | zone4) == ((2.0, 6.0), "job_zone")
    assert engine.india_band(fresh | zone4) == ((1.3, 3.3), "india_postings")
    job = Job(job_id="a:1", title="Electrician", source="adzuna", last_observed_at=datetime(2026, 10, 7),
              experience_min=3, experience_max=5)
    assert engine.experience_fit(job, 4, engine.Target(None, None)).source == "posting"


def test_unlocks_group_by_id_or_text_and_skip_basics():
    def job(i):
        return Job(job_id=f"j{i}", title="Electrician", source="adzuna", last_observed_at=datetime(2026, 10, 7))
    low = engine.JobMatchV2(method="m2_match_texts", match_score=0.08, classification="Partial", soc_code=None,
                            match_confidence="ok")
    gap = {"requirement": "Read electrical blueprints.", "requirement_id": "task:1", "item_type": "task", "score_gain_if_met": 0.05}
    same_text = {**gap, "requirement_id": "job:abc", "requirement": "Read electrical blueprints"}
    basic = {"requirement": "Microsoft Excel", "requirement_id": "tech:x", "item_type": "tech", "score_gain_if_met": 0.2}
    out = engine.find_unlocks([(job(1), low, [gap, basic]), (job(2), low, [same_text])], "electrician", "Delhi NCR")
    assert len(out) == 1 and out[0].jobs_unlocked == 2 and set(out[0].requirement_ids) == {"task:1", "job:abc"}
    assert out[0].message == "Learning to read electrical blueprints would move 2 more electrician jobs in Delhi NCR to a good match."


# --- fallbacks --------------------------------------------------------------------------------------------------

def test_module_1_and_2_down_never_crash(electrician, tmp_path):
    clients, _ = replay_clients(electrician, tmp_path)

    def down(request):
        raise httpx.ConnectError("refused")
    dead = httpx.Client(transport=httpx.MockTransport(down))
    clients.m1 = clients.m2 = dead
    r = engine.search(JobSearchV2Request(**electrician["profiles"]["full"]), clients)
    assert not r.module_1_available and not r.module_2_available and r.queries == ["electrician"]
    assert r.jobs and all(j.relevance.label == "unknown" and j.match.method == "keywords" for j in r.jobs)
    assert any("Module 1" in w for w in r.warnings) and any("Module 2" in w for w in r.warnings)


def test_jsearch_only_when_adzuna_gives_the_city_nothing(tmp_path, monkeypatch):
    from src.providers import adzuna, jsearch
    calls = []
    job = Job(job_id="adzuna:1", title="Electrician", source="adzuna", last_observed_at=datetime(2026, 10, 7))

    def az(query, city, **k):
        calls.append(("adzuna", query, city))
        return ([job] if (query, city) == ("electrician", "Delhi") else []), 1

    def js(query, city, **k):
        calls.append(("jsearch", query, city))
        return [], 0
    monkeypatch.setattr(adzuna, "search", az)
    monkeypatch.setattr(jsearch, "search", js)
    fetch(["electrician", "wireman"], ["Delhi", "Noida"], store=JobStore(tmp_path / "j.sqlite"))
    assert [c for c in calls if c[0] == "jsearch"] == [("jsearch", "electrician", "Noida")]    # Delhi had Adzuna jobs
    calls.clear()
    fetch(["electrician"], ["Pune"], store=JobStore(tmp_path / "k.sqlite"), use_jsearch=False)
    assert all(c[0] == "adzuna" for c in calls)
