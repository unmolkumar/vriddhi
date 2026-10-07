"""v2 job search for any occupation: resolve the role (module 1), build queries, fetch listings, judge each
listing's relevance, match the whole page through module 2's match_texts, rank, and find the unlocks.

Fallbacks: module 1 down -> the raw phrase as the only query, no SOC, relevance 'unknown', warning; module 2 down ->
keyword overlap matching, warning. Never raises for a dependency being down. WORKING.md §15.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from src.engines.job_store import JobStore
from src.engines.locations import expand_cities, normalise_city
from src.engines.m2_client import M2Unavailable
from src.engines.matching import NEUTRAL, experience_component, location_component
from src.engines.ranking import recency
from src.general import m1, m2, relevance
from src.general.fetch import fetch
from src.general.schemas import (
    DroppedListing, ExperienceFit, JobMatchV2, JobSearchV2Request, JobSearchV2Response, JobV2Result, ListingRelevance,
    RelevanceSummary, RequirementGap, RoleOptionV2, RoleResolutionV2, UnlockV2,
)
from src.models.schemas import ExperienceBand, Job

LOW_CONFIDENCE = 0.75                 # role resolution below this asks "did you mean"
SHORT_DESCRIPTION_CHARS = 200         # the listing's title is prepended to shorter descriptions
MIN_JOB_REQUIREMENTS = 3              # fewer requirement clauses than this: match_confidence 'low'
TOP_MISSING = 5
TOP_MET = 5
# v2 match classification on the re-blended match (tuned on the tuning searches, WORKING.md §15): Good is the
# floor that best separates full practitioners' on-target listings from beginners' and other fields'; Strong is
# module 2's own good-fit threshold; Partial is half of Good.
THRESHOLDS = {"Strong": 0.29, "Good": 0.12, "Partial": 0.06}
RANK_WEIGHTS = {"match": 0.55, "relevance": 0.15, "experience": 0.10, "location": 0.10, "recency": 0.10}
RELEVANCE_SCORE = {"on_target": 1.0, "adjacent": 0.5, "unknown": 0.5}
# Experience band: the posting's, else module 1's Indian band when reliable, else the job zone (as module 2).
INDIA_BAND_MIN_SAMPLE = 30
INDIA_BAND_MIN_YEAR = 2023
JOB_ZONE_YEARS = {1: (0.0, 1.0), 2: (0.0, 2.0), 3: (1.0, 4.0), 4: (2.0, 6.0), 5: (4.0, 10.0)}
OPEN_ENDED_EXTRA_YEARS = 3.0
# Unlocks.
UNLOCKS_TOP = 5
UNLOCK_SIMILAR = 0.8                  # word-set overlap at which two requirement texts count as one
UNLOCK_SKIP_TYPES = {"ability", "skill", "knowledge", "work_activity"}
BASIC_SOFTWARE = re.compile(r"\b(microsoft (word|excel|outlook|access|powerpoint|office|windows|teams|exchange|sharepoint)|"
                            r"office suite|google (docs|sheets|drive)|email|e-mail|web browser|operating system)\b", re.I)
KEYWORD_SCORE_CAP = 15                # keyword fallback: shared words / min(job words, this)
# Job text quality (tuning searches): Adzuna sends ~500 characters, often all company introduction. The text is
# cleaned of non-requirements, and the match leans on the occupation's requirements when few clauses are left:
#   match = b x job_text_score + (1 - b) x occupation_score,  b = JOB_TEXT_BLEND_MAX x min(1, clauses / FULL_CLAUSES)
JOB_TEXT_BLEND_MAX = 0.6              # module 2's own blend for a full job text
FULL_CLAUSES = 8
M2_BLEND = 0.6                        # module 2's JOB_TEXT_BLEND, to rescale its score gains
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\s*[\n\r\u2022\u25aa\u25cf]+\s*|\s+-\s+(?=[A-Z])")
_NOT_REQUIREMENT = re.compile(
    r"\b(we|our|us|you'?ll|join(ing)?|company|client'?s?|employer|opportunit(y|ies)|apply|applicants?|salary|ctc|lpa|"
    r"stipend|inr|rs\.?|job ?type|location|work ?mode|shift timings?|benefits?|perks|equal opportunity|"
    r"diversity|interview|walk-?in|contact|email|whatsapp|career area|about (the )?(role|company|us))\b", re.I)
_LABEL = re.compile(r"^[A-Za-z &/]{2,40}:\s*")


@dataclass
class Clients:
    providers: httpx.Client | None = None
    m1: httpx.Client | None = None
    m2: httpx.Client | None = None
    store: JobStore | None = None
    titles: m1.TitleCache | None = None
    now: datetime | None = None


@dataclass
class Target:
    soc: str | None
    title: str | None
    close: set[str] = field(default_factory=set)
    related: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    band: tuple[float, float] | None = None
    band_source: str = "unknown"


def classification(score: float) -> str:
    return next((label for label in ("Strong", "Good", "Partial") if score >= THRESHOLDS[label]), "Weak")


def india_band(profile: dict | None) -> tuple[tuple[float, float] | None, str]:
    """Module 1's Indian band only with enough recent postings, else the job zone (same rule as module 2)."""
    if not profile:
        return None, "unknown"
    exp = profile.get("indian_experience") or {}
    years = [int(y) for y in re.findall(r"\d{4}", str(exp.get("years_covered") or ""))]
    if (not exp.get("fallback_to_job_zone") and exp.get("typical_max") is not None
            and (exp.get("sample_size") or 0) >= INDIA_BAND_MIN_SAMPLE and years and max(years) >= INDIA_BAND_MIN_YEAR):
        return (float(exp.get("typical_min") or 0.0), float(exp["typical_max"])), "india_postings"
    zone = (profile.get("job_zone") or {}).get("job_zone")
    return (JOB_ZONE_YEARS[zone], "job_zone") if zone in JOB_ZONE_YEARS else (None, "unknown")


