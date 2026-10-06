"""Requirements x evidence units -> met / partial / missing, with the evidence that decided it.

Two layers per requirement:
  1. alias (high precision): a tech / tool / market_skill requirement that the v1 taxonomy resolves to the same
     skill as an evidence unit is met, reason 'alias';
  2. semantic: cosine of '{name}: {description}' against every unit; the best unit decides, with per-item-type
     thresholds (task sentences and tool names score on different scales).
Only core requirements (SCORED_TYPES: market_skill, tech, tool, task, dwa) are matched and scored; the generic
O*NET layers are inferred in inference.py. coverage() is a provisional weighted score for calibration only; A2
replaces it with the real scoring.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Literal, NamedTuple

import numpy as np
from pydantic import BaseModel, Field

from src.engines.skill_extractor import extract_skills, resolve_skill
from src.general.embeddings import Encoder, cosine
from src.general.evidence import EvidenceUnit
from src.general.requirements import SCORED_TYPES, Provenance, RequirementItem

Status = Literal["met", "partial", "missing"]

# (met, partial) cosine per core item type, MiniLM; tuned on tests/calibration/profiles/tuning only, then frozen
# (WORKING.md section 11.3).
THRESHOLDS: dict[str, tuple[float, float]] = {
    "tech": (0.60, 0.50), "tool": (0.60, 0.50), "market_skill": (0.60, 0.50),
    "task": (0.55, 0.45), "dwa": (0.55, 0.45),
}
ALIAS_TYPES = ("tech", "tool", "market_skill")
CREDIT = {"met": 1.0, "partial": 0.5, "missing": 0.0}
# Share of each core item type in the score. A type's share is scaled by its items' mean reliability (curated and
# off-domain factors) and by min(1, items / MIN_ITEMS_FOR_FULL_SHARE), then renormalised over the types present.
TYPE_SHARE = {"task": 0.30, "dwa": 0.10, "market_skill": 0.40, "tech": 0.10, "tool": 0.10}
MIN_ITEMS_FOR_FULL_SHARE = 5


class RequirementMatch(BaseModel):
    item: RequirementItem
    provenance: Provenance = Field(description="onet | india_postings | curated (same as item.provenance)")
    status: Status
    similarity: float
    reason: Literal["alias", "semantic", "none"]
    evidence_text: str | None = None
    evidence_type: str | None = None
    evidence_section: str | None = None
    evidence_span: tuple[int, int] | None = None
    evidence_context_span: tuple[int, int] | None = None


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
          cache_key: str | None = None, req_vectors: np.ndarray | None = None,
          unit_vectors: np.ndarray | None = None) -> list[Scored]:
    """Best evidence per requirement (similarity, unit) and the alias unit, before thresholds."""
    if not items:
        return []
    if req_vectors is None:
        texts = [i.text for i in items]
        req_vectors = encoder.encode_cached(cache_key, texts) if cache_key else encoder.encode(texts)
    if unit_vectors is None:
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
    met, partial = thresholds.get(item_type, THRESHOLDS["task"])
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
            item=item, provenance=item.provenance, status=status, similarity=round(sim, 4), reason=reason,
            evidence_text=u.text if u else None, evidence_type=u.evidence_type if u else None,
            evidence_section=u.section if u else None, evidence_span=u.span if u else None,
            evidence_context_span=u.context_span if u else None))
    return out


def match(items: list[RequirementItem], units: list[EvidenceUnit], encoder: Encoder, *,
          cache_key: str | None = None, thresholds: dict[str, tuple[float, float]] = THRESHOLDS
          ) -> list[RequirementMatch]:
    """Status, similarity, reason and best evidence for every core requirement (other layers are skipped)."""
    core = [i for i in items if i.item_type in SCORED_TYPES]
    return classify(core, units, score(core, units, encoder, cache_key=cache_key), thresholds)


def effective_share(type_share: float, weight: float, base_weight: float, n_items: int) -> float:
    """TYPE_SHARE x the type's mean reliability x min(1, n_items / MIN_ITEMS_FOR_FULL_SHARE); renormalised by the
    caller over the types present. A one-item type can't swing 40% of the score."""
    if weight <= 0 or base_weight <= 0:
        return 0.0
    return type_share * (weight / base_weight) * min(1.0, n_items / MIN_ITEMS_FOR_FULL_SHARE)


def coverage(matches: list[RequirementMatch], type_share: dict[str, float] = TYPE_SHARE,
             credit: Callable[[RequirementMatch], float] | None = None) -> float:
    """0-1 score over core types: per type, the weight-averaged credit (default met 1, partial 0.5); the types
    combined by effective_share(), so a SOC with 237 tech rows isn't scored on tech alone, a type made only of
    curated rows counts half, and a type with one item counts a fifth."""
    credit = credit or (lambda m: CREDIT[m.status])
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    base: dict[str, float] = {}
    count: dict[str, int] = {}
    for m in matches:
        t = m.item.item_type
        if t not in type_share:
            continue
        num[t] = num.get(t, 0.0) + m.item.weight * credit(m)
        den[t] = den.get(t, 0.0) + m.item.weight
        base[t] = base.get(t, 0.0) + m.item.base_weight
        count[t] = count.get(t, 0) + 1
    shares = {t: effective_share(type_share[t], den[t], base[t], count[t]) for t in den}
    total = sum(shares.values())
    if total <= 0:
        return 0.0
    return sum(shares[t] / total * num[t] / den[t] for t in shares if shares[t] > 0)
