"""Calibration harness: profiles scored against fixture occupations, by profile set.

Profile sets (tests/calibration/profiles/<set>/<soc>_<name>.txt):
  tuning   15 profiles (+3 partial) for the 15 export occupations; the only set thresholds are tuned on
  heldout  15 more for the same occupations, in other styles; never used for tuning
  new      10 for occupations outside the export (fetched from a live module 1, no curated data)
  heldout2 25 more (15 export + 10 extra occupations) in yet other styles, written after A1b; never used for tuning
Used by scripts/calibrate.py and tests/calibration. Similarities are computed once per (profile, occupation);
thresholds and type shares are then re-applied cheaply.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.general import matcher, scoring
from src.general.embeddings import Encoder, cosine
from src.general.evidence import EvidenceUnit, Translator, from_text, role_history
from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client
from src.general.requirements import SCORED_TYPES, FilterReport, RequirementItem
from src.general.translate import FixtureTranslator

PROFILES_DIR = Path(__file__).resolve().parents[2] / "tests" / "calibration" / "profiles"
SETS = ("tuning", "heldout", "new", "heldout2", "hinglish", "verdict_tuning", "verdict_validation")
PARTIAL_PREFIX = "partial_"
TYPES = list(SCORED_TYPES)


@dataclass
class Profile:
    name: str
    soc: str              # the occupation this profile was written for
    set: str
    partial: bool                     # partial_* and wrong_* profiles: not counted in top-1/top-3
    units: list[EvidenceUnit]
    history: list[tuple[str, float | None]] = field(default_factory=list)
    kind: str = "full"                # full | partial | wrong (wrong_<target soc>: someone from another field)


@dataclass
class Occupation:
    soc: str
    title: str
    items: list[RequirementItem]       # core items (scored)
    all_items: list[RequirementItem]   # every layer (for inference)
    report: FilterReport
    vectors: np.ndarray                # expanded rows of items (matcher.expand)
    starts: np.ndarray


@dataclass
class Pair:
    """One profile against one occupation, per core requirement: type index (TYPES), weight, base weight,
    curated flag, best similarity, and whether the alias layer met it."""
    type_idx: np.ndarray
    weights: np.ndarray
    base: np.ndarray
    curated: np.ndarray
    best: np.ndarray
    alias: np.ndarray
    best_unit: list[int | None] = field(default_factory=list)
    implied: float = 0.0              # role-history credit for this occupation's tasks/DWAs without evidence


WRONG_PREFIX = "wrong_"


def load_profiles(directory: Path = PROFILES_DIR, sets: tuple[str, ...] = SETS,
                  translator: Translator | None = None) -> list[Profile]:
    """<soc>_<name>.txt (full), partial_<soc>_<name>.txt, wrong_<target soc>_<name>.txt."""
    out = []
    for s in sets:
        for f in sorted((directory / s).glob("*.txt")):
            kind = "partial" if f.name.startswith(PARTIAL_PREFIX) else "wrong" if f.name.startswith(WRONG_PREFIX) else "full"
            soc = f.name.removeprefix(PARTIAL_PREFIX).removeprefix(WRONG_PREFIX).split("_", 1)[0]
            text = f.read_text(encoding="utf-8")
            out.append(Profile(f"{s}/{f.stem}", soc, s, kind != "full", from_text(text, translator=translator),
                               role_history(text), kind))
    return out


def load_occupations(client: FixtureM1Client, encoder: Encoder) -> list[Occupation]:
    from src.general.service import prepare_occupation   # the engine's own preparation, so calibration matches it

    out = []
    for soc, occ in client.occupations.items():
        d = prepare_occupation(soc, occ, client.requirements(soc), encoder, client.version_of(soc))
        out.append(Occupation(soc, d.title, d.core, d.items, d.report, d.vectors, d.starts))
    return out


def pair(profile: Profile, occ: Occupation, unit_vectors: np.ndarray, implied: float = 0.0) -> Pair:
    """Same matching as the engine: max over a requirement's clauses, title lines never matched."""
    matchable = np.array([u.matchable for u in profile.units], dtype=bool)
    by_skill = {sid for u in profile.units if u.matchable for sid in u.skill_ids}
    alias = np.array([i.item_type in matcher.ALIAS_TYPES and (matcher.requirement_skill_id(i.name) in by_skill)
                      for i in occ.items], dtype=bool)
    if len(occ.items) and matchable.any():
        sims = np.where(matchable[None, :], cosine(occ.vectors, unit_vectors), -1.0)
        best, unit = matcher.best_rows(sims, occ.starts)
        best = np.maximum(best, 0.0)
    else:
        best, unit = np.zeros(len(occ.items)), np.zeros(len(occ.items), dtype=int)
    return Pair(np.array([TYPES.index(i.item_type) for i in occ.items], dtype=int),
                np.array([i.weight for i in occ.items]), np.array([i.base_weight for i in occ.items]),
                np.array([i.provenance == "curated" for i in occ.items], dtype=bool), best, alias,
                [int(x) for x in unit], implied)


