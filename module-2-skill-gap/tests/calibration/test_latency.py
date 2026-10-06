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
