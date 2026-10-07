"""A person's evidence as small units (bullets, sentences, skills-list items), each with where it came from.

Sources: resume text or free text (sectioned with the v1 section_segmenter), a parsed resume file, an existing
v1 UserProfile, or a typed skill list. Each unit keeps its original text span for explanations, and the taxonomy
skill ids the v1 dictionary finds in it (for the matcher's high-precision alias layer).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from src.engines.skill_extractor import extract_skills, resolve_skill
from src.models.schemas import UserProfile
from src.general.shorthand import expand, is_very_short
from src.parsers.section_segmenter import _DEGREE, _INSTITUTION, _YEAR, DATE_RANGE, extract_work_history, segment

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
HEADER_TITLE_LINES = 3         # header lines that may carry the current title ('Staff Nurse | Kochi')
MAX_TITLE_WORDS = 6

# Language check (A3). Below ENGLISH_MIN_SHARE a sentence is rewritten in English before matching (translate.py).
ENGLISH_MIN_SHARE = 0.75
_WORD = re.compile(r"[^\W\d_]+")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Frequent romanised Hindi function words and verbs; English look-alikes (main, the, to, do, me, par) are left out.
ROMAN_HINDI_MARKERS = frozenset("""
hai hain ho hoon hun hu tha thi ka ki ke ko se mein mai pe aur ya bhi nahi nahin kya kaise kab jab tab toh ek teen
saal mahine mahina din kaam karta karti karte karna kiya kiye liya diya deta leta raha rahi rahe wala wali wale waala
apna apni apne mera meri mere hum aap unka unki uska iska sab sabhi kuch bahut achha accha achhe tak pehle baad saath
liye gaya gayi aata aati aate chala chalaya chalata chalate banana banaya banata dena lena rakhna rakhta rakhti
baandhta dekh dekhna dekhta dhoondh dhoondhta theek yeh woh ye wo hota hoti hote sakta sakti chahiye mujhe maine
humne kar diye gaadi maal rassi kaise koi bhai ji sahi galat jaise wahan yahan abhi kabhi phir isliye lekin magar
""".split())
Translator = Callable[[list[str]], list[str | None]]
Normaliser = Callable[[list[str], str], list[str | None]]     # (very short units, whole text) -> plain phrases


class EvidenceUnit(BaseModel):
    text: str
    evidence_type: EvidenceType
    section: str = Field(description="Resume section ('experience', 'skills', ...), 'free_text', 'profile' or 'typed'")
    span: tuple[int, int] | None = Field(default=None, description="Character offsets in the source text")
    context_span: tuple[int, int] | None = Field(
        default=None, description="For a clause split out of a list-like sentence: the whole sentence's span")
    skill_ids: list[str] = Field(default_factory=list, description="Taxonomy skills the v1 dictionary finds here")
    role_title: bool = Field(default=False, description="A job-title line: role history, never matched as a task")
    education: bool = Field(default=False, description="A degree/institution line: qualifications and knowledge "
                                                        "inference only, never task/DWA/tool evidence")
    translated: bool = Field(default=False, description="text is an English rewrite of original_text")
    original_text: str | None = Field(default=None, description="The text as written, when translated or rewritten")
    rewrites: list[str] = Field(default_factory=list, description="Shorthand expanded or phrases normalised in text "
                                                                 "('BP -> blood pressure')")

    @property
    def matchable(self) -> bool:
        return not self.role_title and not self.education


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


def english_share(text: str) -> float:
    """Share of words that aren't Devanagari or common romanised Hindi (ROMAN_HINDI_MARKERS). A cheap check, not a
    dictionary: English words outside any list count as English."""
    words = _WORD.findall(text)
    if not words:
        return 1.0
    foreign = sum(1 for w in words if _DEVANAGARI.search(w) or w.lower() in ROMAN_HINDI_MARKERS)
    return 1.0 - foreign / len(words)


def is_english(text: str) -> bool:
    return english_share(text) >= ENGLISH_MIN_SHARE


EDUCATION_MAX_WORDS = 8          # a short line naming a degree is a qualification, even without a year or institution
_EDUCATION_LEAD = re.compile(r"^\s*(education|qualifications?|academics?)\s*[:\-]", re.IGNORECASE)


def is_education_line(section: str, piece: str) -> bool:
    """A degree / institution line: the Education section, or a degree name with an institution, a year, or few
    words ('B.Sc Nursing, Government College of Nursing, 2019', 'Diploma in Hotel Management')."""
    if section == "education" or _EDUCATION_LEAD.match(piece):
        return True
    return bool(_DEGREE.search(piece)) and bool(_INSTITUTION.search(piece) or _YEAR.search(piece)
                                                 or len(piece.split()) <= EDUCATION_MAX_WORDS)


def _is_title_line(section: str, piece: str, multi_section: bool) -> bool:
    """A job-title line: an experience line with a date range, or a short line (name, title, city) in the header of
    a sectioned resume. Longer header lines stay evidence: an unrecognised heading puts real bullets there."""
    if section == "experience":
        return bool(DATE_RANGE.search(piece))
    return section == "header" and multi_section and len(piece.split()) <= MAX_TITLE_WORDS


def _skill_ids(texts: list[str], skills_context: bool = False) -> list[str]:
    return list(dict.fromkeys(h.id for t in texts for h in extract_skills(t, use_llm=False, skills_context=skills_context)))


def from_text(text: str, *, default_section: str = "free_text", translator: Translator | None = None,
              warnings: list[str] | None = None, normaliser: Normaliser | None = None) -> list[EvidenceUnit]:
    """Resume-style or free text -> units. Text with no recognised headings is one 'free_text' section.

    - A list-like sentence (comma / semicolon / 'and' parts) yields the sentence and one unit per part; each part
      keeps its own span and the sentence's span as context_span.
    - Job-title lines are kept as role_title units: role history, never matched as tasks (see role_history()).
    - Sentences that look non-English (is_english) are rewritten in English by `translator` when given, in one
      batch; the unit keeps the original text and span and is marked translated. Without a translator (or when it
      fails) they are matched as written and a warning is added.
    - Shorthand (shorthand.expand) is expanded in a matching copy; the unit keeps the text as written in
      original_text and lists the rewrites. With a `normaliser`, very short units are then rewritten as plain phrases.
    """
    sections = segment(text or "")
    multi = len(sections) > 1
    rows = []
    for section, body in sections.items():
        name = default_section if section == "header" and not multi else section
        for piece, parts in _units_of(name, body):
            if len(piece) >= MIN_UNIT_CHARS and not _CONTACT.search(piece):
                rows.append((name, piece, parts, _is_title_line(name, piece, multi)))

    foreign = [i for i, (_, piece, _, title) in enumerate(rows) if not title and not is_english(piece)]
    english: dict[int, str] = {}
    if foreign and translator is not None:
        for i, t in zip(foreign, translator([rows[i][1] for i in foreign])):
            if t and t.strip():
                english[i] = t.strip()
    if warnings is not None and len(foreign) > len(english):
        warnings.append(f"{len(foreign) - len(english)} sentence(s) look non-English and were matched as written "
                        "(no translation available); results for them may be weaker.")

    units, cursor = [], 0
    for i, (name, piece, parts, title) in enumerate(rows):
        span = _find(text, piece, cursor)
        if span:
            cursor = span[0]
        kind = SECTION_TYPE.get(name, "mentioned")
        if i in english:
            eng, rewrites = expand(english[i], text)
            units.append(EvidenceUnit(text=eng, evidence_type=kind, section=name, span=span, translated=True,
                                      original_text=piece, rewrites=rewrites, skill_ids=_skill_ids([eng])))
            units += [EvidenceUnit(text=part, evidence_type=kind, section=name, span=span, context_span=span,
                                   translated=True, original_text=piece, rewrites=rewrites,
                                   skill_ids=_skill_ids([part], True)) for part in clauses(eng)]
            continue
        education = not title and is_education_line(name, piece)
        if title or education:
            units.append(EvidenceUnit(text=piece, evidence_type=kind, section=name, span=span, role_title=title,
                                      education=education))
            continue
        # Shorthand is expanded per unit (expansions contain no clause separators, so clauses keep their spans).
        expanded, rewrites = expand(piece, text)
        units.append(EvidenceUnit(text=expanded, evidence_type=kind, section=name, span=span,
                                  original_text=piece if rewrites else None, rewrites=rewrites,
                                  skill_ids=_skill_ids(list(dict.fromkeys([piece, expanded])), name == "skills")))
        part_cursor = span[0] if span else 0
        for part in parts:
            part_span = _find(text, part, part_cursor)
            if part_span:
                part_cursor = part_span[1]
            p_expanded, p_rewrites = expand(part, text)
            units.append(EvidenceUnit(
                text=p_expanded, evidence_type=kind, section=name, span=part_span, context_span=span,
                original_text=part if p_rewrites else None, rewrites=p_rewrites,
                skill_ids=_skill_ids(list(dict.fromkeys([part, p_expanded])), True)))
    return normalise_short(units, text, normaliser) if normaliser else units


def normalise_short(units: list[EvidenceUnit], text: str, normaliser: Normaliser) -> list[EvidenceUnit]:
    """Very short matchable units (shorthand.is_very_short) rewritten as plain phrases by `normaliser` (fail-safe:
    None keeps the unit)."""
    idx = [i for i, u in enumerate(units) if u.matchable and is_very_short(u.text)]
    if not idx:
        return units
    phrases = normaliser([units[i].text for i in idx], text)
    out = list(units)
    for i, phrase in zip(idx, phrases):
        u = units[i]
        if phrase and phrase.strip() and phrase.strip().lower() != u.text.lower():
            out[i] = u.model_copy(update={"text": phrase.strip(), "original_text": u.original_text or u.text,
                                          "rewrites": u.rewrites + [f"{u.text} -> {phrase.strip()}"]})
    return out


def _months(ym: str | None, today: date) -> int | None:
    if not ym:
        return today.year * 12 + today.month
    try:
        y, m = ym.split("-")
        return int(y) * 12 + int(m)
    except ValueError:
        return None


def role_history(text: str, today: date | None = None) -> list[tuple[str, float | None]]:
    """(job title, years) from the experience section's dated lines (years from the date range), plus the title on
    a sectioned resume's header lines (years unknown). Titles are cut at '|' or ',' ('Staff Nurse, ICU | Aster')."""
    today = today or date.today()
    sections = segment(text or "")
    out = []
    entries, _, _ = extract_work_history(sections.get("experience", ""), today)
    for e in entries:
        if e.title:
            start, end = _months(e.start, today), _months(None if e.current else e.end, today)
            years = _years(start, end)
            out.append((_short_title(e.title), years))
    if len(sections) > 1:
        for line in sections.get("header", "").splitlines()[:HEADER_TITLE_LINES]:
            t = _short_title(line)
            if t and not _CONTACT.search(line) and len(t.split()) <= MAX_TITLE_WORDS:
                out.append((t, None))
    return out


