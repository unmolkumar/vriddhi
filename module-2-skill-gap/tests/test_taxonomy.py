"""Taxonomy integrity and vocabulary alignment with module 1."""
import json
import re
from pathlib import Path

import pytest

from src.engines.skill_extractor import _key, coarser_ids, extract_skills, m1_slug, resolve_skill, taxonomy

MOCKS = json.loads((Path(__file__).parent / "mocks" / "m1_contract.json").read_text(encoding="utf-8"))
SKILLS = taxonomy()["skills"]
IDS = {s["id"] for s in SKILLS}
CATEGORIES = {"language", "framework", "data", "ml", "cloud", "devops", "database", "tool", "concept"}


def test_size_in_range():
    assert 400 <= len(SKILLS) <= 500


def test_entries_are_well_formed():
    for s in SKILLS:
        assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", s["id"]), s["id"]
        assert s["display"] and s["aliases"]
        assert all(a == a.lower() for a in s["aliases"]), s["id"]
        assert s["category"] in CATEGORIES
        assert s["difficulty_tier"] in (1, 2, 3)
        assert s["esco_uri"] is None  # mapping not done yet; never claim it is
        assert s["maps_to"] is None or s["maps_to"] in IDS
        assert all(p in IDS for p in s["prerequisites"])
        for alias, rule in s.get("context", {}).items():
            assert alias in s["aliases"] and rule in ("list", "case")


def test_ids_and_alias_keys_unique():
    assert len(IDS) == len(SKILLS)
    owner = {}
    for s in SKILLS:
        for alias in [s["display"].lower(), *s["aliases"]]:
            k = _key(alias)
            assert owner.setdefault(k, s["id"]) == s["id"], f"{alias!r}: {owner[k]} vs {s['id']}"


def test_maps_to_and_prerequisites_have_no_cycles():
    by_id = {s["id"]: s for s in SKILLS}
    for s in SKILLS:
        chain = coarser_ids(s["id"])
        assert s["id"] not in chain and len(chain) == len(set(chain))
    state = {}

    def visit(n):
        assert state.get(n) != "visiting", f"prerequisite cycle through {n}"
        if state.get(n) == "done":
            return
        state[n] = "visiting"
        for p in by_id[n]["prerequisites"]:
            visit(p)
        state[n] = "done"

    for sid in by_id:
        visit(sid)


@pytest.mark.parametrize("m1_id", sorted(set(MOCKS["documented_top_skills"] + MOCKS["normalize_skill_alias_targets"]
                                            + MOCKS["derived_ids"])))
def test_every_known_m1_id_resolves(m1_id):
    assert resolve_skill(m1_id) is not None, m1_id


def test_m1_examples_resolve_to_themselves():
    for example in MOCKS["career_analysis_examples"].values():
        for m1_id in example["top_skills"]:
            assert resolve_skill(m1_id)["id"] == m1_id


def test_m1_slug_replica_matches_module_1_behaviour():
    assert m1_slug("Machine Learning") == "machine_learning"
    assert m1_slug("C++") == "cpp" and m1_slug("C#") == "csharp"
    assert m1_slug("Node.js") == "nodejs" and m1_slug("Power BI") == "power_bi"
    assert m1_slug("Amazon Web Services") == "aws" and m1_slug("golang") == "go"


@pytest.mark.parametrize("name, expected", [
    ("apache_spark", "spark"), ("ML", "machine_learning"), ("Postgres", "postgresql"), ("k8s", "kubernetes"),
    ("Python 3", "python"), ("python programming", "python"), ("rest_api", "rest_apis"), ("nlp", "natural_language_processing"),
    ("Microsoft Azure", "azure"), ("weights___biases", "weights_biases"),
])
def test_resolve_variants(name, expected):
    assert resolve_skill(name)["id"] == expected


def test_unknown_skill_does_not_resolve():
    assert resolve_skill("quantum basket weaving") is None


