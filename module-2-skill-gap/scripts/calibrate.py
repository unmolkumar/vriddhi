"""Calibrate the general engine on module 1's 15-occupation fixture and the calibration profiles.

Run from module-2-skill-gap/:
  python scripts/calibrate.py                    # report with the current constants
  python scripts/calibrate.py --tune             # also search thresholds and type shares, report before/after
  python scripts/calibrate.py --model BAAI/bge-small-en-v1.5 --tune

Prints: the profiles x occupations matrix, top-1/top-3, margins, per-type similarity of true vs false matches,
filter reports, and the nurse profile's matched/missing list. Writes tests/calibration/results/<model>.json.
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.general import calibration as cal  # noqa: E402
from src.general import matcher  # noqa: E402
from src.general.embeddings import Encoder, model_name  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "tests" / "calibration" / "results"
NAME_TYPES, SENTENCE_TYPES = ("tech", "tool", "market_skill"), ("task", "dwa")
GENERIC_TYPES = ("knowledge", "skill", "work_activity", "ability")
_INITIAL = {"task": 0.25, "dwa": 0.10, "market_skill": 0.20, "tech": 0.10, "tool": 0.05,
            "knowledge": 0.15, "skill": 0.075, "work_activity": 0.075, "ability": 0.0}
SHARE_PRESETS = {   # absolute, so the search doesn't drift with whatever matcher.TYPE_SHARE currently is
    "initial": _INITIAL,
    "task_heavy": {**_INITIAL, "task": 0.35, "dwa": 0.15, "tech": 0.05, "knowledge": 0.10},
    "generic_light": {**_INITIAL, "skill": 0.04, "work_activity": 0.04, "knowledge": 0.10, "task": 0.30},
    "market_heavy": {**_INITIAL, "market_skill": 0.30, "task": 0.25, "tech": 0.05},
    "market_generic_light": {**_INITIAL, "market_skill": 0.30, "task": 0.30, "tech": 0.05, "knowledge": 0.10,
                             "skill": 0.04, "work_activity": 0.04},
}


def thresholds_for(name_met: float, sent_met: float, gen_met: float, gap: float) -> dict[str, tuple[float, float]]:
    out = {}
    for types, met in ((NAME_TYPES, name_met), (SENTENCE_TYPES, sent_met), (GENERIC_TYPES, gen_met)):
        out.update({t: (met, round(met - gap, 3)) for t in types})
    return out


def objective(r: cal.Result) -> tuple:
    full = [m for n, m in r.margins.items() if not n.startswith(cal.PARTIAL_PREFIX)]
    return r.top1, r.top3, round(min(full), 3), round(r.mean_margin, 3)


def tune(profiles, occupations, pairs, shift: float = 0.0):
    best = None
    up = lambda xs: tuple(round(x + shift, 3) for x in xs)  # noqa: E731  (models with a higher cosine scale)
    grid = itertools.product(up((0.55, 0.60, 0.65, 0.70)), up((0.45, 0.50, 0.55, 0.60)), up((0.35, 0.40, 0.45, 0.50)),
                             (0.10, 0.13, 0.16), SHARE_PRESETS)
    for name_met, sent_met, gen_met, gap, preset in grid:
        th = thresholds_for(name_met, sent_met, gen_met, gap)
        r = cal.evaluate(profiles, occupations, pairs, th, SHARE_PRESETS[preset])
        key = objective(r)
        if best is None or key > best[0]:
            best = (key, th, preset, r)
    return best


def distributions(profiles, occupations, pairs, thresholds) -> dict:
    """Best-evidence similarity per item type, for each profile's own occupation (true) vs the others (false)."""
    out = {}
    for t in cal.TYPES:
        idx = cal.TYPES.index(t)
        true, false = [], []
        for p in profiles:
            if p.partial:
                continue
            for o in occupations:
                pr = pairs[(p.name, o.soc)]
                vals = pr.best[pr.type_idx == idx].tolist()
                (true if o.soc == p.soc else false).extend(vals)
        if not true:
            continue
        met = thresholds[t][0]
        out[t] = {"true_median": round(statistics.median(true), 3), "true_p90": round(float(np.percentile(true, 90)), 3),
                  "false_median": round(statistics.median(false), 3),
                  "false_p90": round(float(np.percentile(false, 90)), 3),
                  "true_met_rate": round(sum(v >= met for v in true) / len(true), 3),
                  "false_met_rate": round(sum(v >= met for v in false) / len(false), 3)}
    return out


