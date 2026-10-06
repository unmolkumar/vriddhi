"""A person's evidence as small units (bullets, sentences, skills-list items), each with where it came from.

Sources: resume text or free text (sectioned with the v1 section_segmenter), a parsed resume file, an existing
v1 UserProfile, or a typed skill list. Each unit keeps its original text span for explanations, and the taxonomy
skill ids the v1 dictionary finds in it (for the matcher's high-precision alias layer).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from src.engines.skill_extractor import extract_skills, resolve_skill
from src.models.schemas import UserProfile
from src.parsers.section_segmenter import segment

EvidenceType = Literal["work", "project", "mentioned", "self"]
SECTION_TYPE: dict[str, EvidenceType] = {"experience": "work", "projects": "project"}   # everything else: mentioned
PROFILE_TYPE: dict[str, EvidenceType] = {"work_supported": "work", "project_supported": "project",
                                         "resume_mentioned": "mentioned", "self_reported": "self"}
MIN_UNIT_CHARS = 3
_CONTACT = re.compile(r"@|\+?\d[\d\s-]{8,}\d|linkedin\.com|github\.com", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-•*▪●◦·>]+\s*")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9])")
_LIST_ITEM = re.compile(r"\s*[,|;•·▪●/]\s*|\s+and\s+|\n", re.IGNORECASE)
_CLAUSE = re.compile(r"\s*[,;]\s*|\s+and\s+", re.IGNORECASE)
MIN_CLAUSES = 2                # a sentence with this many list parts also yields one unit per part


class EvidenceUnit(BaseModel):
    text: str
    evidence_type: EvidenceType
    section: str = Field(description="Resume section ('experience', 'skills', ...), 'free_text', 'profile' or 'typed'")
    span: tuple[int, int] | None = Field(default=None, description="Character offsets in the source text")
    context_span: tuple[int, int] | None = Field(
        default=None, description="For a clause split out of a list-like sentence: the whole sentence's span")
    skill_ids: list[str] = Field(default_factory=list, description="Taxonomy skills the v1 dictionary finds here")


def clauses(sentence: str) -> list[str]:
    """The parts of a list-like sentence ('12 yrs, CBSE, classes 8-10 physics and chemistry'), or [] when it has
    fewer than MIN_CLAUSES usable parts."""
    parts = [p.strip(" .") for p in _CLAUSE.split(sentence)]
    parts = [p for p in parts if len(p) >= MIN_UNIT_CHARS]
    return parts if len(parts) >= MIN_CLAUSES else []


def _units_of(section: str, body: str) -> list[tuple[str, list[str]]]:
    """(piece, its clauses) per sentence; skills-section items are pieces with no clauses."""
    if section == "skills":
        out = []
        for line in body.splitlines():
            line = re.sub(r"^[^:]{1,30}:\s*", "", _BULLET.sub("", line))      # "Software: Tally, Excel" -> items
            out += [(p.strip(), []) for p in _LIST_ITEM.split(line) if p.strip()]
        return out
    out = []
    for line in body.splitlines():
        line = _BULLET.sub("", line).strip()
        if line:
            out += [(s.strip(), clauses(s.strip())) for s in _SENTENCE.split(line) if s.strip()]
    return out


def _find(text: str, piece: str, cursor: int) -> tuple[int, int] | None:
    start = text.find(piece, cursor)
    start = text.find(piece) if start < 0 else start
    return (start, start + len(piece)) if start >= 0 else None


def from_text(text: str, *, default_section: str = "free_text") -> list[EvidenceUnit]:
    """Resume-style or free text -> units. Text with no recognised headings is one 'free_text' section.

    A list-like sentence (comma / semicolon / 'and' parts) yields the sentence and one unit per part; each part
    keeps its own span and the sentence's span as context_span.
    """
    sections = segment(text or "")
    units, cursor = [], 0
    for section, body in sections.items():
        name = default_section if section == "header" and len(sections) == 1 else section
        is_skills = name == "skills"
        kind = SECTION_TYPE.get(name, "mentioned")
        for piece, parts in _units_of(name, body):
            if len(piece) < MIN_UNIT_CHARS or _CONTACT.search(piece):
                continue
            span = _find(text, piece, cursor)
            if span:
                cursor = span[0]
            hits = extract_skills(piece, use_llm=False, skills_context=is_skills)
            units.append(EvidenceUnit(text=piece, evidence_type=kind, section=name, span=span,
                                      skill_ids=[h.id for h in hits]))
            part_cursor = span[0] if span else 0
            for part in parts:
                part_span = _find(text, part, part_cursor)
                if part_span:
                    part_cursor = part_span[1]
                units.append(EvidenceUnit(
                    text=part, evidence_type=kind, section=name, span=part_span, context_span=span,
                    skill_ids=[h.id for h in extract_skills(part, use_llm=False, skills_context=True)]))
    return units


def from_resume(data: bytes, filename: str | None = None) -> list[EvidenceUnit]:
    """A PDF / DOCX / text resume -> units (v1 parser; raises ResumeParseError on bad files)."""
    from src.parsers.resume_parser import parse_document

    return from_text(parse_document(data, filename).text)


def from_skills(skills: list[str]) -> list[EvidenceUnit]:
    """Typed skills -> self-reported units."""
    units = []
    for s in skills:
        s = s.strip()
        if len(s) < 2:
            continue
        entry = resolve_skill(s)
        ids = [entry["id"]] if entry else [h.id for h in extract_skills(s, use_llm=False, skills_context=True)]
        units.append(EvidenceUnit(text=s, evidence_type="self", section="typed", skill_ids=ids))
    return units


def from_profile(profile: UserProfile) -> list[EvidenceUnit]:
    """A v1 UserProfile -> units: one per skill (strongest evidence) and one per job title."""
    units = []
    for s in profile.skills:
        kind = next((PROFILE_TYPE[e] for e in ("work_supported", "project_supported", "resume_mentioned",
                                                "self_reported") if e in s.evidence), "mentioned")
        units.append(EvidenceUnit(text=s.display, evidence_type=kind, section="profile",
                                  skill_ids=[s.name] + ([s.maps_to] if s.maps_to else [])))
    for w in profile.work_history:
        if w.title:
            units.append(EvidenceUnit(text=w.title + (f" at {w.company}" if w.company else ""),
                                      evidence_type="work", section="profile"))
    return units
