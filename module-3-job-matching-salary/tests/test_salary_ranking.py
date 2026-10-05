"""Salary estimation (outliers, fallbacks), candidate value, negotiation, and ranking order."""
from datetime import timedelta

import pytest

from conftest import NOW, load_mock, make_job
from src.engines import ranking
from src.engines.salary import (
    MAX_RANGE_RATIO, candidate_value, estimate_market, negotiate, posted_salary_points, trim_outliers,
)
from src.models.schemas import ExperienceBand, MarketBaseline
from src.providers.adzuna import histogram as _  # noqa: F401  (import check only)

L = 100_000  # one lakh


def salaried(lo, hi, i=0, **kw):
    return make_job(job_id=f"adzuna:{i}", salary_min=lo, salary_max=hi, **kw)


# --- market estimate ------------------------------------------------------------------------------

def test_absurd_range_and_predicted_salaries_are_excluded():
    jobs = [salaried(4 * L, 25 * L, 0),                                  # the real "4-25 LPA" listing
            salaried(12 * L, 16 * L, 1, salary_is_predicted=True), salaried(None, None, 2), salaried(10 * L, 15 * L, 3)]
    points, excluded = posted_salary_points(jobs)
    assert points == [12.5 * L] and excluded == {"no_salary": 1, "predicted": 1, "wide_range": 1, "outlier": 0}
    assert 25 / 4 > MAX_RANGE_RATIO


def test_outliers_are_trimmed():
    kept, removed = trim_outliers([10 * L, 11 * L, 12 * L, 12 * L, 13 * L, 14 * L, 90 * L])
    assert removed == 1 and max(kept) == 14 * L


def test_estimate_from_posted_salaries():
    jobs = [salaried(lo * L, hi * L, i) for i, (lo, hi) in enumerate([(10, 14), (12, 16), (12, 18), (14, 20), (15, 21),
                                                                      (16, 22), (60, 80)])]
    est = estimate_market(jobs)
    assert est.sources_used == ["posted_salaries"] and est.sample_size == 6 and est.excluded == {"outlier": 1}
    # midpoints 12, 14, 15, 17, 18, 19 L (70 L trimmed): P25 14.25 L, median 16 L, P75 17.75 L, rounded to ₹10k
    assert (est.estimated_min, est.estimated_median, est.estimated_max) == (1430000, 1600000, 1780000)
    assert est.confidence == pytest.approx(0.45 + 0.03 * 6) and est.display == "Estimated market range: 14.3-17.8 LPA"
    assert "not a guarantee" in est.note


def test_falls_back_to_histogram_then_baseline():
    hist = {int(k): v for k, v in load_mock("adzuna_histogram_sample.json")["histogram"].items()}
    few = [salaried(10 * L, 14 * L, 0), salaried(4 * L, 25 * L, 1)]
    est = estimate_market(few, histogram_fn=lambda: hist)
    assert est.sources_used == ["adzuna_histogram"] and est.sample_size == 446 and est.confidence == 0.5
    assert (est.estimated_min, est.estimated_median, est.estimated_max) == (500000, 1500000, 2500000)
    assert "Fewer than 5 usable posted salaries" in est.note

    def down():
        raise RuntimeError("adzuna down")
    base = estimate_market(few, histogram_fn=down, baseline=MarketBaseline(median_salary_inr_lpa=18.5))
    assert base.sources_used == ["module_1_baseline"] and base.confidence == 0.3
    assert (base.estimated_min, base.estimated_median, base.estimated_max) == (1390000, 1850000, 2310000)
    none = estimate_market(few, histogram_fn=lambda: {0: 3, 1000000: 2})
    assert none.estimated_median is None and none.confidence == 0 and "Tried:" in none.note


# --- candidate value and negotiation --------------------------------------------------------------

MARKET = estimate_market([salaried(lo * L, hi * L, i) for i, (lo, hi) in enumerate(
    [(10, 14), (12, 16), (12, 18), (14, 20), (15, 21), (16, 22)])])


def test_candidate_value_follows_match_and_experience():
    strong = candidate_value(MARKET, match_score=0.9, years=5, band=ExperienceBand(min=2, max=6), city="Bengaluru")
    weak = candidate_value(MARKET, match_score=0.5, years=1, band=ExperienceBand(min=2, max=6), city="Bengaluru")
    assert strong.estimated_min > weak.estimated_min and strong.adjustment > 1 > weak.adjustment
    assert strong.reasons[0].startswith("Strong skill and profile match (90%)")
    assert "Relevant experience (5 years, typical 2-6)" in strong.reasons
    assert any(r.startswith("Less experience") for r in weak.reasons)
    assert candidate_value(MARKET, match_score=2.0, years=50, band=ExperienceBand(min=2, max=6), city=None).adjustment == 1.2