def pair_coverage(p: Pair, thresholds: dict[str, tuple[float, float]], type_share: dict[str, float],
                  drop_curated: bool = False) -> float:
    """matcher.coverage() on precomputed similarities, vectorised for the threshold search."""
    keep = ~p.curated if drop_curated else np.ones(len(p.best), dtype=bool)
    idx, w, base = p.type_idx[keep], p.weights[keep], p.base[keep]
    met = np.array([thresholds[t][0] for t in TYPES])[idx]
    partial = np.array([thresholds[t][1] for t in TYPES])[idx]
    credit = np.where(p.alias[keep] | (p.best[keep] >= met), 1.0, np.where(p.best[keep] >= partial, 0.5, 0.0))
    if p.implied:
        implied_types = np.isin(idx, [TYPES.index(t) for t in scoring.IMPLIED_TYPES])
        credit = np.where((credit == 0) & implied_types, p.implied, credit)
    num = np.bincount(idx, weights=w * credit, minlength=len(TYPES))
    den = np.bincount(idx, weights=w, minlength=len(TYPES))
    bas = np.bincount(idx, weights=base, minlength=len(TYPES))
    reliability = np.divide(den, bas, out=np.zeros_like(den), where=bas > 0)
    counts = np.bincount(idx, minlength=len(TYPES))
    share = (np.array([type_share.get(t, 0.0) for t in TYPES]) * reliability * (den > 0)
             * np.minimum(1.0, counts / matcher.MIN_ITEMS_FOR_FULL_SHARE))
    if share.sum() <= 0:
        return 0.0
    return float((share / share.sum() * np.divide(num, den, out=np.zeros_like(num), where=den > 0)).sum())


def is_full(name: str) -> bool:
    """A full profile (not partial_* or wrong_*), counted in top-1 / top-3 and margins."""
    return PARTIAL_PREFIX not in name and WRONG_PREFIX not in name


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
    runner_up: dict[str, str]   # profile -> best other occupation

    def _full(self) -> dict[str, float]:
        return {n: m for n, m in self.margins.items() if is_full(n)}

    @property
    def mean_margin(self) -> float:
        return statistics.mean(self._full().values()) if self._full() else 0.0

    @property
    def worst(self) -> tuple[str, float]:
        full = self._full()
        name = min(full, key=full.get)
        return name, full[name]


def evaluate(profiles: list[Profile], occupations: list[Occupation], pairs: dict[tuple[str, str], Pair],
             thresholds=None, type_share=None, *, drop_curated: bool = False) -> Result:
    thresholds = thresholds or matcher.THRESHOLDS
    type_share = type_share or matcher.TYPE_SHARE
    socs = [o.soc for o in occupations]
    matrix = np.array([[pair_coverage(pairs[(p.name, s)], thresholds, type_share, drop_curated) for s in socs]
                       for p in profiles])
    top1 = top3 = n = 0
    margins, ranks, runner_up = {}, {}, {}
    for row, p in zip(matrix, profiles):
        own = socs.index(p.soc)
        order = list(np.argsort(-row, kind="stable"))
        ranks[p.name] = order.index(own) + 1
        other = max((j for j in range(len(socs)) if j != own), key=lambda j: row[j])
        margins[p.name] = float(row[own] - row[other])
        runner_up[p.name] = socs[other]
        if not p.partial:
            n += 1
            top1 += ranks[p.name] == 1
            top3 += ranks[p.name] <= 3
    return Result(socs, [p.name for p in profiles], matrix, top1, top3, n, margins, ranks, runner_up)


def implied_by_soc(profile: Profile, client) -> dict[str, float]:
    """Role history as the engine applies it: each past title resolved through module 1's search (fixture here);
    a title at ROLE_MIN_CONFIDENCE implies its occupation's tasks/DWAs at scoring.implied_credit(years)."""
    years: dict[str, list[float | None]] = {}
    for title, y in profile.history:
        res = client.search(title, 1)
        if res.matches and res.matches[0].confidence >= scoring.ROLE_MIN_CONFIDENCE:
            years.setdefault(res.matches[0].soc_code, []).append(y)
    out = {}
    for soc, ys in years.items():
        known = [y for y in ys if y is not None]
        out[soc] = scoring.implied_credit(sum(known) if known else None)
    return out


def build(encoder: Encoder | None = None, client: FixtureM1Client | None = None, sets: tuple[str, ...] = SETS,
          translator: Translator | None = None):
    """Everything evaluate() needs: profiles, occupations (export + extra fixtures) and the precomputed pairs.
    Non-English sentences use the committed translations (FixtureTranslator) unless a translator is given."""
    encoder = encoder or Encoder()
    client = client or FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH)
    profiles = load_profiles(sets=sets, translator=translator or FixtureTranslator())
    occupations = load_occupations(client, encoder)
    pairs = {}
    for p in profiles:
        uv = encoder.encode([u.text for u in p.units])
        implied = implied_by_soc(p, client)
        for o in occupations:
            pairs[(p.name, o.soc)] = pair(p, o, uv, implied.get(o.soc, 0.0))
    return profiles, occupations, pairs


def subset(profiles: list[Profile], occupations: list[Occupation], *, sets: tuple[str, ...] | None = None,
           socs: set[str] | None = None, profile_socs: set[str] | None = None,
           exclude_profile_socs: set[str] | None = None) -> tuple[list[Profile], list[Occupation]]:
    """Profiles of some sets (optionally only those written for, or not for, some occupations), against some
    occupations."""
    ps = [p for p in profiles if (sets is None or p.set in sets)
          and (profile_socs is None or p.soc in profile_socs)
          and (exclude_profile_socs is None or p.soc not in exclude_profile_socs)]
    return ps, [o for o in occupations if socs is None or o.soc in socs]
