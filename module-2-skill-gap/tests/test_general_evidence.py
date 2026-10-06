"""General engine (v2) - evidence units from resume text, free text, typed skills and a v1 profile."""
from src.engines.profile_builder import profile_from_manual
from src.general.evidence import from_profile, from_skills, from_text
from src.models.schemas import ManualProfileInput

RESUME = """Ramesh Gowda
ramesh@example.com | +91 98450 12345

Experience
Maintenance Electrician | Brigade Group | 2018 - Present
- Lay conduits and pull wires for lighting circuits. Install MCBs and DBs as per the drawing.
- Find faults in motors using a multimeter.

Projects
- Solar rooftop wiring for a school.

Skills
Software: AutoCAD, MS Excel | panel wiring

Education
ITI Electrician, 2016
"""


def test_resume_sections_become_typed_units_with_spans():
    units = from_text(RESUME)
    by_text = {u.text: u for u in units}
    lay = by_text["Lay conduits and pull wires for lighting circuits."]
    assert lay.evidence_type == "work" and lay.section == "experience"
    assert RESUME[lay.span[0]:lay.span[1]] == lay.text                     # span points at the original text
    assert by_text["Install MCBs and DBs as per the drawing."].evidence_type == "work"   # sentences split
    assert by_text["Solar rooftop wiring for a school."].evidence_type == "project"
    assert {"AutoCAD", "MS Excel", "panel wiring"} <= set(by_text)          # skills-section items, label stripped
    assert by_text["MS Excel"].evidence_type == "mentioned" and "excel" in by_text["MS Excel"].skill_ids
    assert not any("@" in u.text or "98450" in u.text for u in units)      # contact lines are not evidence


def test_free_text_without_headings_is_one_section():
    text = "I have worked as a staff nurse for five years. I give IV medicines and chart vitals."
    units = from_text(text)
    assert [u.text for u in units] == ["I have worked as a staff nurse for five years.",
                                       "I give IV medicines and chart vitals."]
    assert {u.section for u in units} == {"free_text"} and units[1].span == (47, 84)


def test_typed_skills_are_self_reported_with_taxonomy_ids():
    units = from_skills(["Python", "Tally", " ", "Postgres"])
    assert [u.text for u in units] == ["Python", "Tally", "Postgres"]
    assert all(u.evidence_type == "self" and u.section == "typed" for u in units)
    assert units[0].skill_ids == ["python"] and units[2].skill_ids == ["postgresql"]


def test_v1_profile_becomes_units():
    profile = profile_from_manual(ManualProfileInput(skills=[{"name": "Python", "level": 2}, {"name": "SQL"}]))
    units = from_profile(profile)
    assert {u.text for u in units} >= {"Python", "SQL"}
    assert all(u.evidence_type == "self" and u.section == "profile" for u in units)
    assert units[0].skill_ids[0] in {"python", "sql"}
