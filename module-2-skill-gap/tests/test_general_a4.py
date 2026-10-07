"""General engine (v2) - A4: over-qualified guard (reliable Indian bands only), related occupations without tiers,
basics cap. Word-overlap encoder and fake module 1 clients."""
from src.general import roadmap, scoring
from src.general.schemas import GapAnalysisV2Request
from src.general.service import ROLE_RELATED_UNTIERED_TOP, GeneralEngine, close_related
from test_general_a3b import match
from test_general_matcher import WordEncoder
from test_general_service import EVIDENCE, TwoRoles

STALE = {"typical_min": 1.3, "typical_max": 3.3, "sample_size": 3, "years_covered": "2015-2016",
         "fallback_to_job_zone": False}                                  # module 1's v2.2 Registered Nurses band
RELIABLE = dict(STALE, sample_size=120, years_covered="2023-2025")


# --- C4: over-qualified guard ----------------------------------------------------------------------------

def test_indian_band_needs_enough_recent_postings():
    zone4 = {"job_zone": {"job_zone": 4}}
    assert scoring.experience_band({"indian_experience": RELIABLE, **zone4}) == (1.3, 3.3, "india_postings")
    for exp in (STALE, dict(RELIABLE, sample_size=29), dict(RELIABLE, years_covered="2019-2022"),
                {"typical_min": 1, "typical_max": 4, "sample_size": 99},                 # no years_covered: unknown
                dict(RELIABLE, fallback_to_job_zone=True)):
        assert scoring.experience_band({"indian_experience": exp, **zone4}) == (2.0, 6.0, "job_zone")


class Nurses(TwoRoles):
    """Staff nurses (job zone 4, with module 1's Indian band) and a more senior related role (job zone 5)."""
    def __init__(self, band):
        super().__init__(["Change patient dressings", "Record vital signs", "Supervise ward staff"])
        self.band = band

    def profile(self, soc):
        p = super().profile(soc)
        zone = 4 if soc == "11-0001.00" else 5
        return p | {"job_zone": {"job_zone": zone}} | ({"indian_experience": self.band} if zone == 4 else {})


def test_experienced_nurse_is_not_over_qualified_on_a_stale_band():
    req = GapAnalysisV2Request(soc_code="11-0001.00", free_text=EVIDENCE, experience_years=7.5)
    stale = GeneralEngine(client=Nurses(STALE), encoder=WordEncoder(), translator=None, rephraser=None).analyze(req)
    assert stale.score_breakdown.experience_band_source == "job_zone" and stale.verdict.label == "good_fit"
    reliable = GeneralEngine(client=Nurses(RELIABLE), encoder=WordEncoder(), translator=None, rephraser=None).analyze(req)
    assert reliable.score_breakdown.experience_band == (1.3, 3.3) and reliable.verdict.label == "over_qualified"


def test_over_qualified_needs_more_than_the_extra_years():
    band, t = scoring.Band(2.0, 6.0, "job_zone"), scoring.GOOD_FIT_THRESHOLD
    better = ("11-9111.00", "Medical and Health Services Managers", 0.5)
    assert scoring.verdict(t + 0.1, 6 + scoring.OVERQUALIFIED_EXTRA_YEARS, band, better)[0] == "good_fit"
    assert scoring.verdict(t + 0.1, 6 + scoring.OVERQUALIFIED_EXTRA_YEARS + 0.5, band, better)[0] == "over_qualified"


# --- C5: related occupations without tiers -------------------------------------------------------------

def test_close_related_uses_tiers_when_present_else_module_1_order():
    untiered = [{"related_soc_code": f"00-000{i}.00", "relatedness_tier": None, "index_val": i} for i in range(8, 0, -1)]
    assert close_related(untiered) == {f"00-000{i}.00" for i in range(1, ROLE_RELATED_UNTIERED_TOP + 1)}
    tiered = [{"related_soc_code": "a", "relatedness_tier": "Primary-Short", "index_val": 1},
              {"related_soc_code": "b", "relatedness_tier": "Supplemental", "index_val": 2},
              {"related_soc_code": "c", "relatedness_tier": "Primary-Long", "index_val": 3}]
    assert close_related(tiered) == {"a", "c"}
    assert close_related([{"related_soc_code": "x", "relatedness_tier": "fixture_same_major_group"}]) == set()


# --- C6: basics cap -----------------------------------------------------------------------------------

def test_basics_are_capped_above_a_minimum_weight():
    names = ["Microsoft Access", "Microsoft Outlook", "Microsoft PowerPoint", "Microsoft SharePoint",
             "Microsoft Windows", "Microsoft Exchange", "Google Docs", "Google Drive"]
    weights = [0.85, 0.85, 0.85, 0.85, 0.85, 0.5, 0.255, 0.15]
    gaps = []
    for n, w in zip(names, weights):
        m = match(n, "tech")
        gaps.append(m.model_copy(update={"item": m.item.model_copy(update={"weight": w})}))
    main, later, basics = roadmap.plan(gaps, gaps, scoring.credit, set(), [])
    assert len(basics) == roadmap.BASICS_MAX and all(b.item.weight >= roadmap.BASICS_MIN_WEIGHT for b in basics)
    assert not main and {m.item.name for m in later} == set(names) - {b.item.name for b in basics}
