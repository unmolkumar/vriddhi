"""Fill the job cache for every demo role x city the night before a demo.

Run from module-3-job-matching-salary/:
  python scripts/prewarm.py [--force] [--dry-run] [--with-jsearch] [--with-jsearch-salary]
                            [--roles "Data Scientist,Data Analyst"] [--cities "Bengaluru,Pune"] [--yes]
Adzuna always covers every role x city. --roles/--cities limit the JSearch calls (enrichment and salary) to
those pairs; a plan above JSEARCH_CONFIRM_ABOVE JSearch calls needs --yes.
Start module 2 first (port 8002) so job skills are extracted; otherwise they're retried at search time.
Set LIVE_CACHE_TTL_HOURS high enough (e.g. 36) that the cache is still fresh on demo day.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from datetime import datetime, timezone  # noqa: E402

from src.engines.job_fetcher import fetch_jobs, ttl_hours  # noqa: E402
from src.engines.job_store import JobStore, role_key  # noqa: E402
from src.engines.locations import expand_cities  # noqa: E402
from src.providers import jsearch  # noqa: E402
from src.providers.common import ProviderError  # noqa: E402

ROLES = ["Data Scientist", "Machine Learning Engineer", "Data Engineer", "Data Analyst", "Backend Developer",
         "Full Stack Developer", "DevOps Engineer"]
CITIES = ["Bengaluru", "Hyderabad", "Mumbai", "Delhi-NCR", "Pune"]
QUOTAS = {"adzuna": "~250/day, 2,500/month", "jsearch": "200/month on the free plan"}
CALL_STATUSES = {"ok", "empty", "error", "timeout", "not_subscribed"}  # statuses that spent a request
JSEARCH_CONFIRM_ABOVE = 20     # JSearch calls a run may plan without --yes (200/month quota)


def _list(text: str | None, default: list[str]) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()] if text else default


def jsearch_plan(pairs: list[tuple[str, str]], *, with_jsearch: bool, with_salary: bool) -> dict[str, int]:
    """Most JSearch calls a run can make: one search per leaf city (Delhi-NCR = 3) per pair, as enrichment
    after Adzuna or as the fallback when Adzuna fails, and one salary call per pair."""
    return {"search": sum(len(expand_cities(c)) for _, c in pairs) if with_jsearch else 0,
            "salary": len(pairs) if with_salary else 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="refetch even when the cache is fresh")
    parser.add_argument("--dry-run", action="store_true", help="show the plan and the maximum calls, fetch nothing")
    parser.add_argument("--with-jsearch-salary", action="store_true",
                        help="also cache JSearch salary estimates (one call per role x city, kept 7 days)")
    parser.add_argument("--with-jsearch", action="store_true",
                        help="also use JSearch (fallback + full-description enrichment); off by default to save its 200/month quota")
    parser.add_argument("--roles", help="comma-separated roles for JSearch calls (default: all demo roles)")
    parser.add_argument("--cities", help="comma-separated cities for JSearch calls (default: all demo cities)")
    parser.add_argument("--yes", action="store_true", help=f"allow more than {JSEARCH_CONFIRM_ABOVE} JSearch calls")
    args = parser.parse_args()
    load_dotenv()

    pairs = [(role, city) for role in ROLES for city in CITIES]
    js_pairs = [(r, c) for r in _list(args.roles, ROLES) for c in _list(args.cities, CITIES)]
    leaf_cities = sum(len(expand_cities(c)) for c in CITIES)
    plan = jsearch_plan(js_pairs, with_jsearch=args.with_jsearch, with_salary=args.with_jsearch_salary)
    total = sum(plan.values())
    print(f"Adzuna: {len(ROLES)} roles x {len(CITIES)} cities ({leaf_cities} after Delhi-NCR expands) = "
          f"at most {len(ROLES) * leaf_cities} calls (fresh cache skips). TTL {ttl_hours():g} h.")
    print(f"JSearch: at most {total} call(s) of the 200/month quota"
          + (f" ({plan['search']} search + {plan['salary']} salary, over {len(js_pairs)} role x city pair(s))" if total else
             " (not used)") + ".")
    if args.dry_run:
        return
    if total > JSEARCH_CONFIRM_ABOVE and not args.yes:
        sys.exit(f"Refusing to plan {total} JSearch calls without --yes (limit {JSEARCH_CONFIRM_ABOVE}). "
                 "Narrow --roles/--cities or add --yes.")

    calls, outcomes = Counter(), Counter()
    for role, city in pairs:
        js = args.with_jsearch and (role, city) in js_pairs
        result = fetch_jobs(role, city, force_refresh=args.force, use_jsearch=js, jsearch_enrichment=js)
        for a in result.attempts:
            outcomes[f"{a.provider}:{a.status}"] += 1
            if a.status in CALL_STATUSES:
                calls[a.provider] += 1
        print(f"{role:28} {city:10} {len(result.jobs):3} jobs  "
              + ", ".join(f"{a.city}:{a.provider}:{a.status}" for a in result.attempts))
    if args.with_jsearch_salary:
        store, now = JobStore(), datetime.now(timezone.utc)
        for role, city in js_pairs:
            leaf = expand_cities(city)[0]          # Delhi-NCR -> Delhi
            try:
                estimate = jsearch.estimated_salary(role, leaf, "ALL")
                calls["jsearch"] += 1
            except ProviderError as e:
                outcomes[f"jsearch_salary:{e.status}"] += 1
                continue
            if estimate:
                store.save_salary(f"{role_key(role)}|{leaf}|ALL", estimate, now)
            outcomes[f"jsearch_salary:{'ok' if estimate else 'empty'}"] += 1
    print("\nProvider calls this run:", dict(calls) or "none (all cached)")
    for provider, used in calls.items():
        print(f"  {provider}: {used} call(s); free quota {QUOTAS.get(provider, 'unknown')}")
    print("Outcomes:", dict(outcomes))


if __name__ == "__main__":
    main()
