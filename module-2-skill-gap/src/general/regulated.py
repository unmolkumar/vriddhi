"""Occupations regulated in India (data/general/regulated_occupations.json): a regulated occupation is suggested
(close alternative, more senior fit) only to someone whose evidence shows its qualification or a past title in it."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "data" / "general" / "regulated_occupations.json"


@dataclass(frozen=True)
class Regulated:
    name: str
    soc_prefixes: tuple[str, ...]
    qualifications: tuple[re.Pattern, ...]


@lru_cache(maxsize=1)
def table() -> list[Regulated]:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    return [Regulated(o["name"], tuple(o["soc_prefixes"]), tuple(re.compile(q, re.IGNORECASE) for q in o["qualifications"]))
            for o in data["occupations"]]


def regulation(soc: str) -> Regulated | None:
    return next((r for r in table() if any(soc.startswith(p) for p in r.soc_prefixes)), None)


def blocked(soc: str, evidence_texts: list[str], past_socs: set[str]) -> str | None:
    """None when the user may be offered `soc`; else the reason it is left out."""
    reg = regulation(soc)
    if reg is None:
        return None
    if any(s.startswith(reg.soc_prefixes) for s in past_socs):
        return None
    if any(q.search(t) for q in reg.qualifications for t in evidence_texts):
        return None
    return f"{reg.name}: regulated in India; no qualification or past role in it shown"
