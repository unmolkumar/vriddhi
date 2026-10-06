"""Calibration floors with MiniLM and the shipped (frozen) constants.

Tuning set: thresholds were tuned on it, against the 15 export occupations; the A1 assertions stay.
Held-out sets: never tuned on. Floors sit one profile below the results first observed with the frozen constants
(WORKING.md section 11.3), so they catch regressions without pretending to more accuracy than measured.
Skips when the embedding model can't load (first run offline). Encodings are cached under data/cache/embeddings.
"""
import pytest

from src.general import calibration as cal
from src.general.embeddings import DEFAULT_MODEL, Encoder

RN, ELECTRICIANS, ACCOUNTANTS, DATA_SCIENTISTS = "29-1141.00", "47-2111.00", "13-2011.00", "15-2051.00"
CLEAR_MARGIN = 0.10
# set: (top-1 floor, top-3 floor, mean-margin floor), one profile below the A1c results with the frozen constants:
# b 12/15 14/15 +0.114 | c 10/10 10/10 +0.195 | d 12/15 14/15 +0.094 | e 13/15 15/15 +0.135 | f 10/10 10/10 +0.173 |
# g 13/15 15/15 +0.122 | b vs export15 15/15 +0.143 | e vs export15 14/15 15/15 +0.177
HELDOUT_FLOORS = {
    "b_heldout": (11, 13, 0.08),
    "c_new_occupations": (9, 9, 0.16),
    "d_heldout_no_curated": (11, 13, 0.06),
    "e_heldout2": (12, 14, 0.10),
    "f_heldout2_extra_occupations": (9, 9, 0.14),
    "g_heldout2_no_curated": (12, 14, 0.09),
    "b_heldout_vs_export15": (14, 14, 0.11),
    "e_heldout2_vs_export15": (13, 14, 0.14),
}
A2_GATE = {"e_heldout2": 12, "f_heldout2_extra_occupations": 7}


@pytest.fixture(scope="module")
def built():
    try:
        encoder = Encoder(DEFAULT_MODEL)
        encoder.encode(["warm up"])
    except Exception as e:  # no model files and no network
        pytest.skip(f"{DEFAULT_MODEL} unavailable: {type(e).__name__}")
    return cal.build(encoder)


@pytest.fixture(scope="module")
def export_socs(built):
    _, occupations, _ = built
    client = cal.FixtureM1Client(cal.FIXTURE_PATH)
    return {o.soc for o in occupations if o.soc in client.occupations}


@pytest.fixture(scope="module")
def tuning(built, export_socs):
    profiles, occupations, pairs = built
    ps, os_ = cal.subset(profiles, occupations, sets=("tuning",), socs=export_socs)
    return cal.evaluate(ps, os_, pairs)


def _row(result, name_prefix):
    i = next(n for n, name in enumerate(result.names) if name.startswith(name_prefix))
    return dict(zip(result.socs, result.matrix[i])), result.names[i]


# --- tuning set (15 export occupations) -------------------------------------------------------------

def test_tuning_top1_and_top3(tuning):
    assert tuning.n == 15 and len(tuning.socs) == 15
    assert tuning.top1 >= 13, tuning.ranks
    assert tuning.top3 == 15, tuning.ranks


def test_nurse_profile_beats_electricians_clearly(tuning):
    row, _ = _row(tuning, f"tuning/{RN}")
    assert row[RN] - row[ELECTRICIANS] >= CLEAR_MARGIN


def test_accountant_profile_ranks_accountants_top_two(tuning):
    _, name = _row(tuning, f"tuning/{ACCOUNTANTS}")
    assert tuning.ranks[name] <= 2


def test_data_scientist_profile_still_tops_data_scientists(tuning):
    row, name = _row(tuning, f"tuning/{DATA_SCIENTISTS}")
    assert tuning.ranks[name] == 1 and max(row, key=row.get) == DATA_SCIENTISTS


def test_partial_profiles_score_below_the_full_ones(tuning):
    for soc in (RN, ACCOUNTANTS, ELECTRICIANS):
        full, _ = _row(tuning, f"tuning/{soc}")
        partial, _ = _row(tuning, f"tuning/{cal.PARTIAL_PREFIX}{soc}")
        assert partial[soc] < full[soc]


# --- held-out sets (25 occupations unless noted) ------------------------------------------------------

@pytest.fixture(scope="module")
def heldout(built, export_socs):
    profiles, occupations, pairs = built
    out = {}
    for label, sets, drop, socs, only, exclude in (
            ("b_heldout", ("heldout",), False, None, None, None),
            ("c_new_occupations", ("new",), False, None, None, None),
            ("d_heldout_no_curated", ("heldout",), True, None, None, None),
            ("e_heldout2", ("heldout2",), False, None, export_socs, None),
            ("f_heldout2_extra_occupations", ("heldout2",), False, None, None, export_socs),
            ("g_heldout2_no_curated", ("heldout2",), True, None, export_socs, None),
            ("b_heldout_vs_export15", ("heldout",), False, export_socs, None, None),
            ("e_heldout2_vs_export15", ("heldout2",), False, export_socs, export_socs, None)):
        ps, os_ = cal.subset(profiles, occupations, sets=sets, socs=socs, profile_socs=only, exclude_profile_socs=exclude)
        out[label] = cal.evaluate(ps, os_, pairs, drop_curated=drop)
    return out


def test_a2_gate(heldout):
    for label, floor in A2_GATE.items():
        assert heldout[label].top1 >= floor, (label, heldout[label].ranks)


@pytest.mark.parametrize("label", list(HELDOUT_FLOORS))
def test_heldout_floors(heldout, label):
    top1, top3, mean_margin = HELDOUT_FLOORS[label]
    r = heldout[label]
    assert r.top1 >= top1, r.ranks
    assert r.top3 >= top3, r.ranks
    assert r.mean_margin >= mean_margin


def test_curated_rows_do_not_carry_the_heldout_result(heldout):
    """Removing every curated row changes top-1 by at most one profile."""
    assert abs(heldout["b_heldout"].top1 - heldout["d_heldout_no_curated"].top1) <= 1
    assert abs(heldout["e_heldout2"].top1 - heldout["g_heldout2_no_curated"].top1) <= 1
