"""Module 1 occupation endpoints over REST, with an on-disk cache per module 1 version, and a fixture twin.

Never imports module 1 or opens its database (AGENTS.md isolation). Endpoints:
  GET /api/v1/occupations/search?q=&k=         title -> SOC candidates with confidence
  GET /api/v1/occupations/{soc}/requirements   rows of v_occupation_requirements
  GET /api/v1/occupations/{soc}/profile        job zone, education, experience, salary, related
  GET /api/v1/occupations/{soc}/related        related occupations
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = MODULE_ROOT / "data" / "cache" / "m1"
FIXTURE_PATH = MODULE_ROOT / "tests" / "mocks" / "m1_occupation_requirements_export.json"          # v2.1, 15 SOCs
EXTRA_FIXTURE_PATH = MODULE_ROOT / "tests" / "mocks" / "m1_heldout_occupations.json"   # 10 SOCs from a live module 1
# Module 1's india_title_aliases as documented in its HANDOVER.md, for the fixture's search only.
FIXTURE_ALIASES = {
    "staff nurse": "29-1141.00", "nursing officer": "29-1141.00", "sister in charge": "29-1141.00",
    "icu nurse": "29-1141.00", "ca": "13-2011.00", "chartered accountant": "13-2011.00",
    "accounts executive": "13-2011.00", "internal auditor": "13-2011.00", "site engineer": "17-2051.00",
    "civil site supervisor": "17-2051.00", "telecaller": "43-4051.00", "bpo executive": "43-4051.00",
    "customer care executive": "43-4051.00", "iti electrician": "47-2111.00", "electrical wireman": "47-2111.00",
    "relationship manager": "41-4012.00", "bde": "41-4012.00", "medical representative (mr)": "41-4012.00",
}
META_VERSION_FIELDS = ("schema_version", "version")
META_BUILD_FIELDS = ("export_hash", "built_at")
DEFAULT_BASE_URL = "http://localhost:8001"
TIMEOUT_S = 10.0
LOW_CONFIDENCE = 0.85          # top match below this: ask "Did you mean ...?"
AMBIGUOUS_GAP = 0.05           # ... or the runner-up (another SOC) is this close
DEFAULT_K = 5


class M1Error(Exception):
    """Module 1 call failed. code: unreachable | timeout | not_found | bad_response."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.status = code, status


class OccupationMatch(BaseModel):
    soc_code: str
    title: str
    confidence: float = Field(ge=0.0, le=1.0)
    method: str | None = None
    matched_term: str | None = None


class Resolution(BaseModel):
    query: str
    matches: list[OccupationMatch]
    low_confidence: bool = Field(description="True when the API should ask 'Did you mean ...?'")
    did_you_mean: list[str] = Field(default_factory=list, description="Titles to offer when low_confidence")


def resolution(query: str, matches: list[OccupationMatch]) -> Resolution:
    """Flag low confidence: a weak top match, or two different occupations nearly tied."""
    matches = sorted(matches, key=lambda m: -m.confidence)
    low = not matches or matches[0].confidence < LOW_CONFIDENCE or (
        len(matches) > 1 and matches[0].confidence - matches[1].confidence < AMBIGUOUS_GAP
        and matches[0].soc_code[:7] != matches[1].soc_code[:7])   # 29-1141.00 vs 29-1141.01 is the same family
    return Resolution(query=query, matches=matches, low_confidence=low,
                      did_you_mean=[m.title for m in matches[:3]] if low else [])


def _safe(soc: str) -> str:
    return re.sub(r"[^0-9A-Za-z.\-]", "_", soc)


