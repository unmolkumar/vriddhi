"""Calibration floor: each profile scored against all 15 fixture occupations with MiniLM and the shipped constants.

Skips when the embedding model can't load (first run offline). Encodings are cached under data/cache/embeddings.
"""
import pytest

from src.general import calibration as cal
from src.general.embeddings import DEFAULT_MODEL, Encoder

TOP1_FLOOR = 13            # of 15 (target from the A1 brief; currently 15)
RN, ELECTRICIANS, ACCOUNTANTS, DATA_SCIENTISTS = "29-1141.00", "47-2111.00", "13-2011.00", "15-2051.00"
CLEAR_MARGIN = 0.10


@pytest.fixture(scope="module")
def result():
    try:
        encoder = Encoder(DEFAULT_MODEL)
        encoder.encode(["warm up"])
    except Exception as e:  # no model files and no network
        pytest.skip(f"{DEFAULT_MODEL} unavailable: {type(e).__name__}")
    profiles, occupations, pairs = cal.build(encoder)
    return cal.evaluate(profiles, occupations, pairs)


def _row(result, prefix):
    i = next(n for n, name in enumerate(result.names) if name.startswith(prefix))
    return dict(zip(result.socs, result.matrix[i])), result.names[i]


def test_top1_and_top3_accuracy(result):
    assert result.n == 15
    assert result.top1 >= TOP1_FLOOR, result.ranks
    assert result.top3 == 15, result.ranks


def test_nurse_profile_beats_electricians_clearly(result):
    row, _ = _row(result, RN)
    assert row[RN] - row[ELECTRICIANS] >= CLEAR_MARGIN


def test_accountant_profile_ranks_accountants_top_two(result):
    _, name = _row(result, ACCOUNTANTS)
    assert result.ranks[name] <= 2


def test_data_scientist_profile_still_tops_data_scientists(result):
    row, name = _row(result, DATA_SCIENTISTS)
    assert result.ranks[name] == 1 and max(row, key=row.get) == DATA_SCIENTISTS


def test_partial_profiles_score_below_the_full_ones(result):
    for soc in (RN, ACCOUNTANTS, ELECTRICIANS):
        full, _ = _row(result, soc)
        partial, _ = _row(result, cal.PARTIAL_PREFIX + soc)
        assert partial[soc] < full[soc]
