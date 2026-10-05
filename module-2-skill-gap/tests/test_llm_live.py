"""One real call to Groq. Skipped when GROQ_API_KEY isn't set."""
import os

import pytest
from dotenv import load_dotenv

import src.engines.skill_extractor as se
from src.engines.skill_extractor import extract_skills

load_dotenv()
pytestmark = pytest.mark.skipif(not os.getenv("GROQ_API_KEY", "").strip(), reason="GROQ_API_KEY not set")


def test_live_llm_pass_is_grounded(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "LLM_CACHE_DIR", tmp_path)  # force a real call, keep the real cache clean
    text = ("Data engineer building pipelines in Python on Snowpark, orchestrated with Dagster, "
            "with data contracts validated by Great Expectations.")
    hits = extract_skills(text, use_llm=True)
    ids = {h.id for h in hits}
    assert "python" in ids
    llm_hits = [h for h in hits if h.source == "llm"]
    for h in llm_hits:  # every LLM skill must literally appear in the text
        assert h.matches and h.matches[0].lower() in text.lower()
    assert list(tmp_path.glob("*.json")), "the response should be cached"
