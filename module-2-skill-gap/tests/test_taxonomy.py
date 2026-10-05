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
