"""General engine (v2) - matcher: alias layer first, per-type thresholds, best evidence, provisional coverage.

Uses a deterministic bag-of-words encoder, so no model download is needed.
"""
import re
import zlib

import numpy as np
import pytest

from src.general.evidence import EvidenceUnit
from src.general.matcher import THRESHOLDS, TYPE_SHARE, coverage, match, status_of
from src.general.requirements import normalise


class WordEncoder:
    """Cosine = word overlap (hashed bag of words). encode_cached ignores the cache."""
    name = "words"
    DIM = 4096

    def encode(self, texts):
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for r, t in enumerate(texts):
            for w in set(re.findall(r"[a-z]+", t.lower())):
                out[r, zlib.crc32(w.encode()) % self.DIM] = 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)

    def encode_cached(self, key, texts):
        return self.encode(texts)


@pytest.fixture
def encoder():
    return WordEncoder()


def rows():
    return [
        {"soc_code": "s", "item_type": "tech", "item_name": "Microsoft Excel", "item_description": "Spreadsheet software",
         "importance_norm": 0.85, "reliable": 1},
        {"soc_code": "s", "item_type": "task", "item_name": "Reconcile bank statements monthly",
         "item_description": "Reconcile bank statements monthly", "importance_norm": 0.9, "reliable": 1},
        {"soc_code": "s", "item_type": "task", "item_name": "Prepare payroll registers",
         "item_description": "Prepare payroll registers", "importance_norm": 0.7, "reliable": 1},
        {"soc_code": "s", "item_type": "ability", "item_name": "Near Vision", "item_description": "",
         "importance_norm": 0.6, "reliable": 1},
    ]


def units():
    return [EvidenceUnit(text="Advanced Excel", evidence_type="mentioned", section="skills", skill_ids=["excel"]),
            EvidenceUnit(text="Reconcile bank statements monthly for clients", evidence_type="work",
                         section="experience", span=(10, 55))]


def test_alias_layer_meets_tech_before_semantics(encoder):
    items, _ = normalise(rows())
    by = {m.item.name: m for m in match(items, units(), encoder)}
    excel = by["Microsoft Excel"]
    assert (excel.status, excel.reason, excel.evidence_text) == ("met", "alias", "Advanced Excel")


def test_semantic_status_reason_and_best_evidence(encoder):
    items, _ = normalise(rows())
    by = {m.item.name: m for m in match(items, units(), encoder)}
    rec = by["Reconcile bank statements monthly"]
    assert rec.status == "met" and rec.reason == "semantic" and rec.similarity == pytest.approx(4 / np.sqrt(4 * 6), abs=1e-3)
    assert (rec.evidence_type, rec.evidence_section, rec.evidence_span) == ("work", "experience", (10, 55))
    payroll = by["Prepare payroll registers"]
    assert payroll.status == "missing" and payroll.reason == "none"


def test_thresholds_are_per_item_type():
    met, partial = THRESHOLDS["task"]
    assert status_of("task", met) == "met" and status_of("task", partial) == "partial"
    assert status_of("task", partial - 0.01) == "missing"
    assert THRESHOLDS["tech"][0] != THRESHOLDS["knowledge"][0]
    sim = THRESHOLDS["knowledge"][0]
    assert status_of("knowledge", sim) == "met" and status_of("tech", sim) == "missing"


def test_no_evidence_means_everything_missing(encoder):
    items, _ = normalise(rows())
    assert {m.status for m in match(items, [], encoder)} == {"missing"}


def test_coverage_balances_types_and_ignores_abilities(encoder):
    items, _ = normalise(rows())
    matches = match(items, units(), encoder)
    # tech: 1 of 1 met; task: weight 0.9 met of 1.6; ability has weight 0 and share 0
    tech, task = TYPE_SHARE["tech"], TYPE_SHARE["task"]
    expected = (tech * 1.0 + task * 0.9 / 1.6) / (tech + task)
    assert coverage(matches) == pytest.approx(expected)
