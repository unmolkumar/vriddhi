"""Salary intelligence: market range, candidate market value and negotiation guidance.

Ranges, never false precision (spec: Salary Intelligence; AGENTS.md §13). Sources, in order:
  1. posted salaries of the fetched jobs (Adzuna-predicted ones and absurd ranges excluded, outliers trimmed);
  2. module 1's salary percentiles for the role (market_salary_percentiles), when the sample is big enough;
  3. JSearch's (Glassdoor-backed) salary estimate, when cached or opted in;
  4. Adzuna's salary histogram for role + city;
  5. module 1's regional median (market_baseline.median_salary_inr_lpa).
Formulas in WORKING.md §8.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import NamedTuple

from src.models.schemas import (
    CandidateValue, ExperienceBand, Job, MarketBaseline, MarketPercentiles, Negotiation, SalaryEstimate, SourceCheck,
)

log = logging.getLogger(__name__)

MAX_RANGE_RATIO = 4.0          # a posted max/min above this is noise ("4-25 LPA") and left out
MIN_POSTED_SAMPLE = 5          # fewer usable posted salaries -> next source
MIN_HISTOGRAM_SAMPLE = 20      # vacancies behind the histogram
MIN_PERCENTILE_SAMPLE = 30     # module 1 salary points behind its percentiles
MIN_CITY_SAMPLES = 50          # a module 1 city band smaller than this doesn't adjust the estimate
SMALL_SAMPLE = 100             # module 1 bands below this many points get less confidence ...
SMALL_SAMPLE_PENALTY = 0.1     # ... by this much
SALARY_SOURCE_TOLERANCE = 0.25 # module 1 and JSearch medians within 25% agree
DISAGREE_PENALTY = 0.1         # confidence lost when they don't
PREFER_LARGER_INDIA_SAMPLE = "PREFER_LARGER_INDIA_SAMPLE"  # disagreeing sources: the larger India sample is primary
JSEARCH_CONFIDENCE = {"VERY_HIGH": 0.70, "HIGH": 0.60, "MEDIUM": 0.50, "LOW": 0.35}  # JSearch's own label
JSEARCH_DEFAULT_CONFIDENCE = 0.45
IQR_K = 1.5                    # Tukey fences for outliers
ROUND_TO = 10_000              # ₹10k: no false precision
TARGET_ROUND_TO = 50_000       # negotiation figures to the nearest 0.5 LPA
BASELINE_SPREAD = 0.25         # module 1 gives a median only: ±25% around it
CONFIDENCE = {"posted_base": 0.45, "posted_per_job": 0.03, "posted_cap": 0.85, "histogram": 0.5, "baseline": 0.3}
WIDE_IQR_PENALTY = 0.1         # IQR wider than the median itself: a less certain market

MATCH_ADJUSTMENT = 0.20        # candidate value: ±20% across the match range ...
MATCH_PIVOT = 0.70             # ... centred on a 70% match
ADJUSTMENT_BOUNDS = (0.80, 1.20)
NARROW_BASE = 0.15             # candidate range half-width = market width x (0.15 ...
NARROW_UNCERTAINTY = 0.35      # ... + 0.35 x (1 - confidence)): narrow with good data, wider when thin
NO_POSTED_CONFIDENCE_FACTOR = 0.85

NOTE = ("Estimated, market-based range from current listings and market data. It is indicative, "
        "not a guarantee or an offer.")


def lpa(amount: float | None) -> str | None:
    if amount is None:
        return None
    value = amount / 100_000
    return f"{value:.1f}".rstrip("0").rstrip(".")


def range_text(lo: float | None, hi: float | None) -> str | None:
    if lo is None or hi is None:
        return None
    return f"{lpa(lo)}-{lpa(hi)} LPA"


def _round(x: float, step: int = ROUND_TO) -> int:
    """Round half up to the step (Python's round() would send money halves to even)."""
    return int(math.floor(x / step + 0.5) * step)


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (q in [0, 1])."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo, frac = int(pos), pos - int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def posted_salary_points(jobs: list[Job]) -> tuple[list[float], dict[str, int]]:
    """Midpoint of each usable posted salary, and counts of what was excluded and why."""
    points, excluded = [], {"no_salary": 0, "predicted": 0, "wide_range": 0, "outlier": 0}
    for job in jobs:
        lo, hi = job.salary_min, job.salary_max
        if lo is None and hi is None:
            excluded["no_salary"] += 1
        elif job.salary_is_predicted:
            excluded["predicted"] += 1
        elif lo and hi and hi / lo > MAX_RANGE_RATIO:
            excluded["wide_range"] += 1
        else:
            values = [v for v in (lo, hi) if v is not None]
            points.append(sum(values) / len(values))
    return points, excluded


def trim_outliers(points: list[float]) -> tuple[list[float], int]:
    if len(points) < 4:
        return sorted(points), 0
    s = sorted(points)
    q1, q3 = percentile(s, 0.25), percentile(s, 0.75)
    lo, hi = q1 - IQR_K * (q3 - q1), q3 + IQR_K * (q3 - q1)
    kept = [p for p in s if lo <= p <= hi]
    return kept, len(s) - len(kept)


def histogram_points(histogram: dict[int, int]) -> list[tuple[float, int]]:
    """Adzuna buckets {lower bound: vacancies} -> (bucket midpoint, weight)."""
    keys = sorted(histogram)
    out = []
    for i, k in enumerate(keys):
        width = (keys[i + 1] - k) if i + 1 < len(keys) else (k - keys[i - 1] if i else k or 100_000)
        if histogram[k] > 0:
            out.append((k + width / 2, histogram[k]))
    return out


def weighted_percentile(points: list[tuple[float, int]], q: float) -> float:
    total = sum(w for _, w in points)
    target, running = q * total, 0
    for value, weight in sorted(points):
        running += weight
        if running >= target:
            return value
    return sorted(points)[-1][0]


# Module 1's experience tiers: entry <3 years, mid 3-5, senior >5. Senior has no upper bound; the candidate is
# positioned over 5 to M1_SENIOR_CAP_YEARS.
M1_SENIOR_CAP_YEARS = 12.0
M1_TIER_BANDS = {"entry": (0.0, 3.0), "mid": (3.0, 5.0), "senior": (5.0, M1_SENIOR_CAP_YEARS)}
M1_CITY = {"Delhi": "Delhi NCR", "Noida": "Delhi NCR", "Gurugram": "Delhi NCR"}  # our names -> module 1's metro


class M1Salary(NamedTuple):
    """Module 1's percentiles resolved for one candidate, in INR per year."""
    p25: float
    p50: float
    p75: float
    sample_size: int
    method: str                      # experience_bucket | experience_x_city_ratio | overall | remote
    band: ExperienceBand | None      # the experience tier the figures describe
    detail: str


def experience_tier(years: float) -> str:
    return "entry" if years < 3 else "mid" if years <= 5 else "senior"


def m1_salary(pct: MarketPercentiles, years: float, city: str | None, *, remote_only: bool = False) -> M1Salary | None:
    """Adapter for module 1's market_salary_percentiles (LPA).

    On-site/hybrid by default: the candidate's experience tier, scaled by city p50 / overall p50 when that city
    band has at least MIN_CITY_SAMPLES points; the tier alone otherwise. Falls back to the overall band when the
    tier is missing or under MIN_PERCENTILE_SAMPLE. With remote_only (the request accepts remote work only),
    module 1's remote_inr_lpa band as is, when it has MIN_PERCENTILE_SAMPLE points; older module 1 has none.
    """
    remote = pct.remote_inr_lpa
    if remote_only and remote and remote.sample_size >= MIN_PERCENTILE_SAMPLE:
        return M1Salary(remote.p25 * 100_000, remote.p50 * 100_000, remote.p75 * 100_000, remote.sample_size,
                        "remote", None, "remote roles, all experience levels")
    tier = experience_tier(years)
    band, method = pct.by_experience_inr_lpa.get(tier), "experience_bucket"
    if band is None or band.sample_size < MIN_PERCENTILE_SAMPLE:
        band, method, tier = pct.overall_inr_lpa, "overall", None
    if band is None:
        return None
    m1_city = M1_CITY.get(city, city) if city else None
    city_band = pct.by_city_inr_lpa.get(m1_city) if m1_city else None
    factor, detail = 1.0, f"{tier} tier" if tier else "all experience levels"
    if tier and city_band and pct.overall_inr_lpa and city_band.sample_size >= MIN_CITY_SAMPLES:
        factor, method = city_band.p50 / pct.overall_inr_lpa.p50, "experience_x_city_ratio"
        detail += f" x {m1_city} ratio {factor:.2f}"
    elif city_band:
        detail += f"; {m1_city} band not used ({city_band.sample_size} points, need {MIN_CITY_SAMPLES})"
    if remote_only:
        detail += ("; no remote band from module 1" if remote is None else
                   f"; remote band not used ({remote.sample_size} points, need {MIN_PERCENTILE_SAMPLE})")
    lo, mid, hi = (v * factor * 100_000 for v in (band.p25, band.p50, band.p75))
    return M1Salary(lo, mid, hi, band.sample_size, method,
                    ExperienceBand(min=M1_TIER_BANDS[tier][0], max=M1_TIER_BANDS[tier][1]) if tier else None, detail)


def _estimate(lo: float, mid: float, hi: float, confidence: float, n: int, sources: list[str],
              excluded: dict[str, int]) -> SalaryEstimate:
    lo, mid, hi = _round(lo), _round(mid), _round(hi)
    return SalaryEstimate(estimated_min=lo, estimated_median=mid, estimated_max=hi, confidence=round(confidence, 2),
                          sample_size=n, sources_used=sources, excluded={k: v for k, v in excluded.items() if v},
                          display=f"Estimated market range: {range_text(lo, hi)}", note=NOTE)


def estimate_market(jobs: list[Job], *, histogram_fn: Callable[[], dict[int, int]] | None = None,
                    baseline: MarketBaseline | None = None, percentiles: M1Salary | None = None,
                    jsearch_estimate: dict | None = None) -> SalaryEstimate:
    points, excluded = posted_salary_points(jobs)
    kept, outliers = trim_outliers(points)
    excluded["outlier"] = outliers
    tried = []
    if len(kept) >= MIN_POSTED_SAMPLE:
        q1, med, q3 = percentile(kept, 0.25), percentile(kept, 0.5), percentile(kept, 0.75)
        conf = min(CONFIDENCE["posted_cap"], CONFIDENCE["posted_base"] + CONFIDENCE["posted_per_job"] * len(kept))
        if q3 - q1 > med:
            conf -= WIDE_IQR_PENALTY
        return _estimate(q1, med, q3, conf, len(kept), ["posted_salaries"], excluded)
    tried.append(f"posted_salaries (only {len(kept)} usable, need {MIN_POSTED_SAMPLE})")

    if percentiles is not None:
        if percentiles.sample_size >= MIN_PERCENTILE_SAMPLE:
            return _from_percentiles(percentiles, jsearch_estimate, excluded)
        tried.append(f"module_1_percentiles ({percentiles.sample_size} points, need {MIN_PERCENTILE_SAMPLE})")

    if jsearch_estimate:
        est = _estimate(jsearch_estimate["min"], jsearch_estimate["median"], jsearch_estimate["max"],
                        _jsearch_confidence(jsearch_estimate),
                        jsearch_estimate.get("sample_size", 0), ["jsearch_salary_estimate"], excluded)
        est.note += (f" From JSearch's salary estimate ({jsearch_estimate.get('publisher') or 'aggregated'} data, "
                     f"{jsearch_estimate.get('sample_size', 0):,} salaries).")
        return est

    if histogram_fn is not None:
        try:
            hist = histogram_points(histogram_fn())
        except Exception as e:  # provider down or not configured: fall through to the baseline
            log.info("salary histogram unavailable: %s", e)
            hist = []
        n = sum(w for _, w in hist)
        if n >= MIN_HISTOGRAM_SAMPLE:
            est = _estimate(weighted_percentile(hist, 0.25), weighted_percentile(hist, 0.5),
                            weighted_percentile(hist, 0.75), CONFIDENCE["histogram"], n, ["adzuna_histogram"], excluded)
            est.note += " Fewer than 5 usable posted salaries, so Adzuna's salary distribution was used."
            return est
        tried.append(f"adzuna_histogram ({n} vacancies, need {MIN_HISTOGRAM_SAMPLE})")

    if baseline is not None and baseline.median_salary_inr_lpa:
        median = baseline.median_salary_inr_lpa * 100_000
        est = _estimate(median * (1 - BASELINE_SPREAD), median, median * (1 + BASELINE_SPREAD), CONFIDENCE["baseline"],
                        0, ["module_1_baseline"], excluded)
        est.note += " Based on module 1's regional median only, so the range is wide and the confidence low."
        return est
    tried.append("module_1_baseline (not provided)")
    return SalaryEstimate(estimated_min=None, estimated_median=None, estimated_max=None, confidence=0.0,
                          sample_size=len(kept), sources_used=[], excluded={k: v for k, v in excluded.items() if v},
                          display=None, note="Not enough salary data for an estimate. Tried: " + "; ".join(tried) + ".")


def _from_percentiles(m1: M1Salary, js: dict | None, excluded: dict[str, int]) -> SalaryEstimate:
    """Module 1's percentiles, checked against JSearch's estimate when there is one.

    Medians within SALARY_SOURCE_TOLERANCE: module 1 stays primary. Beyond it, PREFER_LARGER_INDIA_SAMPLE:
    the source with more India-located salaries is primary (module 1 on a tie), the other stays in
    source_check with its numbers and the gap, and confidence drops by DISAGREE_PENALTY."""
    n = m1.sample_size
    m1_conf = min(CONFIDENCE["posted_cap"], 0.55 + 0.05 * math.log10(n)) - (SMALL_SAMPLE_PENALTY if n < SMALL_SAMPLE else 0)
    m1_note = f" Based on module 1's {n:,} salary points for the role ({m1.detail})."
    if not js:
        est = _estimate(m1.p25, m1.p50, m1.p75, m1_conf, n, ["module_1_percentiles"], excluded)
        est.method, est.note = m1.method, est.note + m1_note
        return est
    gap = (js["median"] - m1.p50) / m1.p50
    js_n = js.get("sample_size", 0)
    agree = abs(gap) <= SALARY_SOURCE_TOLERANCE
    use_js = not agree and js_n > n
    check = SourceCheck(
        module_1_p25=_round(m1.p25), module_1_median=_round(m1.p50), module_1_p75=_round(m1.p75), module_1_sample_size=n,
        jsearch_min=_round(js["min"]), jsearch_median=_round(js["median"]), jsearch_max=_round(js["max"]),
        jsearch_sample_size=js_n, gap_pct=round(gap * 100, 1), agree=agree,
        primary="jsearch" if use_js else "module_1", rule="module_1_agrees" if agree else PREFER_LARGER_INDIA_SAMPLE)
    if agree:
        est = _estimate(m1.p25, m1.p50, m1.p75, m1_conf, n, ["module_1_percentiles"], excluded)
        est.method = m1.method
        est.note += m1_note + f" JSearch's estimate agrees (median {lpa(js['median'])} LPA)."
    elif use_js:
        est = _estimate(js["min"], js["median"], js["max"], _jsearch_confidence(js) - DISAGREE_PENALTY, js_n,
                        ["jsearch_salary_estimate"], excluded)
        est.note += (f" From JSearch's estimate ({js_n:,} India salaries). Module 1's median ({lpa(m1.p50)} LPA, "
                     f"{n:,} points) differs by {abs(check.gap_pct):g}%; the larger India sample is used.")
    else:
        est = _estimate(m1.p25, m1.p50, m1.p75, m1_conf - DISAGREE_PENALTY, n, ["module_1_percentiles"], excluded)
        est.method = m1.method
        est.note += m1_note + (f" JSearch's median ({lpa(js['median'])} LPA, {js_n:,} salaries) differs by "
                               f"{abs(check.gap_pct):g}%; module 1 has the larger India sample, so it is used.")
    est.source_check = check
    return est


def _jsearch_confidence(js: dict) -> float:
    return JSEARCH_CONFIDENCE.get(js.get("confidence", ""), JSEARCH_DEFAULT_CONFIDENCE)


def candidate_value(market: SalaryEstimate, *, match_score: float, years: float,
                    band: ExperienceBand | None, city: str | None) -> CandidateValue:
    """Where the candidate likely sits inside the market range.

    position = experience within the band (0 = band minimum, 1 = maximum; 0.5 without a band) places the
    centre between the market's low and high; the match multiplier moves it; the half-width shrinks with
    confidence. So a 5-year engineer in a 2-6 band with a strong match gets a narrow range near the top.
    """
    if market.estimated_median is None:
        return CandidateValue(estimated_min=None, estimated_max=None, confidence=0.0, adjustment=1.0, display=None,
                              reasons=["Not enough salary data to estimate your market value."])
    reasons = []
    adj = max(ADJUSTMENT_BOUNDS[0], min(ADJUSTMENT_BOUNDS[1], 1 + MATCH_ADJUSTMENT * (match_score - MATCH_PIVOT)))
    pct = round(match_score * 100)
    reasons.append(f"Strong skill and profile match ({pct}%)" if match_score >= 0.8 else
                   f"Good match ({pct}%)" if match_score >= 0.65 else f"Partial match ({pct}%) lowers the estimate")
    position = 0.5
    if band is not None:
        position = 1.0 if band.max <= band.min else max(0.0, min(1.0, (years - band.min) / (band.max - band.min)))
        if years > band.max:
            reasons.append(f"Experience above the typical {band.min:g}-{band.max:g} years for the role")
        elif years >= band.min:
            reasons.append(f"Relevant experience ({years:g} years, typical {band.min:g}-{band.max:g})")
        else:
            reasons.append(f"Less experience than the typical {band.min:g}-{band.max:g} years")
    source = {"posted_salaries": f"{market.sample_size} comparable posted salaries",
              "module_1_percentiles": f"{market.sample_size:,} salary points from module 1",
              "jsearch_salary_estimate": f"{market.sample_size:,} salaries via JSearch",
              "adzuna_histogram": "the current salary distribution", "module_1_baseline": "the regional median"}
    reasons.append(f"Location market: {city or 'India'}, based on " + ", ".join(source[s] for s in market.sources_used))
    width = market.estimated_max - market.estimated_min
    centre = (market.estimated_min + position * width) * adj
    half = width * (NARROW_BASE + NARROW_UNCERTAINTY * (1 - market.confidence))
    lo, hi = _round(centre - half), _round(centre + half)
    return CandidateValue(estimated_min=lo, estimated_max=hi, confidence=round(market.confidence * 0.9, 2),
                          adjustment=round(adj, 3), market_position=round(position, 3),
                          display=f"Your estimated range: {range_text(lo, hi)}", reasons=reasons)


def negotiate(*, market: SalaryEstimate, candidate: CandidateValue, job_id: str | None = None,
              posted_min: int | None = None, posted_max: int | None = None, posted_is_predicted: bool = False
              ) -> Negotiation:
    """Posted vs market vs candidate range -> a target and a reasonable minimum (spec: Negotiation Engine)."""
    reasons = []
    has_posted = (posted_min is not None or posted_max is not None) and not posted_is_predicted
    if posted_is_predicted:
        reasons.append("The salary shown for this job is Adzuna's own estimate, not the employer's figure, so it isn't used.")
    if candidate.estimated_min is None:
        return Negotiation(job_id=job_id, posted_salary=range_text(posted_min, posted_max) if has_posted else None,
                           market_range=None, candidate_range=None, recommended_target=None, reasonable_minimum=None,
                           recommended_target_inr=None, reasonable_minimum_inr=None, confidence=0.0,
                           reasons=reasons + ["Not enough salary data for negotiation guidance."], note=NOTE)
    c_lo, c_hi = candidate.estimated_min, candidate.estimated_max
    c_mid = (c_lo + c_hi) / 2
    confidence = candidate.confidence
    if has_posted:
        p_lo = posted_min if posted_min is not None else posted_max
        p_hi = posted_max if posted_max is not None else posted_min
        if p_hi >= c_hi:
            target, minimum = c_hi, max(p_lo, c_lo)
            reasons.append("The posted range reaches above your estimated range: aim for its upper part.")
        elif p_hi >= c_mid:
            target, minimum = p_hi, max(p_lo, c_lo)
            reasons.append("The top of the posted range sits within your estimated range: aim for the top of it.")
        else:
            target, minimum = c_mid, max(p_hi, c_lo)
            reasons.append("The posted salary is below your estimated market value: ask above it, citing comparable roles.")
    else:
        target, minimum = c_mid, c_lo
        confidence *= NO_POSTED_CONFIDENCE_FACTOR
        reasons.append("No posted salary for this job: the target comes from the market estimate.")
    reasons += candidate.reasons
    target, minimum = _round(target, TARGET_ROUND_TO), _round(min(minimum, target), TARGET_ROUND_TO)
    return Negotiation(
        job_id=job_id, posted_salary=range_text(p_lo, p_hi) if has_posted else None,
        market_range=range_text(market.estimated_min, market.estimated_max), candidate_range=range_text(c_lo, c_hi),
        recommended_target=f"{lpa(target)} LPA", reasonable_minimum=f"{lpa(minimum)} LPA",
        recommended_target_inr=target, reasonable_minimum_inr=minimum, confidence=round(confidence, 2),
        reasons=reasons, note=NOTE)
