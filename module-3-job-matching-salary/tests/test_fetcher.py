"""Fetch chain: fallback order, cache hit/expiry, stale snapshot, cities, dedupe, expiry, module 2 down."""
from datetime import timedelta

import httpx
import pytest

from conftest import NOW, json_response, load_mock, m2_extract_handler, raises
from src.engines.job_fetcher import MAX_JOB_AGE_DAYS, dedupe, fetch_jobs

ADZ = load_mock("adzuna_search_sample.json")
JS = load_mock("jsearch_search_sample.json")
ADZ_HOST, JS_HOST, M2_HOST = "api.adzuna.com", "jsearch.p.rapidapi.com", "127.0.0.1"


def run(fake_http, store, location="Bengaluru", **kw):
    client = fake_http.client()
    return fetch_jobs("data scientist", location, store=store, client=client, m2_client=client,
                      now=kw.pop("now", NOW), **kw)


def statuses(result):
    return [(a.provider, a.status) for a in result.attempts]


def test_adzuna_first_and_saved(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ)).on(M2_HOST, m2_extract_handler)
    r = run(fake_http, store)
    assert statuses(r) == [("adzuna", "ok")] and r.sources == ["adzuna"] and not r.from_cache
    assert r.total_available == ADZ["count"] and len(r.jobs) == 3 and fake_http.calls(JS_HOST) == 0
    assert all(j.skills_source == "m2" for j in r.jobs)
    assert store.stats() == {"queries": 1, "jobs": 3}


def test_cache_hit_needs_no_network(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ)).on(M2_HOST, m2_extract_handler)
    run(fake_http, store)
    r = run(fake_http, store, now=NOW + timedelta(hours=5))
    assert statuses(r) == [("adzuna", "cache_hit")] and r.from_cache and not r.stale
    assert fake_http.calls(ADZ_HOST) == 1 and fake_http.calls(M2_HOST) == 3   # no new provider or m2 calls
    assert not any(j.stale for j in r.jobs)


def test_cache_expiry_refetches(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ)).on(M2_HOST, m2_extract_handler)
    run(fake_http, store)
    r = run(fake_http, store, now=NOW + timedelta(hours=7))   # TTL is 6 h
    assert statuses(r) == [("adzuna", "ok")] and fake_http.calls(ADZ_HOST) == 2


def test_force_refresh(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ)).on(M2_HOST, m2_extract_handler)
    run(fake_http, store)
    run(fake_http, store, force_refresh=True)
    assert fake_http.calls(ADZ_HOST) == 2


@pytest.mark.parametrize("adzuna_handler, adzuna_status", [
    (raises(httpx.ReadTimeout), "timeout"),
    (json_response({}, 500), "error"),
    (json_response({"results": [], "count": 0}), "empty"),
])
def test_falls_back_to_jsearch(fake_http, store, adzuna_handler, adzuna_status):
    fake_http.on(ADZ_HOST, adzuna_handler).on(JS_HOST, json_response(JS)).on(M2_HOST, m2_extract_handler)
    r = run(fake_http, store)
    assert statuses(r) == [("adzuna", adzuna_status), ("jsearch", "ok")]
    assert r.sources == ["jsearch"] and {j.source for j in r.jobs} == {"jsearch"}
    assert "jsearch:js-004" not in {j.job_id for j in r.jobs}   # expired listing dropped


def test_unconfigured_adzuna_is_skipped(monkeypatch, fake_http, store):
    monkeypatch.delenv("ADZUNA_APP_ID")
    fake_http.on(JS_HOST, json_response(JS)).on(M2_HOST, m2_extract_handler)
    r = run(fake_http, store)
    assert statuses(r) == [("adzuna", "not_configured"), ("jsearch", "ok")] and fake_http.calls(ADZ_HOST) == 0


def test_stale_snapshot_when_all_providers_fail(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ)).on(M2_HOST, m2_extract_handler)
    run(fake_http, store)
    fake_http.on(ADZ_HOST, raises(httpx.ReadTimeout)).on(JS_HOST, json_response({"message": "You are not subscribed to this API."}, 403))
    r = run(fake_http, store, now=NOW + timedelta(hours=30))
    assert statuses(r) == [("adzuna", "timeout"), ("jsearch", "not_subscribed"), ("snapshot", "used")]
    assert r.stale and r.sources == ["snapshot"] and len(r.jobs) == 3
    assert all(j.stale and j.age_hours == 30.0 for j in r.jobs)        # labelled, never served as fresh
    assert any("last fetched 30.0 hours ago" in w for w in r.warnings)


def test_nothing_available(fake_http, store):
    fake_http.on(ADZ_HOST, raises(httpx.ReadTimeout)).on(JS_HOST, raises(httpx.ConnectError))
    r = run(fake_http, store)
    assert r.jobs == [] and statuses(r)[-1] == ("snapshot", "missing")
    assert any("No live or cached jobs" in w for w in r.warnings)


def test_delhi_ncr_expands_to_three_cities(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ)).on(M2_HOST, m2_extract_handler)
    r = run(fake_http, store, location="Delhi-NCR")
    assert r.cities == ["Delhi", "Noida", "Gurugram"] and fake_http.calls(ADZ_HOST) == 3
    wheres = [dict(q.url.params)["where"] for q in fake_http.requests if q.url.host == ADZ_HOST]
    assert wheres == ["New Delhi", "Noida", "Gurgaon"]
    assert len(r.jobs) == 3                       # the same three mock jobs in every city merge into one set


def test_cross_provider_dedupe_keeps_posted_salary():
    from src.providers import adzuna, jsearch
    adz = [adzuna.normalise(r, NOW) for r in ADZ["results"]]
    js = [j for j in (jsearch.normalise(r, NOW) for r in JS["data"]) if j]
    merged = dedupe(js + adz)
    vy = [j for j in merged if j.title == "Data Scientist" and "VY" in (j.company or "").upper()]
    assert len(vy) == 1 and vy[0].source == "adzuna" and vy[0].salary_min == 1000000   # the copy with a posted salary
    assert len(merged) == len(adz) + len(js) - 1


def test_old_postings_are_dropped(fake_http, store):
    old = {**ADZ, "results": [dict(ADZ["results"][1], created=(NOW - timedelta(days=MAX_JOB_AGE_DAYS + 1)).isoformat())]
           + ADZ["results"][2:]}
    fake_http.on(ADZ_HOST, json_response(old)).on(M2_HOST, m2_extract_handler)
    r = run(fake_http, store)
    assert len(r.jobs) == 1 and any("posted over" in w for w in r.warnings)


def test_module_2_down_then_back(fake_http, store):
    fake_http.on(ADZ_HOST, json_response(ADZ))           # no route for module 2: connection refused
    r = run(fake_http, store)
    assert len(r.jobs) == 3 and all(j.skills_source == "unavailable" and j.skills == [] for j in r.jobs)
    assert any("Module 2" in w and "keyword overlap" in w for w in r.warnings)
    assert fake_http.calls(M2_HOST) == 1                  # stops after the first failure
    fake_http.on(M2_HOST, m2_extract_handler)
    r = run(fake_http, store, now=NOW + timedelta(hours=1))   # cache hit: skills retried and stored
    assert all(j.skills_source == "m2" for j in r.jobs) and "data_analysis" in r.jobs[-1].skills + r.jobs[0].skills + r.jobs[1].skills
    assert all(j.skills_source == "m2" for j in store.load("data scientist", "Bengaluru").jobs)
