"""Shared helpers for turning provider records into module 3's Job shape."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

TIMEOUT_S = 5.0                     # per provider call
SHORT_DESCRIPTION_CHARS = 200       # below this a description is "short"
SALARY_FLOOR_INR = 50_000           # yearly salaries outside this band are treated as noise
SALARY_CEILING_INR = 20_000_000


class ProviderError(Exception):
    """A provider call that didn't produce jobs. status drives the fallback chain."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}: {detail}")
        self.status = status        # not_configured | not_subscribed | timeout | error
        self.detail = detail


_EXP_RANGE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:-|–|to)\s*(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_MIN = re.compile(r"(?:minimum\s+(?:of\s+)?)?(\d{1,2}(?:\.\d)?)\s*\+\s*(?:years?|yrs?)|"
                      r"(?:minimum|min\.?|at least)\s+(?:of\s+)?(\d{1,2}(?:\.\d)?)\s*(?:years?|yrs?)", re.I)
_EXP_PLAIN = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:years?|yrs?)(?:\s+of)?\s+(?:relevant\s+|professional\s+|work\s+)?experience", re.I)


def parse_experience(text: str) -> tuple[float | None, float | None]:
    """'3-5 years' -> (3, 5); '4+ years' / 'at least 4 years' -> (4, None); '2 years of experience' -> (2, None)."""
    if not text:
        return None, None
    m = _EXP_RANGE.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= hi <= 40:
            return lo, hi
    m = _EXP_MIN.search(text) or _EXP_PLAIN.search(text)
    if m:
        value = float(next(g for g in m.groups() if g))
        if value <= 40:
            return value, None
    return None, None


def work_mode(text: str, is_remote: bool | None = None) -> str:
    if is_remote:
        return "remote"
    lowered = (text or "").lower()
    if "hybrid" in lowered:
        return "hybrid"
    if re.search(r"\b(remote|work from home|wfh)\b", lowered):
        return "remote"
    if re.search(r"\b(on-?site|in-?office|work from office)\b", lowered):
        return "onsite"
    return "unknown"


def description_quality(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return "missing"
    return "short" if len(stripped) < SHORT_DESCRIPTION_CHARS else "ok"


def plausible_salary(value) -> int | None:
    """Yearly INR as int, or None when missing or outside a sane band."""
    try:
        amount = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return amount if SALARY_FLOOR_INR <= amount <= SALARY_CEILING_INR else None


def parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def employment_from_text(title: str) -> str | None:
    return "internship" if re.search(r"\bintern(ship)?\b", title or "", re.I) else None


# Legal and generic suffixes that vary between listings of the same employer ("Honeywell" vs "Honeywell Technologies").
_COMPANY_NOISE = re.compile(r"\b(pvt|private|ltd|limited|inc|llp|llc|corp|corporation|co|india|technologies|technology|"
                            r"solutions|services)\b\.?", re.I)


def dedupe_key(title: str, company: str | None, city: str | None) -> str:
    """Same job across providers: normalised title | company | city."""
    def norm(s: str | None) -> str:
        s = _COMPANY_NOISE.sub(" ", (s or "").lower())
        return re.sub(r"[^a-z0-9]+", " ", s).strip()
    return hashlib.sha1(f"{norm(title)}|{norm(company)}|{norm(city)}".encode("utf-8")).hexdigest()
