"""A3 fixes on the real model (MiniLM): the ITI electrician's wiring task is met through requirement clauses, the
nurse's job title no longer meets a supervision task, prescribing is not applicable in India, and Hinglish evidence
is translated. Skipped when the model can't load."""
from pathlib import Path

import pytest

from src.general.embeddings import DEFAULT_MODEL, Encoder
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client
from src.general.schemas import GapAnalysisV2Request
from src.general.service import GeneralEngine
from src.general.translate import FixtureTranslator

PROFILES = Path(__file__).parent / "profiles"


@pytest.fixture(scope="module")
def engine():
    try:
        encoder = Encoder(DEFAULT_MODEL)
        encoder.encode(["warm up"])
    except Exception as e:  # no model files and no network
        pytest.skip(f"{DEFAULT_MODEL} unavailable: {type(e).__name__}")
    return GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=encoder,
                         translator=FixtureTranslator())


def analyze(engine, path, **kw):
    return engine.analyze(GapAnalysisV2Request(free_text=(PROFILES / path).read_text(encoding="utf-8"), **kw))


def by_name(r, prefix):
    return next(x for x in r.strengths + r.gaps if x.requirement.startswith(prefix))


def test_electrician_wiring_task_is_met(engine):
    r = analyze(engine, "tuning/47-2111.00_electrician.txt", target_role="electrician")
    wiring = by_name(r, "Assemble, install, test, or maintain electrical or electronic wiring")
    assert wiring.status == "met" and wiring.reason == "semantic"
    assert r.verdict.label == "good_fit"


def test_nurse_title_line_is_role_history_not_evidence(engine):
    r = analyze(engine, "tuning/29-1141.00_staff_nurse.txt", target_role="staff nurse")
    supervise = by_name(r, "Direct or supervise less-skilled nursing")
    assert supervise.evidence is None or "Staff Nurse" != supervise.evidence.text
    assert any(h.soc_code == "29-1141.00" and h.applies_to_target for h in r.role_history)
    assert any(n.requirement.startswith("Prescribe or recommend drugs") for n in r.not_applicable_in_india)


def test_hinglish_evidence_is_translated(engine):
    r = analyze(engine, "hinglish/29-1141.00_nurse_hinglish.txt", soc_code="29-1141.00")
    translated = [s for s in r.strengths if s.evidence and s.evidence.translated]
    assert translated and translated[0].evidence.original_text and not any("non-English" in w for w in r.warnings)
    assert r.verdict.label == "good_fit"