class M1Client:
    """Live module 1. Requirements, profiles and related lists are cached on disk per module 1 version, so a
    rebuilt module 1 (new version) is fetched again and an unchanged one costs no calls."""

    def __init__(self, base_url: str | None = None, *, client: httpx.Client | None = None,
                 cache_dir: Path | None = CACHE_DIR, timeout: float = TIMEOUT_S):
        self.base_url = (base_url or os.getenv("M1_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self.cache_dir = cache_dir
        self._version: str | None = None

    def _get(self, path: str, **params):
        try:
            r = self._client.get(self.base_url + path, params={k: v for k, v in params.items() if v is not None})
        except httpx.TimeoutException as e:
            raise M1Error("timeout", f"module 1 timed out on {path}") from e
        except httpx.HTTPError as e:
            raise M1Error("unreachable", f"module 1 unreachable at {self.base_url}: {type(e).__name__}") from e
        if r.status_code == 404:
            raise M1Error("not_found", f"module 1: {path} not found", 404)
        if r.status_code >= 400:
            raise M1Error("bad_response", f"module 1 returned {r.status_code} on {path}", r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise M1Error("bad_response", f"module 1 sent non-JSON on {path}", r.status_code) from e

    def version(self) -> str:
        """Cache key for module 1's data: GET /api/v1/meta (schema version + export hash or build time) when module 1
        has it, else the API version from /openapi.json, else 'unknown' (nothing cached)."""
        if self._version is None:
            self._version = self._meta_version() or self._openapi_version() or "unknown"
        return self._version

    def _meta_version(self) -> str | None:
        try:
            meta = self._get("/api/v1/meta")
        except M1Error:
            return None                  # not there yet (module 1 v2.1) or unreachable
        if not isinstance(meta, dict):
            return None
        meta = meta.get("db_meta", meta)
        version = next((str(meta[f]) for f in META_VERSION_FIELDS if meta.get(f)), None)
        build = next((str(meta[f]) for f in META_BUILD_FIELDS if meta.get(f)), None)
        return f"{version}+{build[:16]}" if version and build else version

    def _openapi_version(self) -> str | None:
        try:
            return str(self._get("/openapi.json").get("info", {}).get("version") or "") or None
        except M1Error:
            return None

    def _cached(self, kind: str, soc: str, path: str, **params):
        version = self.version()
        file = self.cache_dir / _safe(version) / f"{_safe(soc)}.{kind}.json" if self.cache_dir else None
        if file and version != "unknown" and file.exists():
            return json.loads(file.read_text(encoding="utf-8"))
        data = self._get(path, **params)
        if file and version != "unknown":
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(data), encoding="utf-8")
        return data

    def search(self, q: str, k: int = DEFAULT_K) -> Resolution:
        rows = self._get("/api/v1/occupations/search", q=q, k=k)
        if not isinstance(rows, list):
            raise M1Error("bad_response", "module 1 search did not return a list")
        return resolution(q, [OccupationMatch.model_validate(r) for r in rows])

    def requirements(self, soc: str) -> list[dict]:
        return self._cached("requirements", soc, f"/api/v1/occupations/{soc}/requirements")

    def profile(self, soc: str) -> dict:
        return self._cached("profile", soc, f"/api/v1/occupations/{soc}/profile")

    def related(self, soc: str, limit: int = 20) -> list[dict]:
        return self._cached(f"related{limit}", soc, f"/api/v1/occupations/{soc}/related", limit=limit)


class FixtureM1Client:
    """Same interface, backed by module 1 export files (tests and calibration; no network). Several files merge;
    each occupation keeps its file's metadata version (version_of)."""

    def __init__(self, *paths: Path):
        self.occupations: dict[str, dict] = {}
        self.versions: dict[str, str] = {}
        for path in paths or (FIXTURE_PATH,):
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            version = str(data.get("metadata", {}).get("version", "unknown"))
            for soc, occ in data["occupations"].items():
                self.occupations[soc] = occ
                self.versions[soc] = version

    def version(self) -> str:
        return "+".join(sorted(set(self.versions.values()))) or "unknown"

    def version_of(self, soc: str) -> str:
        return self.versions.get(soc, "unknown")

    def _occupation(self, soc: str) -> dict:
        if soc not in self.occupations:
            raise M1Error("not_found", f"module 1: {soc} not in the fixture", 404)
        return self.occupations[soc]

    def search(self, q: str, k: int = DEFAULT_K) -> Resolution:
        """Module 1's documented Indian aliases (exact, 1.0), else token overlap with fixture titles. Module 1's real
        search also uses 62k alternate titles."""
        alias = FIXTURE_ALIASES.get(q.strip().lower())
        if alias in self.occupations:
            return resolution(q, [OccupationMatch(soc_code=alias, title=self.occupations[alias]["title"], confidence=1.0,
                                                  method="india_alias_exact", matched_term=q.strip().lower())])
        words = set(re.findall(r"[a-z]+", q.lower()))
        matches = []
        for soc, occ in self.occupations.items():
            title_words = set(re.findall(r"[a-z]+", occ["title"].lower()))
            stems = {w.rstrip("s") for w in title_words}
            hit = len({w.rstrip("s") for w in words} & stems)
            if hit:                         # share of the query matched, averaged with share of the title matched
                conf = 0.5 * hit / max(len(words), 1) + 0.5 * hit / max(len(stems), 1)
                matches.append(OccupationMatch(soc_code=soc, title=occ["title"], method="token_match",
                                               confidence=round(min(1.0, conf), 2)))
        return resolution(q, sorted(matches, key=lambda m: -m.confidence)[:k])

    def requirements(self, soc: str) -> list[dict]:
        return self._occupation(soc)["requirements"]

    def profile(self, soc: str) -> dict:
        occ = self._occupation(soc)
        return {k: occ.get(k) for k in ("soc_code", "title", "description", "domain", "job_zone")}

    def related(self, soc: str, limit: int = 20) -> list[dict]:
        """The export has no related occupations: stand in with fixture occupations of the same SOC major group."""
        self._occupation(soc)
        same = [s for s in self.occupations if s != soc and s[:2] == soc[:2]]
        return [{"soc_code": soc, "related_soc_code": s, "related_title": self.occupations[s]["title"],
                 "relatedness_tier": "fixture_same_major_group", "index_val": i + 1} for i, s in enumerate(same[:limit])]
