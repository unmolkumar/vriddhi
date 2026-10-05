"""Job skill extraction via module 2's REST API (POST /api/v1/skills/extract). No module 2 imports."""
from __future__ import annotations

import os

import httpx

from src.models.schemas import Job

DEFAULT_M2_BASE_URL = "http://127.0.0.1:8002"  # not "localhost": avoids a slow IPv6 attempt on Windows
TIMEOUT = httpx.Timeout(5.0, connect=1.0)     # fail fast when module 2 is down
MAX_TEXT_CHARS = 50_000  # module 2's limit


class M2Unavailable(Exception):
    pass


def base_url() -> str:
    return os.getenv("M2_BASE_URL", "").strip().rstrip("/") or DEFAULT_M2_BASE_URL


def extract_skills(text: str, *, client: httpx.Client | None = None) -> list[dict]:
    """[{id, maps_to, ...}] from module 2 for a piece of text."""
    try:
        resp = (client or httpx).post(f"{base_url()}/api/v1/skills/extract", timeout=TIMEOUT,
                                      json={"text": text[:MAX_TEXT_CHARS], "use_llm": False})
    except httpx.HTTPError as e:
        raise M2Unavailable(type(e).__name__) from None
    if resp.status_code == 422 and "EMPTY_TEXT" in resp.text:
        return []
    if resp.status_code != 200:
        raise M2Unavailable(f"HTTP {resp.status_code}")
    return resp.json().get("skills", [])


def enrich_skills(jobs: list[Job], *, client: httpx.Client | None = None) -> tuple[list[Job], bool]:
    """Fill job.skills from module 2. Returns (jobs, module_2_available).

    Jobs already enriched are skipped. On the first failure the rest are marked 'unavailable'
    (no point hammering a module that's down); they are retried on the next search.
    """
    available = True
    out = []
    for job in jobs:
        if job.skills_source == "m2":
            out.append(job)
            continue
        if available:
            try:
                skills = extract_skills(f"{job.title}\n{job.description}", client=client)
                out.append(job.model_copy(update={
                    "skills": [s["id"] for s in skills], "skills_source": "m2",
                    "skill_parents": {s["id"]: s["maps_to"] for s in skills if s.get("maps_to")},
                    "skill_display": {s["id"]: s["display"] for s in skills if s.get("display")}}))
                continue
            except M2Unavailable:
                available = False
        out.append(job.model_copy(update={"skills": [], "skills_source": "unavailable"}))
    return out, available


def resolve_typed_skills(typed: list[str], *, client: httpx.Client | None = None) -> tuple[list[dict], list[str]]:
    """Typed skill names -> module 2 skills ("Postgres" -> postgresql, with maps_to sql).

    One call with the names as a comma-separated list, so ambiguous names like Go or R are read as list
    items. Returns (skills, names module 2 didn't recognise). Raises M2Unavailable if module 2 is down.
    """
    names = [t.strip() for t in typed if t and t.strip()]
    if not names:
        return [], []
    skills = extract_skills(", ".join(names), client=client)
    surfaces = [m.lower() for s in skills for m in s.get("matches", [])] + [s["id"].replace("_", " ") for s in skills]
    unknown = [n for n in names if not any(n.lower() in m or m in n.lower() for m in surfaces)]
    return skills, unknown
