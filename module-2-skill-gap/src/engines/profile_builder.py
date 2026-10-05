"""Parsed resume or typed input -> UserProfile with evidence-based skill levels.

Nothing is persisted: the resume text only lives for the call; the profile keeps a hash.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date

from src.engines.skill_extractor import extract_skills, resolve_skill
from src.models.schemas import (
    Evidence, ExtractedSkill, ManualProfileInput, ManualSkill, SkillHit, SourceInfo, UserProfile,
)
from src.parsers.resume_parser import ParsedDocument, parse_document
from src.parsers.section_segmenter import DATE_RANGE, extract_education, extract_work_history, segment

# Evidence -> (base level, confidence). Strongest evidence wins (context/AGENTS.md §14).
EVIDENCE_STRENGTH: dict[Evidence, tuple[int, float]] = {
    "work_supported": (3, 0.90),
    "project_supported": (2, 0.75),
    "resume_mentioned": (1, 0.50),
    "self_reported": (1, 0.40),
}
EVIDENCE_ORDER: list[Evidence] = ["work_supported", "project_supported", "resume_mentioned", "self_reported"]
SECTION_EVIDENCE: dict[str, Evidence] = {"experience": "work_supported", "projects": "project_supported"}
MULTI_EVIDENCE_BONUS = 1      # used in both work and projects
MAX_DERIVED_LEVEL = 4         # evidence alone never makes someone "expert" (5)
SELF_REPORTED_CAP = 2         # a typed level above this needs resume evidence

CITY_ALIASES = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "blr": "Bengaluru",
    "gurgaon": "Gurugram", "gurugram": "Gurugram", "bombay": "Mumbai", "mumbai": "Mumbai",
    "delhi": "Delhi", "new delhi": "Delhi", "delhi ncr": "Delhi NCR", "ncr": "Delhi NCR",
    "noida": "Noida", "hyderabad": "Hyderabad", "hyd": "Hyderabad", "secunderabad": "Hyderabad",
    "chennai": "Chennai", "madras": "Chennai", "kolkata": "Kolkata", "calcutta": "Kolkata",
    "pune": "Pune", "poona": "Pune", "kochi": "Kochi", "cochin": "Kochi", "mysore": "Mysuru",
    "mysuru": "Mysuru", "trivandrum": "Thiruvananthapuram", "vizag": "Visakhapatnam",
}


def normalise_location(name: str | None) -> str | None:
    """'bangalore, karnataka' -> 'Bengaluru'. Unknown cities are title-cased, not dropped."""
    if not name or not name.strip():
        return None
    first = name.split(",")[0].strip()
    return CITY_ALIASES.get(re.sub(r"\s+", " ", first.lower()), first.title())


def _level(evidence: list[Evidence]) -> tuple[int, float]:
    strongest = min(evidence, key=EVIDENCE_ORDER.index)
    level, confidence = EVIDENCE_STRENGTH[strongest]
    if "work_supported" in evidence and "project_supported" in evidence:
        level += MULTI_EVIDENCE_BONUS
    return min(level, MAX_DERIVED_LEVEL), confidence


def _skill(hit: SkillHit, evidence: list[Evidence], claimed: int | None = None) -> ExtractedSkill:
    evidence = sorted(set(evidence), key=EVIDENCE_ORDER.index)
    level, confidence = _level(evidence)
    needs_verification = False
    if claimed is not None:
        supported = any(e in ("work_supported", "project_supported") for e in evidence)
        if claimed > level and not supported:
            needs_verification = claimed > SELF_REPORTED_CAP
            level = max(level, min(claimed, SELF_REPORTED_CAP))
        elif supported:
            level = max(level, min(claimed, level + 1))
    entry = resolve_skill(hit.id) if hit.in_taxonomy else None
    return ExtractedSkill(
        name=hit.id, display=hit.display, category=hit.category, in_taxonomy=hit.in_taxonomy,
        maps_to=entry.get("maps_to") if entry else None, level=level, confidence=confidence,
        evidence=evidence, claimed_level=claimed, needs_verification=needs_verification)


def _sorted(skills: list[ExtractedSkill]) -> list[ExtractedSkill]:
    return sorted(skills, key=lambda s: (-s.level, -s.confidence, s.display.casefold()))


def build_profile(doc: ParsedDocument, *, use_llm: bool = True, location: str | None = None,
                  target_occupation: str | None = None, today: date | None = None) -> UserProfile:
    sections = segment(doc.text)
    warnings = list(doc.warnings)
    for needed in ("experience", "skills"):
        if needed not in sections:
            warnings.append(f"No {needed.title()} section found.")

    work_history, experience_years, internship_years = extract_work_history(sections.get("experience", ""), today)
    if "experience" in sections and not work_history:
        warnings.append("Experience section found but no date ranges; experience_years is 0.")

    found: dict[str, tuple[SkillHit, list[Evidence]]] = {}
    for name, body in sections.items():
        if name == "experience":  # a job-title line ("Data Science Intern | ...") is not skill evidence
            body = "\n".join(l for l in body.splitlines() if not DATE_RANGE.search(l))
        evidence = SECTION_EVIDENCE.get(name, "resume_mentioned")
        for hit in extract_skills(body, use_llm=False, skills_context=(name == "skills")):
            found.setdefault(hit.id, (hit, []))[1].append(evidence)
    if use_llm:
        for hit in extract_skills(doc.text, use_llm=True):
            if hit.source != "llm" or hit.id in found:
                continue
            surface = hit.matches[0].lower()
            where = [n for n, body in sections.items() if surface in body.lower()] or ["other"]
            found[hit.id] = (hit, [SECTION_EVIDENCE.get(n, "resume_mentioned") for n in where])

    education_details = extract_education(sections.get("education", ""))
    return UserProfile(
        skills=_sorted([_skill(h, ev) for h, ev in found.values()]),
        experience_years=experience_years,
        internship_years=internship_years,
        education=[" ".join(x for x in (e.degree, e.field) if x) for e in education_details],
        education_details=education_details,
        work_history=work_history,
        location=normalise_location(location),
        target_occupation=target_occupation,
        source=SourceInfo(format=doc.format, pages=doc.pages, ocr_used=bool(doc.ocr_pages), ocr_pages=doc.ocr_pages,
                          ocr_seconds=doc.ocr_seconds, ocr_repairs=doc.ocr_repairs,
                          text_sha1=hashlib.sha1(doc.text.encode("utf-8")).hexdigest(),
                          sections_found=[s for s in sections if s != "header"]),
        warnings=warnings,
    )


def analyze_resume(data: bytes, filename: str | None = None, *, use_llm: bool = True,
                   location: str | None = None, target_occupation: str | None = None) -> UserProfile:
    """Bytes of a PDF / DOCX / text resume -> UserProfile. Raises ResumeParseError on bad files."""
    return build_profile(parse_document(data, filename), use_llm=use_llm, location=location,
                         target_occupation=target_occupation)


def profile_from_manual(data: ManualProfileInput) -> UserProfile:
    """Typed skills -> UserProfile. Every skill is self_reported; unknown skills are kept and flagged."""
    skills: dict[str, ExtractedSkill] = {}
    unknown = []
    for item in data.skills:
        raw = item if isinstance(item, ManualSkill) else ManualSkill(name=str(item))
        name = raw.name.strip()
        if not name:
            continue
        entry = resolve_skill(name)
        hits = ([SkillHit(id=entry["id"], display=entry["display"], category=entry["category"],
                          in_taxonomy=True, source="dictionary", matches=[name])] if entry
                else extract_skills(name, use_llm=False, skills_context=True))
        if not hits:
            unknown.append(name)
            hits = [SkillHit(id=re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "unknown",
                             display=name, category=None, in_taxonomy=False, source="dictionary", matches=[name])]
        for hit in hits:
            skills[hit.id] = _skill(hit, ["self_reported"], claimed=raw.level)
    warnings = [f"Not in the skill taxonomy (kept as typed): {', '.join(unknown)}"] if unknown else []
    if not skills:
        warnings.append("No skills entered.")
    return UserProfile(
        skills=_sorted(list(skills.values())),
        experience_years=data.experience_years,
        education=data.education,
        location=normalise_location(data.location),
        preferred_locations=[l for l in (normalise_location(p) for p in data.preferred_locations) if l],
        target_occupation=data.target_occupation,
        source=SourceInfo(format="manual"),
        warnings=warnings,
    )
