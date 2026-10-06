"""Calibration harness: every calibration profile scored against every fixture occupation.

Used by scripts/calibrate.py (report + threshold search) and tests/calibration (accuracy floor). Similarities are
computed once per (profile, occupation); thresholds and type shares are then re-applied cheaply.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.general import matcher
from src.general.embeddings import Encoder, cosine
from src.general.evidence import EvidenceUnit, from_text
from src.general.m1_client import FixtureM1Client
from src.general.requirements import FilterReport, RequirementItem, domain_texts, normalise

PROFILES_DIR = Path(__file__).resolve().parents[2] / "tests" / "calibration" / "profiles"
PARTIAL_PREFIX = "partial_"


@dataclass
class Profile:
    name: str
    soc: str              # the occupation this profile was written for
    partial: bool
    units: list[EvidenceUnit]


@dataclass
class Occupation:
    soc: str
    title: str
    items: list[RequirementItem]
    report: FilterReport
    vectors: np.ndarray


TYPES = list(matcher.THRESHOLDS)


@dataclass
class Pair:
    """One profile against one occupation: per requirement, its type index (TYPES), weight, best similarity,
    and whether the alias layer met it."""
    type_idx: np.ndarray
    weights: np.ndarray
    best: np.ndarray
    alias: np.ndarray
    best_unit: list[int | None] = field(default_factory=list)


def load_profiles(directory: Path = PROFILES_DIR) -> list[Profile]:
    out = []
    for f in sorted(directory.glob("*.txt")):
        partial = f.name.startswith(PARTIAL_PREFIX)
        soc = f.name.removeprefix(PARTIAL_PREFIX).split("_", 1)[0]
        out.append(Profile(f.stem, soc, partial, from_text(f.read_text(encoding="utf-8"))))
    return out


def load_occupations(client: FixtureM1Client, encoder: Encoder) -> list[Occupation]:
    out = []
    for soc, occ in client.occupations.items():
        rows = client.requirements(soc)
        domain = encoder.encode_cached(f"{soc}-domain", domain_texts(occ["title"], occ.get("domain"), occ.get("description")))

        def similarity(texts: list[str], domain=domain, soc=soc) -> list[float]:
            return cosine(encoder.encode_cached(f"{soc}-offdomain", texts), domain).max(axis=1).tolist()

        items, report = normalise(rows, title=occ["title"], domain_similarity=similarity)
        out.append(Occupation(soc, occ["title"], items, report, encoder.encode_cached(soc, [i.text for i in items])))
    return out


def pair(profile: Profile, occ: Occupation, unit_vectors: np.ndarray) -> Pair:
    sims = cosine(occ.vectors, unit_vectors)
    by_skill = {sid for u in profile.units for sid in u.skill_ids}
    alias = np.array([i.item_type in matcher.ALIAS_TYPES and (matcher.requirement_skill_id(i.name) in by_skill)
                      for i in occ.items], dtype=bool)
    best = sims.max(axis=1) if sims.shape[1] else np.zeros(len(occ.items))
    return Pair(np.array([TYPES.index(i.item_type) for i in occ.items], dtype=int),
                np.array([i.weight for i in occ.items]), best, alias,
                [int(x) for x in sims.argmax(axis=1)] if sims.shape[1] else [None] * len(occ.items))


def pair_coverage(p: Pair, thresholds: dict[str, tuple[float, float]], type_share: dict[str, float]) -> float:
    """matcher.coverage() on precomputed similarities, vectorised for the threshold search."""
    met = np.array([thresholds[t][0] for t in TYPES])[p.type_idx]
    partial = np.array([thresholds[t][1] for t in TYPES])[p.type_idx]
    credit = np.where(p.alias | (p.best >= met), 1.0, np.where(p.best >= partial, 0.5, 0.0))
    num = np.bincount(p.type_idx, weights=p.weights * credit, minlength=len(TYPES))
    den = np.bincount(p.type_idx, weights=p.weights, minlength=len(TYPES))
    share = np.array([type_share.get(t, 0.0) for t in TYPES]) * (den > 0)
    if share.sum() <= 0:
        return 0.0
    return float((share / share.sum() * np.divide(num, den, out=np.zeros_like(num), where=den > 0)).sum())


@dataclass
class Result:
    socs: list[str]
    names: list[str]
    matrix: np.ndarray          # profiles x occupations
    top1: int
    top3: int
    n: int
    margins: dict[str, float]   # profile -> own coverage minus best other
    ranks: dict[str, int]       # profile -> rank of its own occupation (1 = top)

    @property
    def mean_margin(self) -> float:
        full = [m for name, m in self.margins.items() if not name.startswith(PARTIAL_PREFIX)]
        return statistics.mean(full) if full else 0.0


def evaluate(profiles: list[Profile], occupations: list[Occupation], pairs: dict[tuple[str, str], Pair],
             thresholds=None, type_share=None) -> Result:
    thresholds = thresholds or matcher.THRESHOLDS
    type_share = type_share or matcher.TYPE_SHARE
    socs = [o.soc for o in occupations]
    matrix = np.array([[pair_coverage(pairs[(p.name, s)], thresholds, type_share) for s in socs] for p in profiles])
    top1 = top3 = n = 0
    margins, ranks = {}, {}
    for row, p in zip(matrix, profiles):
        own = socs.index(p.soc)
        order = list(np.argsort(-row))
        ranks[p.name] = order.index(own) + 1
        margins[p.name] = float(row[own] - max(v for j, v in enumerate(row) if j != own))
        if not p.partial:
            n += 1
            top1 += ranks[p.name] == 1
            top3 += ranks[p.name] <= 3
    return Result(socs, [p.name for p in profiles], matrix, top1, top3, n, margins, ranks)


def build(encoder: Encoder | None = None, client: FixtureM1Client | None = None):
    """Everything evaluate() needs: profiles, occupations and the precomputed pairs."""
    encoder = encoder or Encoder()
    client = client or FixtureM1Client()
    profiles = load_profiles()
    occupations = load_occupations(client, encoder)
    pairs = {}
    for p in profiles:
        uv = encoder.encode([u.text for u in p.units])
        for o in occupations:
            pairs[(p.name, o.soc)] = pair(p, o, uv)
    return profiles, occupations, pairs
