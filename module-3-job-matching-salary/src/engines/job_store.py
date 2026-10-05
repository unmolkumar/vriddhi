"""Module-local SQLite cache of fetched jobs. Within the TTL it's the cache; after, it's the stale snapshot."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.models.schemas import Job

MODULE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = MODULE_ROOT / "data" / "cache" / "jobs.sqlite"
PATH_ENV = "M3_CACHE_PATH"

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    role TEXT NOT NULL, city TEXT NOT NULL, source TEXT NOT NULL,
    fetched_at TEXT NOT NULL, total_count INTEGER,
    PRIMARY KEY (role, city)
);
CREATE TABLE IF NOT EXISTS jobs (
    role TEXT NOT NULL, city TEXT NOT NULL, job_id TEXT NOT NULL, job_json TEXT NOT NULL,
    PRIMARY KEY (role, city, job_id)
);
"""


def role_key(role: str) -> str:
    return re.sub(r"\s+", " ", role.lower()).strip()


@dataclass
class CachedQuery:
    jobs: list[Job]
    fetched_at: datetime
    source: str
    total_count: int | None


class JobStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv(PATH_ENV) or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, role: str, city: str, source: str, jobs: list[Job], total_count: int | None, fetched_at: datetime) -> None:
        """Replace what's stored for (role, city) with a fresh fetch."""
        key = role_key(role)
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM jobs WHERE role = ? AND city = ?", (key, city))
            conn.executemany("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?)",
                             [(key, city, j.job_id, j.model_dump_json()) for j in jobs])
            conn.execute("INSERT OR REPLACE INTO queries VALUES (?, ?, ?, ?, ?)",
                         (key, city, source, fetched_at.isoformat(), total_count))

    def update_jobs(self, role: str, city: str, jobs: list[Job]) -> None:
        """Rewrite job records in place (e.g. after skills were extracted), keeping fetched_at."""
        key = role_key(role)
        with closing(self._connect()) as conn, conn:
            conn.executemany("UPDATE jobs SET job_json = ? WHERE role = ? AND city = ? AND job_id = ?",
                             [(j.model_dump_json(), key, city, j.job_id) for j in jobs])

    def load(self, role: str, city: str) -> CachedQuery | None:
        key = role_key(role)
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT source, fetched_at, total_count FROM queries WHERE role = ? AND city = ?",
                               (key, city)).fetchone()
            if row is None:
                return None
            rows = conn.execute("SELECT job_json FROM jobs WHERE role = ? AND city = ?", (key, city)).fetchall()
        fetched_at = datetime.fromisoformat(row[1])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return CachedQuery(jobs=[Job.model_validate(json.loads(r[0])) for r in rows], fetched_at=fetched_at,
                           source=row[0], total_count=row[2])

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as conn:
            return {"queries": conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0],
                    "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]}