def _years(start: int | None, end: int | None) -> float | None:
    """Inclusive month span in years, to the nearest half year (as v1's work history)."""
    if start is None or end is None:
        return None
    return round(max(0, end - start + 1) / 12 * 2) / 2


def _short_title(line: str) -> str:
    return re.split(r"\s*[|,(]\s*|\s+-\s+", line.strip())[0].strip()


def profile_history(profile: UserProfile, today: date | None = None) -> list[tuple[str, float | None]]:
    """(title, years) from a v1 profile's work history."""
    today = today or date.today()
    out = []
    for w in profile.work_history:
        if w.title:
            start, end = _months(w.start, today), _months(None if w.current else w.end, today)
            years = _years(start, end)
            out.append((_short_title(w.title), years))
    return out


def from_resume(data: bytes, filename: str | None = None) -> list[EvidenceUnit]:
    """A PDF / DOCX / text resume -> units (v1 parser; raises ResumeParseError on bad files)."""
    from src.parsers.resume_parser import parse_document

    return from_text(parse_document(data, filename).text)


def from_skills(skills: list[str]) -> list[EvidenceUnit]:
    """Typed skills -> self-reported units (shorthand expanded, as in from_text)."""
    units, context = [], " ".join(skills)
    for s in skills:
        s = s.strip()
        if len(s) < 2:
            continue
        entry = resolve_skill(s)
        ids = [entry["id"]] if entry else [h.id for h in extract_skills(s, use_llm=False, skills_context=True)]
        expanded, rewrites = expand(s, context)
        units.append(EvidenceUnit(text=expanded, evidence_type="self", section="typed", skill_ids=ids,
                                  original_text=s if rewrites else None, rewrites=rewrites))
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
                                      evidence_type="work", section="profile", role_title=True))
    return units
