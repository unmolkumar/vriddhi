"""Evidence volume, the insufficient_evidence verdict, follow-up questions and the answers to them (A3b).

A short description can't show much of an occupation, so a low score from little text means "not shown yet", not
"under-skilled". Evidence volume has two parts:
  units          substantive evidence sentences/items (matchable, >= MIN_UNIT_WORDS words, not clause copies)
  related_share  share of the core requirements (type shares and item-count scaling, like the score) with any
                 evidence at cosine >= RELATED_FLOOR, or met/partial by alias, role history or an answer
The verdict (thresholds tuned on the tuning sets only, WORKING.md section 14):
  score >= GOOD_FIT_THRESHOLD                                        -> good_fit (or over_qualified)
  units < SHORT_UNITS and related_share >= MIN_RELATED_SHARE         -> insufficient_evidence (+ follow-up questions)
  otherwise                                                          -> under_skilled
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.general import scoring
from src.general.evidence import EvidenceUnit
from src.general.matcher import RequirementMatch, coverage
from src.general.requirements import requirement_clauses

log = logging.getLogger(__name__)

MIN_UNIT_WORDS = 2
RELATED_FLOOR = 0.35            # cosine at which evidence is "related" to a requirement, short of partial
# Tuned together with scoring.GOOD_FIT_THRESHOLD on the tuning sets only (scripts/calibrate.py --verdict).
SHORT_UNITS = 3                 # fewer substantive units than this is a short description
MIN_RELATED_SHARE = 0.05        # ... and at least this much of the occupation must be touched for "insufficient"
GOOD_FIT_THRESHOLD_SHORT = 0.17  # good-fit threshold for short descriptions
QUESTIONS_MAX = 5
QUESTIONS_MIN = 3
QUESTION_TYPES = ("task", "dwa", "market_skill", "tool", "tech")
ANSWER_SOME_CREDIT = 0.3        # "some" experience: weak partial credit
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "questions"
REPHRASE_PROMPT_VERSION = 1

Answer = Literal["yes", "no", "some"]


class EvidenceVolume(BaseModel):
    units: int = Field(description="Substantive evidence sentences or items")
    related_share: float = Field(description="Share of the core requirements with any related evidence")
    short: bool = Field(description="units < SHORT_UNITS")


class FollowUpQuestion(BaseModel):
    requirement_id: str
    requirement: str
    item_type: str
    question: str


def volume(units: list[EvidenceUnit], matches: list[RequirementMatch]) -> EvidenceVolume:
    """Answers to follow-up questions raise related_share (and the score) but not the unit count: a short
    description stays short, so it keeps getting questions until the score clears the threshold."""
    n = sum(1 for u in units if u.matchable and u.context_span is None and u.section != "answers"
            and len(u.text.split()) >= MIN_UNIT_WORDS)
    related = coverage(matches, credit=lambda m: 1.0 if (m.status != "missing" or m.similarity >= RELATED_FLOOR) else 0.0)
    return EvidenceVolume(units=n, related_share=round(related, 4), short=n < SHORT_UNITS)


def decide(score: float, vol: EvidenceVolume, years, band, better_fit) -> tuple[str, str]:
    """(label, reason): scoring.verdict, with under_skilled split by evidence volume."""
    label, reason = scoring.verdict(score, years, band, better_fit, threshold_for(vol))
    if label == "under_skilled" and vol.short and vol.related_share >= MIN_RELATED_SHARE:
        return "insufficient_evidence", (
            f"Your description is short ({vol.units} item{'s' if vol.units != 1 else ''}) and shows {score:.0%} of this "
            f"role's weighted core requirements so far. Answer the questions below to give a fuller picture.")
    return label, reason


def threshold_for(vol: EvidenceVolume) -> float:
    return GOOD_FIT_THRESHOLD_SHORT if (vol.short and GOOD_FIT_THRESHOLD_SHORT is not None)         else scoring.GOOD_FIT_THRESHOLD


def label_for(score: float, units: int, related: float, threshold: float, threshold_short: float | None,
              short_units: int, min_related: float) -> str:
    """The verdict rule on its own (good_fit / insufficient_evidence / under_skilled), for the threshold search."""
    short = units < short_units
    t = threshold_short if (short and threshold_short is not None) else threshold
    if score >= t:
        return "good_fit"
    return "insufficient_evidence" if short and related >= min_related else "under_skilled"


QUESTION_MAX_WORDS = 18
_QUESTION_TAIL = re.compile(r",?\s+(?:using|such as|including|according to|in order to|to ensure|to determine|based on|"
                            r"to meet|in accordance with|in locations)\b.*$|;.*$", re.IGNORECASE)


def _first_clause(text: str) -> str:
    """The requirement as a short phrase: without its example tail; if still long, its first verb clause."""
    phrase = _QUESTION_TAIL.sub("", text.rstrip(". ")).strip()
    if len(phrase.split()) > QUESTION_MAX_WORDS:
        clauses = [c for c in requirement_clauses(text) if len(c.split()) <= QUESTION_MAX_WORDS]
        phrase = clauses[0] if clauses else re.split(r",\s+", phrase)[0]
    return phrase


def template_question(m: RequirementMatch) -> str:
    name = m.item.name.strip()
    if m.item.item_type in ("tech", "tool"):
        return f"Have you used {name} in your work?"
    if m.item.item_type == "market_skill":
        return f"Do you have experience with {name}?"
    phrase = _first_clause(name)
    return f"In your work, do you {phrase[0].lower() + phrase[1:]}?"


def question_candidates(matches: list[RequirementMatch], answered: set[str]) -> list[RequirementMatch]:
    """Highest-weight core requirements with no evidence yet (missing, or implied by role only), one per text."""
    pool = [m for m in matches if m.item.item_type in QUESTION_TYPES and m.item.item_id not in answered
            and (m.status == "missing" or m.reason == "implied_by_role")]
    pool.sort(key=lambda m: (m.item.item_type not in ("task", "dwa", "market_skill"), -m.item.weight))
    seen, out = set(), []
    for m in pool:
        key = _first_clause(m.item.name).lower()
        if key not in seen:
            seen.add(key)
            out.append(m)
        if len(out) >= QUESTIONS_MAX:
            break
    return out


def follow_up_questions(matches: list[RequirementMatch], occupation: str, answered: set[str] = frozenset(),
                        rephrase=None) -> list[FollowUpQuestion]:
    picked = question_candidates(matches, set(answered))
    questions = [template_question(m) for m in picked]
    if rephrase and questions:
        better = rephrase(questions, occupation)
        questions = [b if b else q for q, b in zip(questions, better)]
    return [FollowUpQuestion(requirement_id=m.item.item_id, requirement=m.item.name, item_type=m.item.item_type,
                             question=q) for m, q in zip(picked, questions)]


def apply_answers(matches: list[RequirementMatch], answers: dict[str, tuple[str, str | None]]) -> list[RequirementMatch]:
    """yes -> met by self-reported evidence (credit EVIDENCE_STRENGTH['self'] / level); some -> partial at
    ANSWER_SOME_CREDIT; no -> unchanged. Real evidence that already meets the requirement is kept."""
    out = []
    for m in matches:
        a = answers.get(m.item.item_id)
        if a and a[0] in ("yes", "some") and m.status != "met":
            answer, detail = a
            text = f"You answered {answer}" + (f": {detail}" if detail else "")
            update = {"evidence_text": text, "evidence_type": "self", "evidence_section": "answers",
                      "evidence_span": None, "evidence_context_span": None, "reason": "answered"}
            if answer == "yes":
                update |= {"status": "met", "implied_credit": None}
            elif scoring.credit(m) < ANSWER_SOME_CREDIT:
                update |= {"status": "partial", "implied_credit": ANSWER_SOME_CREDIT}
            else:
                update = {}
            m = m.model_copy(update=update) if update else m
        out.append(m)
    return out


class GroqRephraser:
    """Rewrites template questions in plain, friendly English for the occupation; cached; fail-safe (None)."""

    def __init__(self, api_key: str, cache_dir: Path | None = CACHE_DIR):
        self.api_key, self.cache_dir = api_key, cache_dir

    def __call__(self, questions: list[str], occupation: str) -> list[str | None]:
        from src.engines.skill_extractor import llm_model

        key = hashlib.sha1(json.dumps([REPHRASE_PROMPT_VERSION, llm_model(), occupation, questions]).encode()).hexdigest()
        f = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if f and f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        try:
            from groq import Groq

            client = Groq(api_key=self.api_key, timeout=10, max_retries=0)
            resp = client.chat.completions.create(
                model=llm_model(), temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": (
                    f"You rewrite interview questions for someone applying as: {occupation}. Make each question short, "
                    "plain and friendly, in simple Indian English, asking whether they have done the thing. Keep the "
                    "meaning; don't add new topics. Reply with JSON only: {\"questions\": [...]} in the same order.")},
                    {"role": "user", "content": json.dumps({"questions": questions})}])
            got = json.loads(resp.choices[0].message.content or "{}").get("questions", [])
        except Exception as e:  # no network, rate limit, bad JSON: keep the templates
            log.warning("question rephrase skipped: %s", type(e).__name__)
            return [None] * len(questions)
        out = [g if isinstance(g, str) and g.strip().endswith("?") else None for g in got] if len(got) == len(questions) \
            else [None] * len(questions)
        if f and any(out):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(out), encoding="utf-8")
        return out


def default_rephraser() -> GroqRephraser | None:
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("GROQ_API_KEY", "").strip()
    return GroqRephraser(key) if key else None