def test_spec_normalisation_examples():
    # context/MODULE-2-SKILL-GAP.md "Skill Normalization"
    for text in ["Python", "Python 3", "Python programming", "Python development"]:
        assert [h.id for h in extract_skills(text, use_llm=False)] == ["python"], text
    for text in ["Postgres", "PostgreSQL", "PostgreSQL DB"]:
        assert [h.id for h in extract_skills(text, use_llm=False)] == ["postgresql"], text


def test_coarser_chain():
    assert coarser_ids("amazon_s3") == ["aws", "cloud"]
    assert coarser_ids("postgresql") == ["sql"]
    assert coarser_ids("tensorflow")[:2] == ["deep_learning", "machine_learning"]


@pytest.mark.parametrize("text, expected", [
    ("Tableau Desktop and Tableau Server", "tableau"), ("Power BI Desktop", "power_bi"), ("MS Excel", "excel"),
    ("Microsoft Excel", "excel"), ("Jupyter Notebook", "jupyter"), ("Docker Desktop", "docker"),
    ("Amazon AWS", "aws"), ("MySQL Workbench", "mysql"), ("SSMS", "microsoft_sql_server"),
])
def test_product_variant_aliases(text, expected):
    assert [h.id for h in extract_skills(text, use_llm=False)] == [expected]


@pytest.mark.parametrize("m1_id, expected", [
    ("tableau_desktop", "tableau"), ("power_bi_desktop", "power_bi"), ("ms_excel", "excel"),
    ("google_colab", "jupyter"), ("aws_ec2", "amazon_ec2"), ("aws_s3", "amazon_s3"), ("aws_lambda", "aws_lambda"),
])
def test_product_variant_ids_resolve(m1_id, expected):
    assert resolve_skill(m1_id)["id"] == expected
    if expected.startswith(("amazon_", "aws_")):
        assert coarser_ids(expected) == ["aws", "cloud"]


# --- module 1's exported skill vocabulary (m1_target_roles_skills_export.json) -----------------

_LIVE_EXPORT = Path(__file__).resolve().parents[2] / "module-1-career-intelligence" / "data" / "m1_target_roles_skills_export.json"
_VENDORED_EXPORT = Path(__file__).parent / "mocks" / "m1_target_roles_skills_export.json"


def _export_ids() -> list[str]:
    """Every id module 1 emits, from its live export when present, else the vendored copy (module stays standalone)."""
    ids = set()
    for path in (_LIVE_EXPORT, _VENDORED_EXPORT):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            ids.update(data["all_emitted_skill_ids"])
            for role in data["target_roles"].values():
                ids.update(role["top_skills"])
                ids.update(role.get("top_skill_weights", {}))
    return sorted(ids)


def test_export_is_available():
    assert _VENDORED_EXPORT.exists() and len(_export_ids()) >= 21


@pytest.mark.parametrize("m1_id", _export_ids())
def test_every_exported_m1_id_is_covered(m1_id):
    """100% coverage: each id resolves to a taxonomy entry (exact id or alias, e.g. pyspark -> spark),
    or is a documented non-skill category."""
    from src.engines.skill_extractor import non_skill_reason
    assert resolve_skill(m1_id) is not None or non_skill_reason(m1_id), m1_id


def test_exported_alias_ids_resolve_to_the_right_skill():
    assert resolve_skill("pyspark")["id"] == "spark"


def test_non_skill_ids_are_documented_and_not_skills():
    non_skill = taxonomy()["non_skill_ids"]
    assert set(non_skill) == {"data"} and all(reason for reason in non_skill.values())
    assert all(sid not in IDS for sid in non_skill)


@pytest.mark.parametrize("text, expected", [
    ("Infrastructure automation with Ansible", {"automation", "ansible"}),
    ("Skills: Python, Bash, Automation", {"python", "shell_scripting", "automation"}),
    ("Automation testing with Selenium", {"automation_testing", "selenium"}),  # longest match wins
    ("automation of monthly reports", set()),                                   # bare word in prose: not a skill
])
def test_automation_skill(text, expected):
    assert {h.id for h in extract_skills(text, use_llm=False)} == expected


def test_automation_and_backend_relations():
    assert resolve_skill("automation")["maps_to"] == "devops"
    assert resolve_skill("backend")["id"] == "backend" and resolve_skill("Backend Development")["id"] == "backend"