def clean_description(text: str) -> str:
    """The listing's requirement-like sentences: without company voice, salary/location/job-type lines, bare labels
    ('Responsibilities:'), and the cut-off last fragment of a truncated description."""
    text = (text or "").strip()
    pieces = [p.strip() for p in _SENTENCES.split(text) if p and p.strip()]
    if pieces and (text.endswith(("…", "�", "...")) or not re.search(r"[.!?)]$", text)):
        pieces = pieces[:-1]                                           # truncated mid-sentence
    keep = []
    for piece in pieces:
        p = _LABEL.sub("", piece).strip(" -:")
        if len(p.split()) >= 3 and not _NOT_REQUIREMENT.search(piece):
            keep.append(p if p.endswith((".", "!", "?")) else p + ".")
    return " ".join(keep)


def job_text(job: Job) -> str:
    """What module 2 matches: the cleaned description, with the title in front when that is short."""
    desc = clean_description(job.description)
    return f"{job.title}. {desc}" if len(desc) < SHORT_DESCRIPTION_CHARS else desc


def blend(result: dict) -> float:
    """Weight of the job text in the v2 match (see JOB_TEXT_BLEND_MAX); 1.0 when there's no occupation score."""
    if result.get("occupation_score") is None:
        return 1.0
    return JOB_TEXT_BLEND_MAX * min(1.0, result["job_requirements"] / FULL_CLAUSES)


def reblend(result: dict) -> dict:
    """Module 2's result with match_score and every score_gain_if_met re-weighted to blend(result)."""
    b = blend(result)
    if result.get("occupation_score") is None:
        return result
    score = b * result["job_text_score"] + (1 - b) * result["occupation_score"]
    scale = {True: b / M2_BLEND, False: (1 - b) / (1 - M2_BLEND)}

    def rescale(items):
        return [{**x, "score_gain_if_met": (round(x["score_gain_if_met"] * scale[x["provenance"] == "job_text"], 4)
                                            if x.get("score_gain_if_met") is not None else None)} for x in items]
    missing = sorted(rescale(result["missing"]), key=lambda x: -(x["score_gain_if_met"] or 0))
    return {**result, "match_score": round(score, 4), "blend": round(b, 4), "missing": missing}


def candidate_years(req: JobSearchV2Request) -> float | None:
    if req.experience_years is not None:
        return req.experience_years
    years = (req.profile or {}).get("experience_years")
    return float(years) if years else None


def evidence(req: JobSearchV2Request) -> dict:
    out = {k: v for k, v in (("free_text", req.free_text), ("skills", req.skills), ("profile", req.profile),
                             ("experience_years", req.experience_years)) if v not in (None, [], "")}
    return out


# --- role resolution --------------------------------------------------------------------------------------------

