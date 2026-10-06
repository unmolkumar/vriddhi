"""The general engine: role resolution, evidence, matching, score, verdict, alternatives, gaps, roadmap, match_text.

Occupations are prepared once per process (filters, provenance, requirement vectors from the disk cache); a request
encodes only its own evidence. Module 1 is reached only through the client (REST or fixture).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from src.general import inference, roadmap, scoring
from src.general.embeddings import Encoder, cosine
from src.general.evidence import EvidenceUnit, from_profile, from_skills, from_text
from src.general.m1_client import M1Client, M1Error
from src.general.matcher import RequirementMatch, classify, coverage, requirement_skill_id, score
from src.general.requirements import (
    DEFAULT_LEVEL, SCORED_TYPES, FilterReport, RequirementItem, domain_texts, normalise,
)
from src.general.schemas import (
    CloseAlternative, DrawsOnItem, EvidenceRef, FitIndicatorItem, GapAnalysisV2Request, GapAnalysisV2Response,
    GeneralRoadmap, MatchTextRequest, MatchTextResponse, ProvenanceSummary, RequirementResult, RoadmapItem,
    RoleOption, RoleResolution, ScoreBreakdown, TypeScore, Verdict, WorkActivityItem,
)
from src.models.schemas import HourRange
from src.parsers.section_segmenter import extract_work_history, segment

log = logging.getLogger(__name__)

RELATED_LIMIT = 5              # related occupations scored for alternatives and the over-qualified check
ALTERNATIVE_MARGIN = 0.05      # an alternative within this of the target's score (or above it) is returned
STRENGTHS_TOP = 10
GAPS_TOP = 15
SEARCH_K = 5
JOB_TEXT_BLEND = 0.6           # match_text: weight of the job's own text vs the occupation's core requirements
JOB_MIN_WORDS = 3              # job-text clauses shorter than this aren't requirements ("Pune", "Full time")
JOB_MAX_REQUIREMENTS = 60
MATCH_TEXT_TOP = 15
CURATED_NOTE = ("Curated rows are hand-written in module 1 and count at half weight until module 1 replaces them "
                "with O*NET or posting data.")


class RoleNotResolved(Exception):
    pass


@dataclass
class OccupationData:
    soc: str
    title: str
    profile: dict
    items: list[RequirementItem]            # every layer
    core: list[RequirementItem]
    report: FilterReport
    vectors: np.ndarray                     # of core
    version: str
    vec_by_id: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def job_zone(self) -> int | None:
        return (self.profile.get("job_zone") or {}).get("job_zone")


def prepare_occupation(soc: str, profile: dict, rows: list[dict], encoder: Encoder, version: str) -> OccupationData:
    """Filter and weight the rows (off-domain check against the title and domain labels) and encode the core
    requirements, cached on disk per (model, version, SOC, texts)."""
    key = f"{version}-{soc}"
    title = profile.get("title") or soc
    domain = encoder.encode_cached(f"{key}-domain", domain_texts(title, profile.get("domain"), profile.get("description")))

    def similarity(texts: list[str]) -> list[float]:
        return cosine(encoder.encode_cached(f"{key}-offdomain", texts), domain).max(axis=1).tolist()

    items, report = normalise(rows, title=title, domain_similarity=similarity)
    core = [i for i in items if i.item_type in SCORED_TYPES]
    vectors = encoder.encode_cached(key, [i.text for i in core])
    return OccupationData(soc, title, profile, items, core, report, vectors, version,
                          {i.item_id: vectors[n] for n, i in enumerate(core)})


def evidence_ref(m: RequirementMatch) -> EvidenceRef | None:
    if not m.evidence_text or m.status == "missing":
        return None
    return EvidenceRef(text=m.evidence_text, evidence_type=m.evidence_type, section=m.evidence_section or "",
                       span=m.evidence_span, context_span=m.evidence_context_span)


def advice(m: RequirementMatch) -> str | None:
    if m.status == "met" and m.evidence_type in scoring.WEAK_EVIDENCE:
        return (f"You mention \"{m.evidence_text}\", but nothing in your work or projects shows it. "
                f"Add an example of where you did this.")
    return None


def result(m: RequirementMatch) -> RequirementResult:
    return RequirementResult(
        requirement=m.item.name, item_type=m.item.item_type, item_id=m.item.item_id, status=m.status,
        similarity=m.similarity, credit=round(scoring.credit(m), 4), weight=round(m.item.weight, 4),
        required_level=m.item.level, provenance=m.item.provenance, reason=m.reason, evidence=evidence_ref(m),
        advice=advice(m), flags=m.item.flags)


class GeneralEngine:
    def __init__(self, client=None, encoder: Encoder | None = None):
        self.client = client or M1Client()
        self.encoder = encoder or Encoder()
        self._occupations: dict[str, OccupationData] = {}

    # --- module 1 -----------------------------------------------------------------------------
    def version(self, soc: str | None = None) -> str:
        if soc and hasattr(self.client, "version_of"):
            return self.client.version_of(soc)
        return self.client.version()

    def occupation(self, soc: str) -> OccupationData:
        if soc not in self._occupations:
            profile = self.client.profile(soc)
            if not profile:
                raise M1Error("not_found", f"module 1 has no occupation {soc}", 404)
            self._occupations[soc] = prepare_occupation(soc, profile, self.client.requirements(soc), self.encoder,
                                                        self.version(soc))
        return self._occupations[soc]

    def resolve(self, target_role: str | None, soc_code: str | None) -> RoleResolution:
        if soc_code:
            occ = self.occupation(soc_code)
            return RoleResolution(soc_code=soc_code, title=occ.title, confidence=1.0, method="soc_code",
                                  low_confidence=False)
        res = self.client.search(target_role, SEARCH_K)
        if not res.matches:
            raise RoleNotResolved(f"No occupation found for '{target_role}'. Try another title or send soc_code.")
        top = res.matches[0]
        others = [RoleOption(soc_code=m.soc_code, title=m.title, confidence=m.confidence) for m in res.matches[1:4]]
        return RoleResolution(soc_code=top.soc_code, title=top.title, confidence=top.confidence, method=top.method,
                              low_confidence=res.low_confidence, did_you_mean=others if res.low_confidence else [])

    # --- evidence -----------------------------------------------------------------------------
    def evidence(self, req) -> tuple[list[EvidenceUnit], float | None, list[str]]:
        units, warnings = [], []
        if req.free_text and req.free_text.strip():
            units += from_text(req.free_text)
        units += from_skills([s for s in req.skills if s.strip()])
        if req.profile:
            units += from_profile(req.profile)
        years = req.experience_years
        if years is None and req.profile:
            years = req.profile.experience_years or None
        if years is None and req.free_text:
            experience = segment(req.free_text).get("experience")
            if experience:
                _, parsed, _ = extract_work_history(experience)
                years = parsed or None
        if years is None:
            warnings.append("Experience years unknown (send experience_years); no experience adjustment was made.")
        if not units:
            warnings.append("No usable evidence was found in the input.")
        return units, years, warnings

    def _match(self, occ: OccupationData, units: list[EvidenceUnit], unit_vectors: np.ndarray) -> list[RequirementMatch]:
        return classify(occ.core, units, score(occ.core, units, self.encoder, req_vectors=occ.vectors,
                                               unit_vectors=unit_vectors))

    def _score(self, occ: OccupationData, units, unit_vectors, years) -> tuple[float, list[RequirementMatch], float]:
        matches = self._match(occ, units, unit_vectors)
        factor = scoring.experience_factor(years, scoring.experience_band(occ.profile))
        return scoring.skill_score(matches) * factor, matches, factor

    # --- gap analysis ---------------------------------------------------------------------------
    def analyze(self, req: GapAnalysisV2Request) -> GapAnalysisV2Response:
        resolution = self.resolve(req.target_role, req.soc_code)
        occ = self.occupation(resolution.soc_code)
        units, years, warnings = self.evidence(req)
        unit_vectors = self.encoder.encode([u.text for u in units])
        band = scoring.experience_band(occ.profile)
        match_score, matches, factor = self._score(occ, units, unit_vectors, years)
        skill = scoring.skill_score(matches)

        alternatives, better_fit = self._alternatives(occ, units, unit_vectors, years, match_score, warnings)
        if resolution.low_confidence:
            alternatives += [CloseAlternative(soc_code=o.soc_code, title=o.title, score=o.confidence, source="search",
                                              message=f"Did you mean {o.title}?") for o in resolution.did_you_mean]
        label, reason = scoring.verdict(match_score, years, band, better_fit)
        suggested = RoleOption(soc_code=better_fit[0], title=better_fit[1], confidence=round(better_fit[2], 4)) \
            if label == "over_qualified" and better_fit else None

        met = sorted((m for m in matches if m.status == "met"), key=lambda m: -m.item.weight * scoring.credit(m))
        gaps = sorted((m for m in matches if m.status != "met"), key=lambda m: -m.item.weight)
        generic = inference.infer(occ.items, matches, units, self.encoder)
        return GapAnalysisV2Response(
            resolution=resolution, match_score=round(match_score, 4),
            verdict=Verdict(label=label, reason=reason, suggested_role=suggested),
            score_breakdown=ScoreBreakdown(
                skill_score=round(skill, 4), by_type={t: TypeScore(**v) for t, v in scoring.by_type(matches).items()},
                experience_years=years, experience_band=(band.low, band.high) if band else None,
                experience_band_source=band.source if band else "none", experience_factor=round(factor, 4)),
            strengths=[result(m) for m in met[:STRENGTHS_TOP]],
            gaps=[result(m) for m in gaps[:GAPS_TOP]], gaps_total=len(gaps),
            draws_on=[DrawsOnItem(name=d.item.name, item_type=d.item.item_type, importance=d.item.importance,
                                  inferred=d.inferred, support=d.support) for d in generic.draws_on],
            work_activities=[WorkActivityItem(name=w.item.name, status=w.status, via=w.via)
                             for w in generic.work_activities],
            fit_indicators=[FitIndicatorItem(**f.model_dump()) for f in generic.fit_indicators],
            close_alternatives=alternatives,
            roadmap=self._roadmap(occ, gaps, units, req.hours_per_week),
            provenance_summary=self._provenance(occ),
            m1_version=occ.version, warnings=warnings)

    def _alternatives(self, occ, units, unit_vectors, years, target_score, warnings):
        try:
            related = self.client.related(occ.soc, limit=RELATED_LIMIT)
        except M1Error as e:
            warnings.append(f"Related occupations unavailable ({e.code}).")
            return [], None
        out, better = [], None
        for r in related[:RELATED_LIMIT]:
            soc = r.get("related_soc_code")
            try:
                other = self.occupation(soc)
            except M1Error:
                continue                    # not in module 1 (or the fixture): skip quietly
            s, _, _ = self._score(other, units, unit_vectors, years)
            if s >= target_score - ALTERNATIVE_MARGIN:
                stronger = s > target_score
                out.append(CloseAlternative(
                    soc_code=soc, title=other.title, score=round(s, 4), source="related",
                    message=(f"You may be an even stronger fit for {other.title} ({s:.2f})." if stronger
                             else f"You're also a close fit for {other.title} ({s:.2f}).")))
            if (other.job_zone or 0) > (occ.job_zone or 0) and s >= scoring.GOOD_FIT_THRESHOLD:
                if better is None or s > better[2]:
                    better = (soc, other.title, s)
        return sorted(out, key=lambda a: -a.score), better

    def _roadmap(self, occ: OccupationData, gaps: list[RequirementMatch], units: list[EvidenceUnit],
                 hours_per_week: float | None) -> GeneralRoadmap:
        known = roadmap.known_skill_ids({sid for u in units for sid in u.skill_ids})
        ideas = roadmap.practice_ideas(gaps, occ.vec_by_id)
        items = []
        for step, m in enumerate(roadmap.order(gaps, known)[:roadmap.ROADMAP_MAX_ITEMS], start=1):
            h = roadmap.hours(m, occ.job_zone)
            items.append(RoadmapItem(step=step, requirement=m.item.name, item_type=m.item.item_type, status=m.status,
                                     provenance=m.item.provenance, weight=round(m.item.weight, 4),
                                     prerequisites=roadmap.prerequisites(m, known),
                                     practice_ideas=ideas.get(m.item.item_id, []), hours=h,
                                     weeks=roadmap.weeks(h, hours_per_week)))
        total = HourRange(low=sum(i.hours.low for i in items), high=sum(i.hours.high for i in items))
        return GeneralRoadmap(items=items, total_hours=total, total_weeks=roadmap.weeks(total, hours_per_week),
                              hours_per_week=hours_per_week, note=roadmap.ROADMAP_NOTE)

    @staticmethod
    def _provenance(occ: OccupationData) -> ProvenanceSummary:
        counts: dict[str, int] = {}
        weights: dict[str, float] = {}
        for i in occ.core:
            counts[i.provenance] = counts.get(i.provenance, 0) + 1
            weights[i.provenance] = weights.get(i.provenance, 0.0) + i.weight
        total = sum(weights.values()) or 1.0
        note = CURATED_NOTE if "curated" in counts else "No curated rows for this occupation."
        return ProvenanceSummary(scored_items=counts, weight_share={k: round(v / total, 4) for k, v in weights.items()},
                                 note=note)

    # --- match_text (module 3) ------------------------------------------------------------------
    def match_text(self, req: MatchTextRequest) -> MatchTextResponse:
        units, years, warnings = self.evidence(req)
        unit_vectors = self.encoder.encode([u.text for u in units])
        job_items = job_requirements(req.job_text)
        if not job_items:
            warnings.append("No requirement-like sentences found in job_text.")
        job_matches = classify(job_items, units, score(job_items, units, self.encoder, unit_vectors=unit_vectors))
        job_score = coverage(job_matches, credit=scoring.credit) if job_matches else 0.0
        occ_score, occ_matches, occ, version = None, [], None, None
        if req.soc_code:
            occ = self.occupation(req.soc_code)
            occ_score, occ_matches, _ = self._score(occ, units, unit_vectors, years)
            version = occ.version
        blend = JOB_TEXT_BLEND if occ_score is not None else 1.0
        total = blend * job_score + (1 - blend) * (occ_score or 0.0)
        everything = job_matches + occ_matches
        met = sorted((m for m in everything if m.status == "met"), key=lambda m: -m.item.weight * scoring.credit(m))
        missing = sorted((m for m in everything if m.status != "met"), key=lambda m: -m.item.weight)
        return MatchTextResponse(
            match_score=round(total, 4), job_text_score=round(job_score, 4),
            occupation_score=round(occ_score, 4) if occ_score is not None else None, blend=blend,
            soc_code=req.soc_code, occupation_title=occ.title if occ else None,
            met=[result(m) for m in met[:MATCH_TEXT_TOP]], missing=[result(m) for m in missing[:MATCH_TEXT_TOP]],
            job_requirements=len(job_items), m1_version=version, warnings=warnings)


def job_requirements(job_text: str) -> list[RequirementItem]:
    """Clause-split job text as task-like requirements (provenance 'job_text'). A sentence that was split into
    clauses is replaced by its clauses."""
    units = from_text(job_text)
    split = {u.context_span for u in units if u.context_span}
    seen, items = set(), []
    for u in units:
        if u.span in split or len(u.text.split()) < JOB_MIN_WORDS or u.text.lower() in seen:
            continue
        seen.add(u.text.lower())
        items.append(RequirementItem(
            soc="job", item_type="task", item_id=f"job:{len(items)}", name=u.text, description="", importance=1.0,
            level=DEFAULT_LEVEL["task"], source="job_text", provenance="job_text", reliable=True, layer="core",
            weight=1.0))
        if len(items) >= JOB_MAX_REQUIREMENTS:
            break
    return items


__all__ = ["GeneralEngine", "RoleNotResolved", "prepare_occupation", "job_requirements", "requirement_skill_id"]