def print_result(label: str, r: cal.Result) -> None:
    print(f"\n== {label}: top-1 {r.top1}/{r.n}, top-3 {r.top3}/{r.n}, mean margin {r.mean_margin:+.3f}")
    short = [s[:7] for s in r.socs]
    print(" " * 34 + " ".join(f"{s:>7}" for s in short))
    for name, row in zip(r.names, r.matrix):
        own = name.removeprefix(cal.PARTIAL_PREFIX).split("_", 1)[0]
        cells = " ".join((f"[{v:.2f}]" if s == own else f" {v:.2f} ").rjust(7) for s, v in zip(r.socs, row))
        print(f"{name[:33]:33} {cells}   rank {r.ranks[name]} margin {r.margins[name]:+.3f}")


def nurse_sample(profiles, occupations, encoder, thresholds) -> list[dict]:
    p = next(x for x in profiles if x.name.startswith("29-1141.00"))
    o = next(x for x in occupations if x.soc == "29-1141.00")
    matches = matcher.match(o.items, p.units, encoder, cache_key=o.soc, thresholds=thresholds)
    core = [m for m in matches if m.item.layer == "core"]
    core.sort(key=lambda m: -m.item.weight)
    return [{"type": m.item.item_type, "requirement": m.item.name[:90], "status": m.status, "similarity": m.similarity,
             "reason": m.reason, "evidence": (m.evidence_text or "")[:90], "evidence_type": m.evidence_type}
            for m in core[:25]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=None, help="sentence-transformers model (default: EMBEDDING_MODEL or MiniLM)")
    ap.add_argument("--tune", action="store_true", help="search thresholds and type shares")
    ap.add_argument("--grid-shift", type=float, default=0.0, help="add to every threshold in the search grid")
    ap.add_argument("--filters", default="13-2011.00,47-2111.00,17-2051.00", help="SOCs to print filter reports for")
    args = ap.parse_args()

    encoder = Encoder(args.model or model_name())
    profiles, occupations, pairs = cal.build(encoder)
    before = cal.evaluate(profiles, occupations, pairs)
    print_result(f"{encoder.name}, current constants", before)
    out = {"model": encoder.name, "before": {"top1": before.top1, "top3": before.top3, "n": before.n,
                                             "mean_margin": round(before.mean_margin, 4), "ranks": before.ranks,
                                             "margins": {k: round(v, 4) for k, v in before.margins.items()}}}
    thresholds, share = matcher.THRESHOLDS, matcher.TYPE_SHARE
    if args.tune:
        key, thresholds, preset, after = tune(profiles, occupations, pairs, args.grid_shift)
        share = SHARE_PRESETS[preset]
        print_result(f"tuned (type shares '{preset}')", after)
        print("thresholds:", json.dumps(thresholds))
        print("type_share:", json.dumps(share))
        out["after"] = {"top1": after.top1, "top3": after.top3, "mean_margin": round(after.mean_margin, 4),
                        "min_margin": key[2], "thresholds": thresholds, "type_share": share, "preset": preset,
                        "ranks": after.ranks, "margins": {k: round(v, 4) for k, v in after.margins.items()}}

    dist = distributions(profiles, occupations, pairs, thresholds)
    print("\n== best-evidence similarity, own occupation (true) vs others (false)")
    for t, d in dist.items():
        print(f"  {t:14} {d}")
    out["distributions"] = dist

    out["filters"] = {}
    for o in occupations:
        out["filters"][o.soc] = o.report.model_dump()
        if o.soc in args.filters.split(","):
            print(f"\n== filter report {o.soc} {o.title}: kept {o.report.kept}")
            for reason, names in o.report.dropped.items():
                print(f"  dropped {reason} ({len(names)}): {sorted(set(names))[:12]}")
            for reason, names in o.report.down_weighted.items():
                print(f"  down-weighted {reason} ({len(names)}): {names[:12]}")

    sample = nurse_sample(profiles, occupations, encoder, thresholds)
    print("\n== staff nurse profile vs Registered Nurses (core requirements, heaviest first)")
    for s in sample:
        print(f"  {s['status']:8} {s['similarity']:.2f} {s['reason']:8} {s['type']:12} {s['requirement'][:60]:60} <- {s['evidence'][:60]}")
    out["nurse_sample"] = sample

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{encoder.name.replace('/', '_')}{'_shift' + str(args.grid_shift) if args.grid_shift else ''}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