def resolve(req: JobSearchV2Request, c: Clients, warnings: list[str]) -> tuple[RoleResolutionV2, Target, list[str], bool]:
    """(resolution, target, queries, module 1 available)."""
    phrase = (req.target_role or "").strip() or None
    try:
        if req.soc_code:
            prof = m1.profile(req.soc_code, client=c.m1)
            if prof is None:
                warnings.append(f"Module 1 has no occupation {req.soc_code}; searching with the phrase only.")
                return (RoleResolutionV2(query=phrase, soc_code=None, title=None, confidence=0.0, low_confidence=True),
                        Target(None, None), [phrase] if phrase else [], True)
            matches = [m1.Match(req.soc_code, prof.get("title") or req.soc_code, 1.0, "soc_code")]
        else:
            matches = m1.search(phrase, client=c.m1)
            prof = None
        if not matches:
            warnings.append(f"Module 1 found no occupation for '{phrase}'; searching with the phrase only.")
            return (RoleResolutionV2(query=phrase, soc_code=None, title=None, confidence=0.0, low_confidence=True),
                    Target(None, None), [phrase], True)
        top = matches[0]
        low = top.confidence < LOW_CONFIDENCE
        resolution = RoleResolutionV2(
            query=phrase, soc_code=top.soc_code, title=top.title, confidence=top.confidence, low_confidence=low,
            did_you_mean=[RoleOptionV2(soc_code=m.soc_code, title=m.title, confidence=m.confidence)
                          for m in matches[1:4]] if low else [])
        prof = prof or m1.profile(top.soc_code, client=c.m1)
        rel = m1.related(top.soc_code, client=c.m1)
        alias_matches = matches + m1.search(relevance.board_title(top.title), client=c.m1)
        queries = relevance.build_queries(phrase, top.title, alias_matches, top.soc_code)
        close = relevance.close_socs(top.soc_code, rel)
        band, source = india_band(prof)
        target = Target(top.soc_code, top.title, close, close | {r["related_soc_code"] for r in rel},
                        relevance.target_terms(queries), band, source)
        return resolution, target, queries, True
    except m1.M1Unavailable as e:
        warnings.append(f"Module 1 (occupations) is unreachable ({e}); searching with the phrase only, without "
                        "occupation matching or relevance checks.")
        return (RoleResolutionV2(query=phrase, soc_code=req.soc_code, title=None, confidence=0.0, low_confidence=True),
                Target(req.soc_code, None), [phrase] if phrase else [], False)


# --- matching ---------------------------------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#]{2,}", (text or "").lower())} - relevance.STOPWORDS


def keyword_match(req: JobSearchV2Request, job: Job) -> JobMatchV2:
    """Module 2 down: share of the job's words found in the user's evidence (capped)."""
    user = _words(" ".join([req.free_text or "", " ".join(req.skills),
                            " ".join(s.get("name", "") for s in (req.profile or {}).get("skills", []))]))
    job_words = _words(job_text(job))
    shared = user & job_words
    score = min(1.0, len(shared) / max(1, min(len(job_words), KEYWORD_SCORE_CAP)))
    return JobMatchV2(method="keywords", match_score=round(score, 4), classification=classification(score), soc_code=None,
                      match_confidence="low", met=sorted(shared)[:TOP_MET])


def m2_match(result: dict, job: Job) -> JobMatchV2:
    low = len((job.description or "").strip()) < SHORT_DESCRIPTION_CHARS or result["job_requirements"] < MIN_JOB_REQUIREMENTS
    missing = [RequirementGap(requirement=x["requirement"], requirement_id=x["requirement_id"], item_type=x["item_type"],
                              status=x["status"], score_gain_if_met=x.get("score_gain_if_met"))
               for x in result["missing"][:TOP_MISSING]]
    return JobMatchV2(method="m2_match_texts", match_score=result["match_score"],
                      classification=classification(result["match_score"]), job_text_score=result["job_text_score"],
                      occupation_score=result["occupation_score"], soc_code=result["soc_code"],
                      match_confidence="low" if low else "ok", job_requirements=result["job_requirements"],
                      met=[x["requirement"] for x in result["met"][:TOP_MET]], missing=missing)


# --- experience and ranking -------------------------------------------------------------------------------------

def experience_fit(job: Job, years: float | None, target: Target) -> ExperienceFit:
    if job.experience_min is not None:
        hi = job.experience_max if job.experience_max is not None else job.experience_min + OPEN_ENDED_EXTRA_YEARS
        band, source = (job.experience_min, max(hi, job.experience_min)), "posting"
    else:
        band, source = target.band, target.band_source
    if years is None or band is None:
        return ExperienceFit(band=band, source=source if band else "unknown", candidate_years=years, fit=NEUTRAL)
    fit = experience_component(years, ExperienceBand(min=band[0], max=band[1]))
    return ExperienceFit(band=band, source=source, candidate_years=years, fit=round(fit, 4))


