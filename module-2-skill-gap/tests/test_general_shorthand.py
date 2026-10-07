"""General engine (v2) - A4: informal shorthand (deterministic map) and the optional normaliser (mocked)."""
import json

from src.general import shorthand
from src.general.evidence import from_skills, from_text
from src.general.schemas import GapAnalysisV2Request
from src.general.service import GeneralEngine
from src.general.m1_client import FIXTURE_PATH, FixtureM1Client
from test_general_matcher import WordEncoder


def test_expansions_are_literal_and_keep_clause_splitting():
    for e in shorthand.entries():
        assert "," not in e.expansion and ";" not in e.expansion and " and " not in f" {e.expansion} "
    assert shorthand.expand("BP checking, IV cannula") == ("blood pressure checking, intravenous cannula",
                                                          ["BP -> blood pressure", "IV -> intravenous"])


def test_case_plural_and_word_boundaries():
    assert shorthand.expand("bp and ecg")[0] == "blood pressure and electrocardiogram"       # case 'any'
    assert shorthand.expand("iv drip")[0] == "iv drip"                                         # 'upper' only
    assert shorthand.expand("fixed MCBs")[0] == "fixed miniature circuit breakers"
    assert shorthand.expand("BPO calls, CAD drawings")[0] == "BPO calls, CAD drawings"         # inside words: no
    assert shorthand.expand("5 yrs exp")[0] == "5 years experience"


def test_context_guards():
    assert shorthand.expand("DB and MCB fitting")[0] == "distribution board and miniature circuit breaker fitting"
    assert shorthand.expand("SQL DB queries")[0] == "SQL DB queries"                           # not near wiring
    assert shorthand.expand("DB work", "Electrician. DB work")[0] == "distribution board work"  # context: whole input
    assert shorthand.expand("wiring and DB, MySQL database")[0] == "wiring and DB, MySQL database"   # not_near
    assert shorthand.expand("OT duty", "staff nurse, OT duty")[0] == "operation theatre duty"
    assert shorthand.expand("OT hours paid", "factory worker, OT hours paid")[0] == "OT hours paid"


def test_units_keep_the_original_text_and_span():
    text = "Staff nurse, 3 yrs: injection, BP checking, dressing"
    units = from_text(text)
    whole = units[0]
    assert whole.text.endswith("blood pressure checking, dressing") and whole.original_text == text
    assert "BP -> blood pressure" in whole.rewrites and text[whole.span[0]:whole.span[1]] == text
    assert any(u.text == "blood pressure checking" and u.context_span == whole.span for u in units)
    typed = from_skills(["ECG", "Tally"])
    assert (typed[0].text, typed[0].original_text, typed[1].original_text) == ("electrocardiogram", "ECG", None)


def test_education_and_title_lines_are_not_rewritten():
    units = from_text("Experience\nITI Electrician | Tata Steel | 2015 - 2020\n- Fixed MCB faults.\nEducation\nITI Electrician, 2014\n")
    assert all("Industrial" not in u.text for u in units if u.role_title or u.education)
    assert any(u.text == "Fixed miniature circuit breaker faults." for u in units)


def test_rewrites_reach_the_api_evidence():
    e = GeneralEngine(client=FixtureM1Client(FIXTURE_PATH), encoder=WordEncoder(), translator=None, rephraser=None,
                      normaliser=None)
    r = e.analyze(GapAnalysisV2Request(soc_code="29-1141.00", free_text="Monitor patients' BP and record vital signs."))
    ev = next(x.evidence for x in r.strengths + r.gaps if x.evidence and x.evidence.rewrites)
    assert ev.original_text == "Monitor patients' BP and record vital signs." and "BP -> blood pressure" in ev.rewrites


# --- optional normaliser (mocked) ------------------------------------------------------------------------

def test_normaliser_rewrites_only_very_short_units():
    seen = {}

    def fake(fragments, context):
        seen["fragments"], seen["context"] = fragments, context
        return ["changing wound dressings" if f == "dressing" else None for f in fragments]

    text = "Staff nurse in a government hospital medicine ward for three years. Injection, dressing"
    units = from_text(text, normaliser=fake)
    assert all(shorthand.is_very_short(f) for f in seen["fragments"]) and seen["context"] == text
    d = next(u for u in units if u.text == "changing wound dressings")
    assert d.original_text == "dressing" and d.rewrites == ["dressing -> changing wound dressings"]
    assert any(u.text == "Injection" for u in units)                       # None: unchanged


def test_groq_normaliser_is_fail_safe_and_cached(tmp_path, monkeypatch):
    import sys
    import types

    class Resp:
        def __init__(self, content):
            self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]

    calls = []

    class FakeGroq:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            calls.append(kw)
            n = len(json.loads(kw["messages"][1]["content"])["fragments"])
            return Resp(json.dumps({"phrases": ["checking blood pressure"] * n}))

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq))
    n = shorthand.GroqNormaliser("k", cache_dir=tmp_path)
    assert n(["BP check"], "nurse") == ["checking blood pressure"]
    assert n(["BP check"], "nurse") == ["checking blood pressure"] and len(calls) == 1      # cached

    class Broken(FakeGroq):
        def create(self, **kw):
            raise TimeoutError

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=Broken))
    assert shorthand.GroqNormaliser("k", cache_dir=tmp_path)(["dressing"], "nurse") == [None]


def test_normaliser_is_off_without_the_flag(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("M2_NORMALISE_SHORT", raising=False)
    assert shorthand.default_normaliser() is None
    monkeypatch.setenv("M2_NORMALISE_SHORT", "1")
    assert isinstance(shorthand.default_normaliser(), shorthand.GroqNormaliser)
