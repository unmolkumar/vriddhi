"""Dictionary pass, ambiguous aliases, and the fail-safe LLM pass."""
import pytest

import src.engines.skill_extractor as se
from src.engines.skill_extractor import extract_skills


def ids(text, **kw):
    return [h.id for h in extract_skills(text, use_llm=False, **kw)]


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch, tmp_path):
    """Tests never reach the network or the real cache, whatever is in .env."""
    def fail(*_a, **_k):
        raise AssertionError("real LLM called")
    monkeypatch.setattr(se, "_call_llm", fail)
    monkeypatch.setattr(se, "LLM_CACHE_DIR", tmp_path / "llm")


def test_site_job_description():
    text = ("Looking for a senior engineer with experience in Python, TensorFlow, ML pipelines, A/B testing, "
            "SQL and Apache Spark. Must have strong statistical analysis skills.")
    assert set(ids(text)) == {"python", "tensorflow", "ml_pipelines", "a_b_testing", "sql", "spark", "statistics"}


@pytest.mark.parametrize("text", ["ML", "machine-learning", "machine learning", "Machine Learning (ML)", "Python, M.L., SQL"])
def test_machine_learning_aliases(text):
    assert "machine_learning" in ids(text)


@pytest.mark.parametrize("text, expected", [
    ("Languages: Python, Go, SQL", {"python", "go", "sql"}),
    ("Golang microservices", {"go", "microservices"}),
    ("We go the extra mile.", set()),
    ("Go-to-market strategy", set()),
    ("Python, R, SQL", {"python", "r", "sql"}),
    ("Data analysis in R", {"data_analysis", "r"}),
    ("R&D team", set()),
    ("C, C++, Java", {"c", "cpp", "java"}),
    ("C++ only", {"cpp"}),
    ("C# and .NET", {"csharp", "dotnet"}),
    ("Grade C. Vitamin C.", set()),
    ("Advanced Excel and Power BI", {"excel", "power_bi"}),
    ("excel in teamwork", set()),
    ("Excel in teamwork", set()),
    ("Spring Boot microservices", {"spring_boot", "microservices"}),
    ("Internship in Spring 2023", set()),
    ("Java, Spring, Hibernate", {"java", "spring", "hibernate"}),
    ("Met M.L. Sharma at the event", set()),
])
def test_ambiguous_aliases(text, expected):
    assert set(ids(text)) == expected


def test_skills_context_allows_single_ambiguous_item():
    assert ids("Go", skills_context=True) == ["go"]
    assert ids("Go") == []


def test_longest_match_wins():
    assert set(ids("React Native and React")) == {"react_native", "react"}
    assert ids("React Native app") == ["react_native"]
    assert set(ids("PySpark on MySQL and PostgreSQL")) == {"spark", "mysql", "postgresql"}  # no bare "sql"


def test_urls_and_emails_are_ignored():
    assert ids("github.com/someone, me@python.org, https://docker.com/x") == []


def test_output_shape():
    hit = extract_skills("Uses k8s daily", use_llm=False)[0]
    assert (hit.id, hit.display, hit.in_taxonomy, hit.source) == ("kubernetes", "Kubernetes", True, "dictionary")
    assert hit.matches == ["k8s"]


def test_llm_off_never_calls_llm(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    assert ids("Python and SQL") == ["python", "sql"]


def test_no_api_key_falls_back_to_dictionary(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert [h.id for h in extract_skills("Python and SQL", use_llm=True)] == ["python", "sql"]


@pytest.mark.parametrize("error", [TimeoutError("slow"), RuntimeError("429 rate limit"), ValueError("bad json")])
def test_llm_failure_falls_back_to_dictionary(monkeypatch, error):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def boom(*_a, **_k):
        raise error
    monkeypatch.setattr(se, "_call_llm", boom)
    assert [h.id for h in extract_skills("Python and SQL", use_llm=True)] == ["python", "sql"]


def test_llm_results_are_normalised_grounded_and_cached(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = []

    def fake(text, found, key):
        calls.append(text)
        return ["Spark", "Snowpark", "Kubernetes", "Rust", "42", ""]
    monkeypatch.setattr(se, "_call_llm", fake)
    text = "Python jobs that use spark and Snowpark."
    hits = {h.id: h for h in extract_skills(text, use_llm=True)}
    assert set(hits) == {"python", "spark", "snowpark"}  # Kubernetes / Rust aren't in the text
    assert (hits["spark"].source, hits["spark"].in_taxonomy) == ("llm", True)
    assert (hits["snowpark"].in_taxonomy, hits["snowpark"].display) == (False, "Snowpark")
    extract_skills(text, use_llm=True)
    assert len(calls) == 1  # second call served from the cache