def rank_components(match: JobMatchV2, rel: str, exp: ExperienceFit, job: Job, cities: set[str], now) -> dict[str, float]:
    return {"match": match.match_score, "relevance": RELEVANCE_SCORE.get(rel, 0.5), "experience": exp.fit,
            "location": location_component(job, cities), "recency": round(recency(job, now), 4)}


def rank_score(components: dict[str, float]) -> float:
    return round(sum(RANK_WEIGHTS[k] * components[k] for k in RANK_WEIGHTS), 4)


# --- unlocks ------------------------------------------------------------------------------------------------------

def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= UNLOCK_SIMILAR


def unlockable(gap: dict) -> bool:
    return (gap.get("score_gain_if_met") or 0) > 0 and gap["item_type"] not in UNLOCK_SKIP_TYPES \
        and not BASIC_SOFTWARE.search(gap["requirement"])


def phrase_for(requirement: str, item_type: str) -> str:
    if item_type in ("tech", "tool", "market_skill"):
        return f"Learning {requirement}"
    text = re.split(r"[.;]|,\s+(?:such as|including|using)\b", requirement.strip())[0].strip()
    return f"Learning to {text[0].lower() + text[1:]}" if text else f"Learning {requirement}"


def find_unlocks(scored: list[tuple[Job, JobMatchV2, list[dict]]], role: str, place: str) -> list[UnlockV2]:
    """For each requirement missing somewhere (grouped by requirement_id or near-identical text): the listings now
    below Good that meeting it would lift to Good or Strong (score + score_gain_if_met >= THRESHOLDS['Good'])."""
    groups: list[dict] = []
    for job, match, gaps in scored:
        if match.match_score >= THRESHOLDS["Good"]:
            continue
        for g in gaps:
            if not unlockable(g):
                continue
            grp = next((x for x in groups if g["requirement_id"] in x["ids"] or _similar(g["requirement"], x["text"])), None)
            if grp is None:
                grp = {"ids": set(), "text": g["requirement"], "type": g["item_type"], "jobs": {}}
                groups.append(grp)
            grp["ids"].add(g["requirement_id"])
            if match.match_score + g["score_gain_if_met"] >= THRESHOLDS["Good"]:
                grp["jobs"][job.job_id] = True
    groups = [g for g in groups if g["jobs"]]
    groups.sort(key=lambda g: (-len(g["jobs"]), g["text"]))
    out = []
    for g in groups[:UNLOCKS_TOP]:
        n = len(g["jobs"])
        out.append(UnlockV2(requirement=g["text"], requirement_ids=sorted(g["ids"]), item_type=g["type"], jobs_unlocked=n,
                            job_ids=list(g["jobs"])[:10],
                            message=f"{phrase_for(g['text'], g['type'])} would move {n} more {role} "
                                    f"job{'s' if n != 1 else ''} in {place} to a good match."))
    return out


# --- the search -----------------------------------------------------------------------------------------------

