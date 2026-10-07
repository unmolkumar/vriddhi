"""One real v2 search: Adzuna + module 1 (M1_BASE_URL) + module 2 (M2_BASE_URL). Skipped without Adzuna keys or when
either module isn't running. Never calls JSearch (use_jsearch=False)."""
import os

import httpx
import pytest
from dotenv import load_dotenv

from src.general import engine, m1
from src.engines.m2_client import base_url as m2_base_url
from src.general.schemas import JobSearchV2Request

load_dotenv()
pytestmark = pytest.mark.live


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not (os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")), reason="Adzuna keys not set")
def test_v2_search_live(tmp_path):
    if not (_up(m1.base_url() + "/health") and _up(m2_base_url() + "/api/v1/health")):
        pytest.skip("module 1 or module 2 not running")
    r = engine.search(JobSearchV2Request(target_role="accountant", location="Mumbai", use_jsearch=False,
                                         free_text="Accountant, 6 years: Tally, GST and TDS returns, bank reconciliation."),
                      engine.Clients(titles=m1.TitleCache(tmp_path / "titles.sqlite")))
    assert r.module_1_available and r.module_2_available and r.resolution.soc_code == "13-2011.00"
    assert r.jobs and r.relevance_summary.on_target > 0 and r.jobs[0].match.method == "m2_match_texts"
