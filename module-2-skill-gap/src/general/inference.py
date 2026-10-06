"""The generic O*NET layers, inferred instead of matched (they don't discriminate between occupations).

- work_activity (GWA): evidenced when any of its DWAs is met. O*NET's hierarchy is DWA -> IWA -> GWA, and the
  ids nest: a DWA or IWA id starts with its GWA's element id ('4.A.2.a.4.I01.D03' and module 1's
  '4.A.4.a.5.c.3' both sit under GWA '4.A.4.a.5').
- knowledge, skill: the top-importance ones are reported as "what this role draws on", marked inferred when a met
  task/DWA (or the education section) supports them. Never gaps to learn.
- ability: fit indicators only.
"""
from __future__ import annotations

import re
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from src.general.embeddings import Encoder, cosine
from src.general.evidence import EvidenceUnit
from src.general.matcher import RequirementMatch
from src.general.requirements import RequirementItem

GWA_PARTS = 5                  # a GWA element id has five parts: 4.A.<x>.<y>.<n>
DRAWS_ON_TOP = {"knowledge": 5, "skill": 5}   # how many of each are reported, by importance
SUPPORT_SIM = 0.45             # knowledge/skill text vs a met task/DWA to count as inferred
EDUCATION_SUPPORT_SIM = 0.35   # ... vs an education line (short degree names score lower)
FIT_TOP = 6                    # abilities reported as fit indicators
_IWA_PART = re.compile(r"^I\d+$")


def gwa_of(item_id: str) -> str:
    """The GWA element id a DWA or IWA id sits under."""
    parts = item_id.split(".")
    for i, p in enumerate(parts):
        if _IWA_PART.match(p):
            return ".".join(parts[:i])
    return ".".join(parts[:GWA_PARTS])


class WorkActivityEvidence(BaseModel):
    item: RequirementItem
    status: Literal["evidenced", "not_evidenced", "no_dwa_data"] = Field(
        description="'no_dwa_data' when module 1 sent no DWAs under this activity")
    via: list[str] = Field(default_factory=list, description="Met DWAs under it")


class DrawsOn(BaseModel):
    item: RequirementItem
    inferred: bool = Field(description="Supported by a met task/DWA or the education section; never a gap")
    support: str | None = None
    support_kind: Literal["task", "dwa", "education"] | None = None
    similarity: float | None = None


class FitIndicator(BaseModel):
    name: str
    importance: float
    level: float


class GenericInference(BaseModel):
    work_activities: list[WorkActivityEvidence]
    draws_on: list[DrawsOn]
    fit_indicators: list[FitIndicator]


def work_activities(items: list[RequirementItem], matches: list[RequirementMatch]) -> list[WorkActivityEvidence]:
    dwas = [i for i in items if i.item_type == "dwa"]
    met = {m.item.item_id for m in matches if m.item.item_type == "dwa" and m.status == "met"}
    out = []
    for gwa in sorted((i for i in items if i.item_type == "work_activity"), key=lambda i: -i.importance):
        under = [d for d in dwas if gwa_of(d.item_id) == gwa.item_id]
        via = [d.name for d in under if d.item_id in met]
        status = "evidenced" if via else "not_evidenced" if under else "no_dwa_data"
        out.append(WorkActivityEvidence(item=gwa, status=status, via=via))
    return out


def draws_on(items: list[RequirementItem], matches: list[RequirementMatch], units: list[EvidenceUnit],
             encoder: Encoder) -> list[DrawsOn]:
    top = []
    for t, n in DRAWS_ON_TOP.items():
        top += sorted((i for i in items if i.item_type == t), key=lambda i: -i.importance)[:n]
    if not top:
        return []
    support = [(m.item.text, m.item.item_type) for m in matches if m.status == "met" and m.item.item_type in ("task", "dwa")]
    support += [(u.text, "education") for u in units if u.education or u.section == "education"]
    vecs = encoder.encode([i.text for i in top])
    sims = cosine(vecs, encoder.encode([s for s, _ in support])) if support else np.zeros((len(top), 0))
    out = []
    for r, item in enumerate(top):
        best, best_sim = None, 0.0
        for c, (text, kind) in enumerate(support):
            bar = EDUCATION_SUPPORT_SIM if kind == "education" else SUPPORT_SIM
            if sims[r, c] >= bar and sims[r, c] > best_sim:
                best, best_sim = (text, kind), float(sims[r, c])
        out.append(DrawsOn(item=item, inferred=best is not None, support=best[0] if best else None,
                           support_kind=best[1] if best else None, similarity=round(best_sim, 4) if best else None))
    return out


def fit_indicators(items: list[RequirementItem]) -> list[FitIndicator]:
    abilities = sorted((i for i in items if i.item_type == "ability"), key=lambda i: -i.importance)[:FIT_TOP]
    return [FitIndicator(name=a.name, importance=a.importance, level=a.level) for a in abilities]


def infer(items: list[RequirementItem], matches: list[RequirementMatch], units: list[EvidenceUnit],
          encoder: Encoder) -> GenericInference:
    return GenericInference(work_activities=work_activities(items, matches),
                            draws_on=draws_on(items, matches, units, encoder), fit_indicators=fit_indicators(items))
