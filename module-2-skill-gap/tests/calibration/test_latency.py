"""Warm v2 gap analysis (occupation prepared, embeddings cached) stays under WARM_BUDGET_S on CPU with MiniLM."""
import time
from pathlib import Path

import pytest

from src.general.embeddings import DEFAULT_MODEL, Encoder
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client
from src.general.schemas import GapAnalysisV2Request
from src.general.service import GeneralEngine

WARM_BUDGET_S = 2.0
PROFILE = Path(__file__).parent / "profiles" / "tuning" / "29-1141.00_staff_nurse.txt"


def test_warm_request_under_budget():
    try:
        encoder = Encoder(DEFAULT_MODEL)
        encoder.encode(["warm up"])
    except Exception as e:  # no model files and no network
        pytest.skip(f"{DEFAULT_MODEL} unavailable: {type(e).__name__}")
    engine = GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=encoder)
    request = GapAnalysisV2Request(target_role="staff nurse", free_text=PROFILE.read_text(encoding="utf-8"),
                                   hours_per_week=10)
    engine.analyze(request)                                  # cold: prepares the occupation and its related ones
    times = []
    for _ in range(3):
        start = time.perf_counter()
        engine.analyze(request)
        times.append(time.perf_counter() - start)
    assert sorted(times)[1] < WARM_BUDGET_S, times


MATCH_TEXTS_NEW_BUDGET_S = 4.0       # a page of 50 never-seen listings (measured ~2.9 s median on a 14-thread CPU)
MATCH_TEXTS_REPEAT_BUDGET_S = 3.0    # the same listings again (clause vectors cached: ~0.2 s)


def test_match_texts_50_jobs_warm_under_budget():
    """50 jobs of six O*NET tasks each (~150 words; half with soc_code) against one resume, occupations warm."""
    from src.general.schemas import JobIn, MatchTextsRequest
    try:
        encoder = Encoder(DEFAULT_MODEL)
        encoder.encode(["warm up"])
    except Exception as e:  # no model files and no network
        pytest.skip(f"{DEFAULT_MODEL} unavailable: {type(e).__name__}")
    client = FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH)
    engine = GeneralEngine(client=client, encoder=encoder, translator=None, rephraser=None, normaliser=None)

    def page(tag):                                           # every clause unique to `tag`: nothing cached
        jobs = []
        for n, soc in enumerate(list(client.occupations) * 2):
            tasks = [r["item_name"] for r in client.requirements(soc) if r["item_type"] == "task"][n // 25 * 6:(n // 25 + 1) * 6]
            text = " ".join(t.rstrip(".") + f" at site {tag}." for t in tasks)
            jobs.append(JobIn(job_id=str(n), job_text=text, soc_code=soc if n % 2 else None))
        return MatchTextsRequest(free_text=PROFILE.read_text(encoding="utf-8"), jobs=jobs[:50])

    engine.match_texts(page("warm"))                         # cold: prepares the occupations

    def timed(request):
        start = time.perf_counter()
        out = engine.match_texts(request)
        assert len(out.results) == 50
        return time.perf_counter() - start

    new = sorted(timed(page(f"new{k}")) for k in range(3))
    repeat = sorted(timed(page("warm")) for _ in range(3))
    assert new[1] < MATCH_TEXTS_NEW_BUDGET_S and repeat[1] < MATCH_TEXTS_REPEAT_BUDGET_S, (new, repeat)
