"""Split resume text into sections, and pull work history and education out of them."""
from __future__ import annotations

import re
from datetime import date

from src.models.schemas import EducationEntry, WorkEntry

HEADINGS = {
    "summary": ["summary", "profile", "professional summary", "objective", "career objective", "about me", "about"],
    "experience": ["experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "career history", "internships", "internship", "internship experience",
                   "experience & internships", "relevant experience"],
    "projects": ["projects", "academic projects", "personal projects", "key projects", "project experience",
                 "project work", "major projects", "side projects"],
    "skills": ["skills", "technical skills", "key skills", "core skills", "skills & tools", "technologies",
               "tech stack", "tools", "tools & technologies", "core competencies", "competencies", "skill set",
               "technical proficiency", "languages", "programming languages"],
    "education": ["education", "academic background", "academics", "educational qualifications", "qualifications",
                  "academic qualifications", "education & certifications"],
    "other": ["certifications", "certificates", "achievements", "awards", "publications", "extracurricular activities",
              "extracurriculars", "hobbies", "interests", "positions of responsibility", "volunteering", "references"],
}
_HEADING_LOOKUP = {h: section for section, names in HEADINGS.items() for h in names}
MAX_HEADING_CHARS = 40


def _heading(line: str) -> str | None:
    s = re.sub(r"[^a-z& ]", " ", line.lower())
    s = re.sub(r"\s+", " ", s).strip()
    if not s or len(s) > MAX_HEADING_CHARS:
        return None
    return _HEADING_LOOKUP.get(s)


def segment(text: str) -> dict[str, str]:
    """{'header', 'summary', 'experience', 'projects', 'skills', 'education', 'other'} -> text.

    A heading must be alone on its line ('Skills: Python' is content, not a heading).
    Lines before the first heading go to 'header'. Missing sections are absent.
    """
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        found = _heading(line)
        if found:
            current = found
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


# --- work history -----------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?"


def _date_rx(n: int) -> str:
    return (rf"(?:(?P<mon{n}>{_MON})\s*['’]?\s*(?P<my{n}>\d{{4}}|\d{{2}})"
            rf"|(?P<num{n}>\d{{1,2}})\s*[/.\-]\s*(?P<ny{n}>\d{{4}})"
            rf"|(?P<y{n}>(?:19|20)\d{{2}}))")


_PRESENT = r"(?P<present>present|current(?:ly)?|now|till\s+date|to\s+date|ongoing|date)"
DATE_RANGE = re.compile(
    # OCR often drops the dash, so plain whitespace also separates the two dates.
    rf"(?<![\w/]){_date_rx(1)}(?:\s*(?:-|–|—|to|till|until)\s*|\s+)(?:{_date_rx(2)}|{_PRESENT})(?![\w/])",
    re.IGNORECASE)
_TITLE_WORDS = re.compile(
    r"\b(engineer|developer|analyst|scientist|intern|internship|manager|lead|consultant|architect|designer|"
    r"administrator|associate|specialist|trainee|programmer|tester|sde|devops|head|director|officer|"
    r"executive|researcher|fellow|apprentice)\b", re.IGNORECASE)
_HEADER_SPLIT = re.compile(r"\s+[|–—\-@]\s+|\s*[|,;]\s*|\s+at\s+", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-•*▪●◦]")
MAX_HEADER_WORDS = 6  # a longer line above a job title is a bullet, not a company name


def _month_index(m: re.Match, n: int) -> int | None:
    if m.group(f"mon{n}"):
        month = _MONTHS[m.group(f"mon{n}")[:3].lower()]
        year = int(m.group(f"my{n}"))
        year += 2000 if year < 100 else 0
    elif m.group(f"num{n}"):
        month, year = int(m.group(f"num{n}")), int(m.group(f"ny{n}"))
        if not 1 <= month <= 12:
            return None
    elif m.group(f"y{n}"):
        month, year = 1, int(m.group(f"y{n}"))  # year-only dates count from January
    else:
        return None
    return year * 12 + (month - 1)


def _span(m: re.Match, today: date) -> tuple[int, int] | None:
    start = _month_index(m, 1)
    now = today.year * 12 + today.month - 1
    end = now if m.group("present") else _month_index(m, 2)
    if start is None or end is None or end < start or start > now or end - start > 50 * 12:
        return None
    return start, min(end, now)


