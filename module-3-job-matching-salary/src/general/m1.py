"""Module 1 over REST (M1_BASE_URL): occupation search, profile, related occupations. No module 1 imports.

Listing titles are resolved once and kept in the module's SQLite cache (title -> top matches), so a page of
listings costs module 1 calls only for titles it hasn't seen.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.engines.job_store import DEFAULT_PATH, PATH_ENV

DEFAULT_M1_BASE_URL = "http://127.0.0.1:8001"
TIMEOUT = httpx.Timeout(10.0, connect=1.0)
SEARCH_K = 5
TITLE_CACHE_SCHEMA = "CREATE TABLE IF NOT EXISTS v2_title_soc (title TEXT PRIMARY KEY, matches TEXT NOT NULL)"


class M1Unavailable(Exception):
    pass


@dataclass(frozen=True)
class Match:
    soc_code: str
    title: str
    confidence: float
    method: str | None = None
    matched_term: str | None = None


def base_url() -> str:
    return os.getenv("M1_BASE_URL", "").strip().rstrip("/") or DEFAULT_M1_BASE_URL


def _get(client: httpx.Client | None, path: str, **params):
    try:
        resp = (client or httpx).get(f"{base_url()}{path}", params=params or None, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise M1Unavailable(type(e).__name__) from None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise M1Unavailable(f"HTTP {resp.status_code}")
    return resp.json()


def search(q: str, *, client: httpx.Client | None = None, k: int = SEARCH_K) -> list[Match]:
    rows = _get(client, "/api/v1/occupations/search", q=q, k=k) or []
    return [Match(r["soc_code"], r.get("title", ""), float(r.get("confidence") or 0.0), r.get("method"),
                  r.get("matched_term")) for r in rows]


def profile(soc: str, *, client: httpx.Client | None = None) -> dict | None:
    return _get(client, f"/api/v1/occupations/{soc}/profile")


def related(soc: str, *, client: httpx.Client | None = None, limit: int = 20) -> list[dict]:
    return _get(client, f"/api/v1/occupations/{soc}/related", limit=limit) or []


def title_key(title: str) -> str:
    return re.sub(r"\s+", " ", title.lower()).strip()


class TitleCache:
    """title -> module 1 search matches, in the module's SQLite file (table v2_title_soc)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv(PATH_ENV) or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(TITLE_CACHE_SCHEMA)

    def get_many(self, titles: list[str]) -> dict[str, list[Match]]:
        keys = list({title_key(t) for t in titles})
        out: dict[str, list[Match]] = {}
        with closing(sqlite3.connect(self.path)) as conn:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                rows = conn.execute(f"SELECT title, matches FROM v2_title_soc WHERE title IN ({','.join('?' * len(chunk))})",
                                    chunk).fetchall()
                out.update({t: [Match(**m) for m in json.loads(js)] for t, js in rows})
        return out

    def put(self, title: str, matches: list[Match]) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO v2_title_soc VALUES (?, ?)",
                         (title_key(title), json.dumps([m.__dict__ for m in matches])))


def resolve_titles(titles: list[str], *, client: httpx.Client | None = None,
                   cache: TitleCache | None = None) -> dict[str, list[Match]]:
    """title_key -> matches for every title; unseen titles go to module 1 (raises M1Unavailable if it's down)."""
    cache = cache or TitleCache()
    known = cache.get_many(titles)
    for t in dict.fromkeys(title_key(t) for t in titles):
        if t not in known:
            known[t] = search(t, client=client)
            cache.put(t, known[t])
    return known
