"""Calibrate the general engine: tune on the tuning profiles only, then report held-out results.

Run from module-2-skill-gap/:
  python scripts/calibrate.py            # the four-way report with the shipped (frozen) constants
  python scripts/calibrate.py --tune     # also search thresholds and type shares on the tuning set only
  python scripts/calibrate.py --model BAAI/bge-small-en-v1.5 --tune --grid-shift 0.15

Report, on the 25-occupation matrix (15 export + 10 extra occupations):
  (a) tuning profiles  (b) held-out profiles  (c) profiles for the 10 extra occupations
  (d) held-out profiles with every curated requirement removed
plus worst cases, per-type similarity, filter reports and the nurse sample with inferred generic layers.
Writes tests/calibration/results/<model>.json.
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
from src.general import inference, matcher  # noqa: E402
from src.general.embeddings import Encoder, model_name  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "tests" / "calibration" / "results"
NAME_TYPES, SENTENCE_TYPES = ("tech", "tool", "market_skill"), ("task", "dwa")
SHARE_PRESETS = {   # absolute, so the search doesn't drift with whatever matcher.TYPE_SHARE currently is
    "balanced": {"task": 0.35, "dwa": 0.15, "market_skill": 0.30, "tech": 0.10, "tool": 0.10},
    "task_heavy": {"task": 0.45, "dwa": 0.20, "market_skill": 0.20, "tech": 0.10, "tool": 0.05},
    "market_heavy": {"task": 0.30, "dwa": 0.10, "market_skill": 0.40, "tech": 0.10, "tool": 0.10},
    "dwa_heavy": {"task": 0.30, "dwa": 0.30, "market_skill": 0.25, "tech": 0.10, "tool": 0.05},
    "tech_light": {"task": 0.40, "dwa": 0.20, "market_skill": 0.30, "tech": 0.05, "tool": 0.05},
}
EXPORT_SOCS: set[str] = set()


def thresholds_for(name_met: float, sent_met: float, gap: float) -> dict[str, tuple[float, float]]:
    out = {t: (name_met, round(name_met - gap, 3)) for t in NAME_TYPES}
    out.update({t: (sent_met, round(sent_met - gap, 3)) for t in SENTENCE_TYPES})
    return out


def objective(r: cal.Result) -> tuple:
    return r.top1, r.top3, round(r.worst[1], 3), round(r.mean_margin, 3)


def tune(profiles, occupations, pairs, shift: float = 0.0):
    """Grid search on the tuning profiles against the 15 export occupations only."""
    tp, to = cal.subset(profiles, occupations, sets=("tuning",), socs=EXPORT_SOCS)
    up = lambda xs: tuple(round(x + shift, 3) for x in xs)  # noqa: E731  (models with a higher cosine scale)
    best = None
    for name_met, sent_met, gap, preset in itertools.product(up((0.55, 0.60, 0.65, 0.70)), up((0.45, 0.50, 0.55, 0.60)),
                                                             (0.10, 0.13, 0.16), SHARE_PRESETS):
        th = thresholds_for(name_met, sent_met, gap)
        r = cal.evaluate(tp, to, pairs, th, SHARE_PRESETS[preset])
        if best is None or objective(r) > best[0]:
            best = (objective(r), th, preset, r)
    return best


def summary(r: cal.Result) -> dict:
    name, worst = r.worst
    misses = {n: {"rank": r.ranks[n], "own": round(float(r.matrix[r.names.index(n)][r.socs.index(n.split("/")[1].removeprefix(cal.PARTIAL_PREFIX).split("_")[0])]), 3),
                  "runner_up": r.runner_up[n], "margin": round(r.margins[n], 3)}
              for n in r.names if r.ranks[n] > 1 and cal.PARTIAL_PREFIX not in n}
    return {"top1": r.top1, "top3": r.top3, "n": r.n, "mean_margin": round(r.mean_margin, 4),
            "worst": {"profile": name, "margin": round(worst, 4), "runner_up": r.runner_up[name]},
            "misses": misses, "ranks": r.ranks, "margins": {k: round(v, 4) for k, v in r.margins.items()}}


def four_way(profiles, occupations, pairs, thresholds, share) -> dict:
    """(a)-(d) on all 25 occupations, plus the held-out set against the 15 export occupations only (like for like:
    the 10 extra occupations come from a v2.0 database without DWA, tool or curated rows)."""
    out = {}
    for label, sets, drop, socs in (("a_tuning", ("tuning",), False, None), ("b_heldout", ("heldout",), False, None),
                                    ("c_new_occupations", ("new",), False, None),
                                    ("d_heldout_no_curated", ("heldout",), True, None),
                                    ("b_heldout_vs_export15", ("heldout",), False, EXPORT_SOCS)):
        ps, os_ = cal.subset(profiles, occupations, sets=sets, socs=socs)
        out[label] = summary(cal.evaluate(ps, os_, pairs, thresholds, share, drop_curated=drop))
    return out


def print_four_way(fw: dict, titles: dict[str, str]) -> None:
    print(f"\n== 25-occupation matrix ({len(titles)} occupations)")
    print(f"  {'set':24} {'top-1':>7} {'top-3':>7} {'mean margin':>12} {'worst margin':>13}  worst profile -> runner-up")
    for label, s in fw.items():
        w = s["worst"]
        print(f"  {label:24} {s['top1']:>3}/{s['n']:<3} {s['top3']:>3}/{s['n']:<3} {s['mean_margin']:>+12.3f} "
              f"{w['margin']:>+13.3f}  {w['profile']} -> {titles[w['runner_up']][:30]}")
        for n, m in s["misses"].items():
            print(f"      miss: {n:48} rank {m['rank']}  own {m['own']:.2f}  beaten by {titles[m['runner_up']][:34]} "
                  f"(margin {m['margin']:+.3f})")


def distributions(profiles, occupations, pairs) -> dict:
    """Best-evidence similarity per core type: own occupation (true) vs the others (false), tuning set."""
    out = {}
    for t in cal.TYPES:
        idx = cal.TYPES.index(t)
        true, false = [], []
        for p in profiles:
            if p.partial or p.set != "tuning":
                continue
            for o in occupations:
                pr = pairs[(p.name, o.soc)]
                (true if o.soc == p.soc else false).extend(pr.best[pr.type_idx == idx].tolist())
        if true and false:
            out[t] = {"true_median": round(statistics.median(true), 3), "false_median": round(statistics.median(false), 3),
                      "true_p90": round(float(np.percentile(true, 90)), 3), "false_p90": round(float(np.percentile(false, 90)), 3)}
    return out


def nurse_sample(profiles, occupations, encoder, thresholds) -> dict:
    p = next(x for x in profiles if x.name.startswith("tuning/29-1141.00"))
    o = next(x for x in occupations if x.soc == "29-1141.00")
    matches = matcher.match(o.all_items, p.units, encoder, thresholds=thresholds)
    gen = inference.infer(o.all_items, matches, p.units, encoder)
    top = sorted(matches, key=lambda m: -m.item.weight)[:20]
    return {
        "core": [{"type": m.item.item_type, "provenance": m.provenance, "requirement": m.item.name[:90], "status": m.status,
                  "similarity": m.similarity, "reason": m.reason, "evidence": (m.evidence_text or "")[:90]} for m in top],
        "work_activities": [{"name": w.item.name, "status": w.status, "via": w.via[:2]} for w in gen.work_activities[:10]],
        "draws_on": [{"type": d.item.item_type, "name": d.item.name, "inferred": d.inferred, "support": (d.support or "")[:70],
                      "kind": d.support_kind} for d in gen.draws_on],
        "fit_indicators": [f.name for f in gen.fit_indicators]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=None, help="sentence-transformers model (default: EMBEDDING_MODEL or MiniLM)")
    ap.add_argument("--tune", action="store_true", help="search thresholds and type shares on the tuning set")
    ap.add_argument("--grid-shift", type=float, default=0.0, help="add to every threshold in the search grid")
    ap.add_argument("--filters", default="13-2011.00,47-2111.00,17-2051.00", help="SOCs to print filter reports for")
    args = ap.parse_args()

    encoder = Encoder(args.model or model_name())
    client = cal.FixtureM1Client(cal.FIXTURE_PATH, cal.EXTRA_FIXTURE_PATH)
    EXPORT_SOCS.update(soc for soc, v in client.versions.items() if v.startswith("2.1"))
    profiles, occupations, pairs = cal.build(encoder, client)
    titles = {o.soc: o.title for o in occupations}
    out: dict = {"model": encoder.name, "export_version": client.version()}
    thresholds, share = matcher.THRESHOLDS, matcher.TYPE_SHARE

    if args.tune:
        key, thresholds, preset, r = tune(profiles, occupations, pairs, args.grid_shift)
        share = SHARE_PRESETS[preset]
        print(f"== tuned on tuning set x 15 export occupations: top-1 {r.top1}/{r.n}, mean {r.mean_margin:+.3f}, "
              f"worst {r.worst[1]:+.3f} ({r.worst[0]})")
        print("thresholds:", json.dumps(thresholds))
        print(f"type_share ({preset}):", json.dumps(share))
        out["tuned"] = {"thresholds": thresholds, "type_share": share, "preset": preset, "tuning_objective": list(key)}

    fw = four_way(profiles, occupations, pairs, thresholds, share)
    print_four_way(fw, titles)
    out["four_way"] = fw
    out["distributions"] = dist = distributions(profiles, occupations, pairs)
    print("\n== best-evidence similarity, own occupation vs others (tuning set, medians)")
    for t, d in dist.items():
        print(f"  {t:14} {d}")

    out["filters"] = {o.soc: o.report.model_dump() for o in occupations}
    for o in occupations:
        if o.soc in args.filters.split(","):
            print(f"\n== filter report {o.soc} {o.title}: kept {o.report.kept}")
            for reason, names in o.report.dropped.items():
                print(f"  dropped {reason} ({len(names)}): {sorted(set(names))[:12]}")
            for reason, names in o.report.down_weighted.items():
                print(f"  down-weighted {reason} ({len(names)}): {names[:12]}")

    sample = nurse_sample(profiles, occupations, encoder, thresholds)
    out["nurse_sample"] = sample
    print("\n== staff nurse (tuning) vs Registered Nurses: core requirements, heaviest first")
    for s in sample["core"]:
        print(f"  {s['status']:8} {s['similarity']:.2f} {s['reason']:8} {s['provenance']:9} {s['type']:12} "
              f"{s['requirement'][:55]:55} <- {s['evidence'][:50]}")
    print("  work activities:", [(w["name"][:40], w["status"]) for w in sample["work_activities"]])
    print("  draws on:", [(d["name"], "inferred" if d["inferred"] else "-") for d in sample["draws_on"]])
    print("  fit indicators:", sample["fit_indicators"])

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{encoder.name.replace('/', '_')}{'_shift' + str(args.grid_shift) if args.grid_shift else ''}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