def _ym(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _parts(s: str) -> list[str]:
    return [p.strip(" -–—|,()@\t") for p in _HEADER_SPLIT.split(s) if p.strip(" -–—|,()@\t")]


def _title_company(header: str, previous: str) -> tuple[str | None, str | None]:
    parts = _parts(header)
    if len(parts) == 1:  # no separators (common after OCR): "Data Analyst Swiggy"
        last = list(_TITLE_WORDS.finditer(parts[0]))
        rest = parts[0][last[-1].end():].strip(" ;,") if last else ""
        if rest:
            parts = [parts[0][:last[-1].end()].strip(), rest]
    # "Swiggy, Bengaluru" above "Data Analyst  Jun 2021 - Present": company on the line above.
    if (len(parts) < 2 and previous and not _BULLET.match(previous) and len(previous.split()) <= MAX_HEADER_WORDS
            and not DATE_RANGE.search(previous)):
        parts += _parts(previous)
    title = next((p for p in parts if _TITLE_WORDS.search(p)), parts[0] if parts else None)
    company = next((p for p in parts if p != title), None)
    return title, company


def merged_years(spans: list[tuple[int, int]]) -> float:
    """Total length of the union of inclusive month spans, in years rounded to 0.5."""
    months, current = 0, None
    for start, end in sorted(spans):
        if current and start <= current[1] + 1:
            current[1] = max(current[1], end)
            continue
        if current:
            months += current[1] - current[0] + 1
        current = [start, end]
    if current:
        months += current[1] - current[0] + 1
    return round(months / 12 * 2) / 2


def extract_work_history(experience_text: str, today: date | None = None) -> tuple[list[WorkEntry], float, float]:
    """(entries, experience_years, internship_years) from an Experience section.

    Overlapping jobs are counted once. Internships are kept out of experience_years.
    """
    today = today or date.today()
    lines = experience_text.splitlines()
    entries, work, intern = [], [], []
    for i, line in enumerate(lines):
        for m in DATE_RANGE.finditer(line):
            span = _span(m, today)
            if span is None:
                continue
            previous = next((l.strip() for l in reversed(lines[:i]) if l.strip()), "")
            title, company = _title_company(line[:m.start()] + " " + line[m.end():], previous)
            is_intern = bool(re.search(r"\bintern", f"{title or ''} {line}", re.IGNORECASE))
            (intern if is_intern else work).append(span)
            entries.append(WorkEntry(
                title=title, company=company, start=_ym(span[0]),
                end=None if m.group("present") else _ym(span[1]),
                current=bool(m.group("present")), internship=is_intern))
    return entries, merged_years(work), merged_years(intern)


# --- education --------------------------------------------------------------------

_DEGREE = re.compile(
    r"(?<![A-Za-z])(B\.?\s?Tech|M\.?\s?Tech|B\.?\s?E\.?|M\.?\s?E\.?|B\.?\s?Sc|M\.?\s?Sc|B\.?\s?C\.?\s?A|M\.?\s?C\.?\s?A"
    r"|BBA|MBA|B\.?\s?Com|M\.?\s?Com|Ph\.?\s?D|Diploma|PGDM|"
    r"Bachelor(?:'s)?(?: of [A-Z][a-z]+(?: [A-Z][a-z]+)?)?|Master(?:'s)?(?: of [A-Z][a-z]+(?: [A-Z][a-z]+)?)?)"
    r"(?![A-Za-z])")
_DEGREE_NAMES = {"btech": "B.Tech", "mtech": "M.Tech", "be": "B.E.", "me": "M.E.", "bsc": "B.Sc", "msc": "M.Sc",
                 "bca": "BCA", "mca": "MCA", "bba": "BBA", "mba": "MBA", "bcom": "B.Com", "mcom": "M.Com",
                 "phd": "PhD", "diploma": "Diploma", "pgdm": "PGDM"}
_INSTITUTION = re.compile(r"\b(Institute|University|College|IIT|NIT|IIIT|BITS|School|Academy|Polytechnic|Vidyapeeth)\b")
_YEAR = re.compile(r"\b(19[89]\d|20\d{2})\b")


def extract_education(education_text: str) -> list[EducationEntry]:
    lines = [l.strip() for l in education_text.splitlines()]
    out = []
    for i, line in enumerate(lines):
        m = _DEGREE.search(line)
        if not m:
            continue
        raw = m.group(1)
        degree = _DEGREE_NAMES.get(re.sub(r"[.\s']", "", raw).lower(), raw)
        rest = line[m.end():]
        field_match = re.match(r"\s*(?:in|of|-|,|\()?\s*([A-Za-z &]+?)\s*(?:[,|)(–—]|\d|$)", rest)
        field = field_match.group(1).strip() if field_match and field_match.group(1).strip() else None
        if field and _INSTITUTION.search(field):
            field = None
        nearby = [line] + [l for l in lines[i + 1:i + 3] if l and not _DEGREE.search(l)]
        institution = next((p.strip() for l in nearby for p in re.split(r"[,|–—]", l) if _INSTITUTION.search(p)), None)
        years = [int(y) for l in nearby for y in _YEAR.findall(l)]
        out.append(EducationEntry(degree=degree, field=field, institution=institution,
                                  year=max(years) if years else None))
    return out