def test_negotiation_with_posted_salary_below_market():
    value = candidate_value(MARKET, match_score=0.85, years=4, band=ExperienceBand(min=2, max=6), city="Bengaluru")
    deal = negotiate(market=MARKET, candidate=value, job_id="adzuna:1", posted_min=10 * L, posted_max=12 * L)
    assert deal.posted_salary == "10-12 LPA" and deal.market_range == "14.3-17.8 LPA"
    assert deal.recommended_target_inr > 12 * L and deal.reasonable_minimum_inr >= 12 * L
    assert deal.recommended_target.endswith(" LPA") and deal.recommended_target_inr % 50_000 == 0
    assert "below your estimated market value" in deal.reasons[0] and "not a guarantee" in deal.note


def test_negotiation_with_generous_posted_salary():
    value = candidate_value(MARKET, match_score=0.85, years=4, band=ExperienceBand(min=2, max=6), city="Bengaluru")
    deal = negotiate(market=MARKET, candidate=value, posted_min=18 * L, posted_max=24 * L)
    assert deal.recommended_target_inr == round(value.estimated_max / 50_000) * 50_000
    assert deal.reasonable_minimum_inr >= 18 * L - 50_000 and "upper part" in deal.reasons[0]


def test_negotiation_without_posted_salary_and_with_predicted_one():
    value = candidate_value(MARKET, match_score=0.85, years=4, band=ExperienceBand(min=2, max=6), city="Bengaluru")
    none = negotiate(market=MARKET, candidate=value)
    assert none.posted_salary is None and none.confidence < value.confidence
    assert none.reasons[0].startswith("No posted salary") and none.reasonable_minimum_inr <= none.recommended_target_inr
    predicted = negotiate(market=MARKET, candidate=value, posted_min=30 * L, posted_max=40 * L, posted_is_predicted=True)
    assert predicted.posted_salary is None and "Adzuna's own estimate" in predicted.reasons[0]
    empty = candidate_value(estimate_market([]), match_score=0.8, years=3, band=None, city=None)
    assert negotiate(market=estimate_market([]), candidate=empty).recommended_target is None


# --- ranking ---------------------------------------------------------------------------------------

def comps(job, overall, location=1.0, experience=1.0, median=15 * L):
    return ranking.rank_components(job, overall, location, experience, median, NOW)


def test_salary_alone_cannot_outrank_a_much_better_match():
    good_match = make_job(job_id="a:1", salary_min=None)
    rich = make_job(job_id="a:2", salary_min=40 * L, salary_max=50 * L)
    assert ranking.rank_score(comps(good_match, 0.9)) > ranking.rank_score(comps(rich, 0.6))


def test_salary_breaks_a_close_call_and_predicted_is_ignored():
    plain = make_job(job_id="a:1")
    paid = make_job(job_id="a:2", salary_min=20 * L, salary_max=24 * L)
    predicted = make_job(job_id="a:3", salary_min=20 * L, salary_max=24 * L, salary_is_predicted=True)
    assert ranking.rank_score(comps(paid, 0.8)) > ranking.rank_score(comps(plain, 0.8))
    assert ranking.salary_attractiveness(predicted, 15 * L) == ranking.SALARY_UNKNOWN


def test_recency_and_staleness():
    assert ranking.recency(make_job(posted_at=NOW - timedelta(days=1)), NOW) == 1.0
    assert ranking.recency(make_job(posted_at=NOW - timedelta(days=45)), NOW) == ranking.RECENCY_FLOOR
    assert ranking.recency(make_job(posted_at=None), NOW) == ranking.RECENCY_UNKNOWN
    assert ranking.recency(make_job(stale=True), NOW) == pytest.approx(0.8)


def test_sort_key_is_stable_and_explainable():
    a, b = make_job(job_id="a:1", posted_at=NOW), make_job(job_id="a:2", posted_at=NOW - timedelta(days=2))
    keys = sorted([ranking.sort_key(0.7, 0.8, b), ranking.sort_key(0.7, 0.8, a), ranking.sort_key(0.7, 0.9, b)])
    assert keys[0][1] == -0.9 and keys[1][3] == "a:1"        # same score: higher match, then newer first