def search(req: JobSearchV2Request, c: Clients | None = None) -> JobSearchV2Response:
    c = c or Clients()
    now = c.now or datetime.now(timezone.utc)
    warnings: list[str] = []
    location = req.location or (req.profile or {}).get("location")
    cities = expand_cities(location)
    resolution, target, queries, m1_ok = resolve(req, c, warnings)
    jobs, attempts, fetch_warnings, sources, stale, oldest = fetch(
        queries, cities, store=c.store, client=c.providers, now=now, force_refresh=req.force_refresh,
        use_jsearch=req.use_jsearch)
    warnings += fetch_warnings

    # relevance
    titles: dict[str, list[m1.Match]] = {}
    if target.soc and m1_ok:
        try:
            titles = m1.resolve_titles([j.title for j in jobs], client=c.m1, cache=c.titles)
        except m1.M1Unavailable:
            warnings.append("Module 1 became unreachable while checking listing titles; relevance is unknown.")
            m1_ok = False
    rels = {j.job_id: relevance.classify(j.title, titles.get(m1.title_key(j.title), []), target.soc if m1_ok else None,
                                         target.close, target.related, target.terms) for j in jobs}
    kept = [j for j in jobs if rels[j.job_id].label != "off_target"]
    dropped = [DroppedListing(job_id=j.job_id, title=j.title, reason=rels[j.job_id].reason)
               for j in jobs if rels[j.job_id].label == "off_target"]
    order = {"on_target": 0, "adjacent": 1, "unknown": 1}
    kept.sort(key=lambda j: order[rels[j.job_id].label])                     # stable: newest first within a label
    matched_jobs, beyond = kept[:req.max_jobs], max(0, len(kept) - req.max_jobs)

    # matching (one module 2 call for the page)
    m2_ok, results = True, {}
    if matched_jobs:
        payload = []
        for n, j in enumerate(matched_jobs):        # short positional ids: provider ids can exceed module 2's limit
            r = rels[j.job_id]
            soc = r.soc_code if (r.soc_code and r.confidence >= relevance.RESOLVE_MIN_CONFIDENCE
                                 and r.soc_code in target.related) else target.soc
            payload.append({"job_id": f"j{n}", "job_text": job_text(j)[:MAX_JOB_TEXT], "job_title": j.title[:200],
                            **({"soc_code": soc} if soc else {})})
        try:
            results = {matched_jobs[int(r["job_id"][1:])].job_id: reblend(r)
                       for r in m2.match_texts(evidence(req), payload, client=c.m2)}
        except M2Unavailable as e:
            m2_ok = False
            warnings.append(f"Module 2 (matching) is unreachable ({e}); jobs are matched by keyword overlap, which "
                            "is much less reliable.")
    years = candidate_years(req)
    if years is None:
        warnings.append("Experience years unknown (send experience_years); experience fit is neutral.")
    home = {normalise_city(location)} | {normalise_city(p) for p in req.preferred_locations}
    home = {city for h in home if h for city in expand_cities(h)}
    out, scored = [], []
    for j in matched_jobs:
        res = results.get(j.job_id)
        match = m2_match(res, j) if res else keyword_match(req, j)
        rel = rels[j.job_id]
        exp = experience_fit(j, years, target)
        comps = rank_components(match, rel.label, exp, j, home, now)
        out.append((j, match, rel, exp, comps, rank_score(comps)))
        if res:
            scored.append((j, match, res["missing"]))
    posted = lambda j: j.posted_at.timestamp() if j.posted_at else 0.0  # noqa: E731
    out.sort(key=lambda t: (-t[5], -t[1].match_score, -posted(t[0]), t[0].job_id))
    results_out = [JobV2Result(
        job_id=j.job_id, title=j.title, company=j.company, location=j.location, work_mode=j.work_mode,
        employment_type=j.employment_type, posted_at=j.posted_at, source=j.source, source_url=j.source_url,
        description=j.description, salary_min=j.salary_min, salary_max=j.salary_max,
        salary_is_predicted=j.salary_is_predicted,
        relevance=ListingRelevance(label=rel.label, soc_code=rel.soc_code, occupation_title=rel.title,
                                   confidence=rel.confidence, reason=rel.reason),
        match=match, experience=exp, rank=n, rank_score=score, rank_components=comps, stale=j.stale)
        for n, (j, match, rel, exp, comps, score) in enumerate(out, start=1)]

    labels = [rels[j.job_id].label for j in jobs]
    role = (target.title or req.target_role or "matching")
    unlocks = find_unlocks(scored, relevance.board_title(role) if target.title else role, location) if m2_ok else []
    age = round((now - oldest).total_seconds() / 3600, 1) if oldest else None
    return JobSearchV2Response(
        resolution=resolution, queries=queries, cities=cities, jobs=results_out,
        relevance_summary=RelevanceSummary(
            listings=len(jobs), on_target=labels.count("on_target"), adjacent=labels.count("adjacent"),
            off_target_dropped=labels.count("off_target"), unknown=labels.count("unknown"),
            on_target_share=round(labels.count("on_target") / len(jobs), 4) if jobs else 0.0, beyond_max_jobs=beyond),
        dropped=dropped if req.include_dropped else [], unlocks=unlocks, thresholds=THRESHOLDS,
        experience_band=target.band, experience_band_source=target.band_source, provider_trace=attempts,
        sources=sources, stale=stale, fetched_at=oldest, age_hours=age, module_1_available=m1_ok,
        module_2_available=m2_ok, warnings=list(dict.fromkeys(warnings)))


MAX_JOB_TEXT = 50_000
