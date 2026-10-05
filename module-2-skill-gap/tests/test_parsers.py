"""Document parsing, structured errors, sections, work history and education."""
import io
import zipfile
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from src.parsers.resume_parser import MAX_FILE_BYTES, MAX_PAGES, ResumeParseError, parse_document, parse_text
from src.parsers.section_segmenter import extract_education, extract_work_history, merged_years, segment

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 1)


def fixture(name):
    return (FIXTURES / name).read_bytes()


def error_code(data, filename=None):
    with pytest.raises(ResumeParseError) as e:
        parse_document(data, filename)
    body = e.value.to_response().model_dump()
    assert set(body["error"]) == {"code", "message"} and body["error"]["message"]
    return e.value.code


def test_text_pdf():
    doc = parse_document(fixture("resume_text.pdf"), "resume.pdf")
    assert (doc.format, doc.pages, doc.ocr_pages) == ("pdf", 1, [])
    assert "EXPERIENCE" in doc.text and "Swiggy" in doc.text


def test_scanned_pdf_uses_ocr():
    doc = parse_document(fixture("resume_scanned.pdf"))
    assert (doc.format, doc.ocr_pages) == ("pdf", [1])
    assert "Swiggy" in doc.text and "EXPERIENCE" in doc.text


def test_docx_and_text():
    assert parse_document(fixture("resume.docx"), "cv.docx").format == "docx"
    assert "Swiggy" in parse_document(fixture("resume.docx")).text  # detected without the extension
    assert parse_document(fixture("resume.txt"), "cv.txt").format == "text"
    assert parse_text("Python, SQL").format == "text"


def test_encrypted_pdf():
    assert error_code(fixture("resume_encrypted.pdf")) == "ENCRYPTED_FILE"


def test_corrupt_pdf():
    assert error_code(fixture("resume_corrupt.pdf"), "cv.pdf") == "CORRUPT_FILE"


def test_corrupt_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<not really xml")
    assert error_code(buf.getvalue(), "cv.docx") == "CORRUPT_FILE"
    assert error_code(b"PK\x03\x04garbage", "cv.docx") == "CORRUPT_FILE"


def test_oversized_file():
    assert error_code(b"%PDF-1.7" + b"0" * MAX_FILE_BYTES) == "FILE_TOO_LARGE"


def test_too_many_pages():
    doc = pymupdf.open()
    for _ in range(MAX_PAGES + 1):
        doc.new_page().insert_text((50, 50), "Python developer with SQL experience and more text here.")
    assert error_code(doc.tobytes()) == "TOO_MANY_PAGES"


@pytest.mark.parametrize("data, code", [
    (b"", "EMPTY_DOCUMENT"),
    (b"   \n  ", "EMPTY_DOCUMENT"),
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00binary", "UNSUPPORTED_FORMAT"),
    (b"\xd0\xcf\x11\xe0legacy-doc", "UNSUPPORTED_FORMAT"),
])
def test_wrong_or_empty_input(data, code):
    assert error_code(data) == code


def test_blank_pdf_is_empty_not_a_crash(monkeypatch):
    import src.parsers.resume_parser as rp
    monkeypatch.setattr(rp, "_ocr_page", lambda page: "")  # skip the slow OCR model for a blank page
    doc = pymupdf.open()
    doc.new_page()
    assert error_code(doc.tobytes()) == "EMPTY_DOCUMENT"


def test_segment_headings():
    text = "Jane Doe\nPROFESSIONAL SUMMARY\nAnalyst.\nWork Experience:\nDid SQL.\nTechnical Skills\nSkills: Python\nAcademic Projects\nA model.\nEducation\nB.Tech"
    s = segment(text)
    assert s["header"] == "Jane Doe" and s["summary"] == "Analyst."
    assert s["experience"] == "Did SQL." and s["skills"] == "Skills: Python"  # inline label is content
    assert s["projects"] == "A model." and s["education"] == "B.Tech"


def test_segment_without_headings():
    assert set(segment("Just some text\nwith lines")) == {"header"}


def test_work_history_merges_overlaps_and_separates_internships():
    text = ("Data Analyst | Swiggy | Jun 2021 - Present\n- SQL\n"
            "Junior Data Analyst, Mu Sigma | Jul 2019 - Aug 2021\n"
            "Data Science Intern | Fractal | Jan 2019 - Jun 2019")
    entries, years, intern = extract_work_history(text, TODAY)
    assert [(e.title, e.company, e.current, e.internship) for e in entries] == [
        ("Data Analyst", "Swiggy", True, False),
        ("Junior Data Analyst", "Mu Sigma", False, False),
        ("Data Science Intern", "Fractal", False, True),
    ]
    assert (entries[0].start, entries[0].end, entries[1].end) == ("2021-06", None, "2021-08")
    assert (years, intern) == (7.5, 0.5)  # Jul 2019 - Oct 2026 = 88 months; overlap counted once


@pytest.mark.parametrize("line, years", [
    ("Engineer, Acme | 01/2020 - 12/2021", 2.0),
    ("Engineer, Acme | 2018 - 2020", 2.0),          # year-only dates count from January
    ("Engineer, Acme | Jan'22 – Jun'22", 0.5),
    ("Engineer, Acme | March 2024 to Current", 2.5),
    ("Engineer Acme Jun 2021 Present", 5.5),        # OCR dropped the dash and separators
    ("Engineer, Acme | Jan 2030 - Present", 0.0),   # future start is ignored
    ("Engineer at Acme, no dates", 0.0),
])
def test_date_formats(line, years):
    assert extract_work_history(line, TODAY)[1] == years


def test_company_on_line_above_and_ocr_style_header():
    entries = extract_work_history("Swiggy, Bengaluru\nData Analyst    Jan 2021 – Present", TODAY)[0]
    assert (entries[0].title, entries[0].company) == ("Data Analyst", "Swiggy")
    entries = extract_work_history("Built dashboards for every city team\nData Analyst Swiggy Jun 2021 Present", TODAY)[0]
    assert (entries[0].title, entries[0].company) == ("Data Analyst", "Swiggy")


def test_merged_years():
    assert merged_years([]) == 0.0
    assert merged_years([(0, 11), (12, 23)]) == 2.0      # back-to-back jobs
    assert merged_years([(0, 11), (6, 17)]) == 1.5       # overlap counted once


def test_education():
    out = extract_education("B.Tech in Computer Science, NIT Trichy, 2019\nClass XII, CBSE, 2015")
    assert [(e.degree, e.field, e.institution, e.year) for e in out] == [
        ("B.Tech", "Computer Science", "NIT Trichy", 2019)]
    out = extract_education("M.Sc (Statistics)\nUniversity of Delhi\n2017 - 2019")
    assert (out[0].degree, out[0].field, out[0].institution, out[0].year) == ("M.Sc", "Statistics", "University of Delhi", 2019)
    assert extract_education("MCA, Anna University")[0].degree == "MCA"
    assert extract_education("No degree here") == []
