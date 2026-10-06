"""Requirements x evidence units -> met / partial / missing, with the evidence that decided it.

Two layers per requirement:
  1. alias (high precision): a tech / tool / market_skill requirement that the v1 taxonomy resolves to the same
     skill as an evidence unit is met, reason 'alias';
  2. semantic: cosine of '{name}: {description}' against every unit; the best unit decides, with per-item-type
     thresholds (task sentences and tool names score on different scales).
coverage() is a provisional weighted score for calibration only; A2 replaces it with the real scoring.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal, NamedTuple

import numpy as np
from pydantic import BaseModel

from src.engines.skill_extractor import extract_skills, resolve_skill
from src.general.embeddings import Encoder, cosine
from src.general.evidence import EvidenceUnit
from src.general.requirements import RequirementItem

Status = Literal["met", "partial", "missing"]

# (met, partial) cosine per item type, MiniLM; calibrated on tests/calibration (WORKING.md, General engine A1).
THRESHOLDS: dict[str, tuple[float, float]] = {
    "tech": (0.65, 0.52), "tool": (0.65, 0.52), "market_skill": (0.65, 0.52),
    "task": (0.55, 0.42), "dwa": (0.55, 0.42),
    "knowledge": (0.50, 0.37), "skill": (0.50, 0.37), "work_activity": (0.50, 0.37), "ability": (0.50, 0.37),
}
ALIAS_TYPES = ("tech", "tool", "market_skill")
CREDIT = {"met": 1.0, "partial": 0.5, "missing": 0.0}
# Provisional coverage: share of each item type in the score (renormalised over the types present). Within a
# type, items are weighted by RequirementItem.weight, so LAYER_WEIGHT only matters across layers via these shares.
TYPE_SHARE = {"task": 0.30, "dwa": 0.10, "market_skill": 0.30, "tech": 0.05, "tool": 0.05,
              "knowledge": 0.10, "skill": 0.04, "work_activity": 0.04, "ability": 0.0}


class RequirementMatch(BaseModel):
    item: RequirementItem
    status: Status
    similarity: float
    reason: Literal["alias", "semantic", "none"]
    evidence_text: str | None = None
    evidence_type: str | None = None
    evidence_section: str | None = None
    evidence_span: tuple[int, int] | None = None


class Scored(NamedTuple):
    similarity: float           # best cosine over units
    unit: int | None            # index of that unit
    alias_unit: int | None      # index of a unit with the same taxonomy skill, if any


@lru_cache(maxsize=20_000)
def requirement_skill_id(name: str) -> str | None:
    """The taxonomy skill a tech/tool/market_skill name means ('Microsoft Excel' -> excel), if unambiguous."""
    entry = resolve_skill(name)
    if entry is None:
        hits = [h for h in extract_skills(name, use_llm=False, skills_context=True) if not h.is_category]
        return hits[0].id if len(hits) == 1 else None
    return None if entry.get("is_category") else entry["id"]


def score(items: list[RequirementItem], units: list[EvidenceUnit], encoder: Encoder, *,
          cache_key: str | None = None, req_vectors: np.ndarray | None = None) -> list[Scored]:
    """Best evidence per requirement (similarity, unit) and the alias unit, before thresholds."""
    if not items:
        return []
    if req_vectors is None:
        texts = [i.text for i in items]
        req_vectors = encoder.encode_cached(cache_key, texts) if cache_key else encoder.encode(texts)
    unit_vectors = encoder.encode([u.text for u in units])
    sims = cosine(req_vectors, unit_vectors)
    by_skill: dict[str, int] = {}
    for idx, u in enumerate(units):
        for sid in u.skill_ids:
            by_skill.setdefault(sid, idx)
    out = []
    for r, item in enumerate(items):
        alias = None
        if item.item_type in ALIAS_TYPES and (sid := requirement_skill_id(item.name)):
            alias = by_skill.get(sid)
        if sims.shape[1]:
            best = int(np.argmax(sims[r]))
            out.append(Scored(float(sims[r, best]), best, alias))
        else:
            out.append(Scored(0.0, None, alias))
    return out


def status_of(item_type: str, sim: float, thresholds: dict[str, tuple[float, float]] = THRESHOLDS) -> Status:
    met, partial = thresholds.get(item_type, THRESHOLDS["skill"])
    return "met" if sim >= met else "partial" if sim >= partial else "missing"


def classify(items: list[RequirementItem], units: list[EvidenceUnit], scored: list[Scored],
             thresholds: dict[str, tuple[float, float]] = THRESHOLDS) -> list[RequirementMatch]:
    out = []
    for item, s in zip(items, scored):
        if s.alias_unit is not None:
            u, status, reason, sim = units[s.alias_unit], "met", "alias", s.similarity
        else:
            status = status_of(item.item_type, s.similarity, thresholds)
            reason = "semantic" if status != "missing" else "none"
            u, sim = (units[s.unit] if s.unit is not None else None), s.similarity
        out.append(RequirementMatch(
            item=item, status=status, similarity=round(sim, 4), reason=reason,
            evidence_text=u.text if u else None, evidence_type=u.evidence_type if u else None,
            evidence_section=u.section if u else None, evidence_span=u.span if u else None))
    return out


def match(items: list[RequirementItem], units: list[EvidenceUnit], encoder: Encoder, *,
          cache_key: str | None = None, thresholds: dict[str, tuple[float, float]] = THRESHOLDS
          ) -> list[RequirementMatch]:
    """Status, similarity, reason and best evidence for every requirement."""
    return classify(items, units, score(items, units, encoder, cache_key=cache_key), thresholds)


def coverage(matches: list[RequirementMatch], type_share: dict[str, float] = TYPE_SHARE) -> float:
    """Provisional 0-1 score: per item type, the weight-averaged credit (met 1, partial 0.5); then the types
    combined by TYPE_SHARE, so a SOC with 474 tech rows isn't scored on tech alone."""
    per_type: dict[str, list[float]] = {}
    for m in matches:
        t = per_type.setdefault(m.item.item_type, [0.0, 0.0])
        t[0] += m.item.weight * CREDIT[m.status]
        t[1] += m.item.weight
    shares = {t: type_share.get(t, 0.0) for t, (_, w) in per_type.items() if w > 0}
    total = sum(shares.values())
    if total <= 0:
        return 0.0
    return sum(shares[t] / total * per_type[t][0] / per_type[t][1] for t in shares)
