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


# --- C1: Q&A write-ups and the oblique / career-change verdict ------------------------------------------------

def test_questions_and_negative_answers_are_not_evidence():
    from src.general.evidence import from_text
    text = ("Q: What do you do now?\nA: I file GST returns and reconcile the bank.\n"
            "Q: Years? A: Six years.\nQ: Have you done a statutory audit?\nA: No, the CA firm did it.\nA: Not yet, learning.")
    texts = [u.text for u in from_text(text) if u.context_span is None]
    assert texts == ["I file goods & services tax returns and reconcile the bank.", "Six years."]


def test_other_role_needs_a_confident_title_in_another_field():
    from src.general.m1_client import EXTRA_FIXTURE_PATH, FIXTURE_PATH, FixtureM1Client
    e = GeneralEngine(client=FixtureM1Client(FIXTURE_PATH, EXTRA_FIXTURE_PATH), encoder=WordEncoder(), translator=None,
                      rephraser=None, normaliser=None)
    lab = ("Experience\nLaboratory Technician | Metropolis Lab, Chennai | Jun 2016 - Present\n"
           "- Collect blood samples from patients and record vital signs.\n")
    r = e.analyze(GapAnalysisV2Request(soc_code="29-1141.00", free_text=lab))
    assert r.evidence_volume.other_role and r.verdict.label == "under_skilled"
    nurse = lab.replace("Laboratory Technician | Metropolis Lab", "Staff Nurse | Apollo Hospitals")
    r = e.analyze(GapAnalysisV2Request(soc_code="29-1141.00", free_text=nurse))
    assert r.evidence_volume.other_role is None


# --- A4b (prompt 21) A1: licensing guard ----------------------------------------------------------------------

class Clinic:
    """Pharmacists, physicians, nurses: all related, sharing tasks (word-overlap encoder)."""
    TASKS = ["Review prescriptions for patients", "Give medicines to patients", "Advise patients on doses"]
    DATA = {"29-1051.00": ("Pharmacists", 5), "29-1229.00": ("Physicians, All Other", 5),
            "29-1141.00": ("Registered Nurses", 3), "29-1141.01": ("Acute Care Nurses", 3),
            "31-1131.00": ("Nursing Assistants", 4)}

    def version(self):
        return "test"

    def profile(self, soc):
        from src.general.m1_client import M1Error
        if soc not in self.DATA:
            raise M1Error("not_found", soc, 404)
        return {"soc_code": soc, "title": self.DATA[soc][0], "job_zone": {"job_zone": self.DATA[soc][1]}}

    def requirements(self, soc):
        return [{"soc_code": soc, "item_type": "task", "item_id": f"{soc}-{k}", "item_name": t, "importance_norm": 0.8,
                 "reliable": 1} for k, t in enumerate(self.TASKS)]

    def related(self, soc, limit=20):
        return [{"related_soc_code": s, "relatedness_tier": None, "index_val": i + 1}
                for i, s in enumerate(s for s in self.DATA if s != soc)]

    def search(self, q, k=5):
        from src.general.m1_client import resolution
        return resolution(q, [])


def _clinic(soc, text):
    e = GeneralEngine(client=Clinic(), encoder=WordEncoder(), translator=None, rephraser=None, normaliser=None)
    r = e.analyze(GapAnalysisV2Request(soc_code=soc, free_text=text, experience_years=3))
    return {a.soc_code for a in r.close_alternatives}, {x.soc_code: x.reason for x in r.alternatives_excluded}


WORK = "Review prescriptions for patients. Give medicines to patients. Advise patients on doses."


def test_pharmacist_is_never_offered_physician_roles():
    alts, excluded = _clinic("29-1051.00", WORK + "\nB.Pharm, 2016")
    assert "29-1229.00" not in alts and "regulated in India" in excluded["29-1229.00"]
    assert "29-1141.00" not in alts                                      # nursing needs GNM / B.Sc Nursing too


def test_nurse_with_gnm_gets_nursing_alternatives():
    alts, excluded = _clinic("29-1141.00", WORK + "\nGNM, 2018")
    assert "29-1141.01" in alts and "29-1229.00" not in alts
    alts, excluded = _clinic("29-1141.00", WORK)                         # no nursing qualification shown
    assert "29-1141.01" not in alts and "29-1141.01" in excluded


def test_mbbs_doctor_gets_physician_alternatives():
    alts, _ = _clinic("29-1051.00", WORK + "\nMBBS, AIIMS Delhi, 2012")
    assert "29-1229.00" in alts


def test_higher_job_zone_needs_a_good_fit():
    alts, excluded = _clinic("29-1141.00", "Give medicines to patients.\nGNM, 2018")      # weak evidence
    assert "31-1131.00" not in alts
    assert "Job zone 4" in excluded["31-1131.00"] or "regulated" in excluded["31-1131.00"]
