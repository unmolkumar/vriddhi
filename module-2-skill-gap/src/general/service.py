"""The general engine: role resolution, evidence, matching, score, verdict, alternatives, gaps, roadmap, match_text.

Occupations are prepared once per process (filters, provenance, requirement vectors from the disk cache); a request
encodes only its own evidence. Module 1 is reached only through the client (REST or fixture).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from src.general import inference, roadmap, scoring
from src.general.embeddings import Encoder, cosine
from src.general.evidence import (
    EvidenceUnit, Translator, from_profile, from_skills, from_text, profile_history, role_history,
)
from src.general.m1_client import M1Client, M1Error
from src.general.matcher import RequirementMatch, classify, coverage, expand, requirement_skill_id, score
from src.general.requirements import (
    DEFAULT_LEVEL, SCORED_TYPES, FilterReport, RequirementItem, domain_texts, normalise,
)
from src.general.schemas import (
    CloseAlternative, DrawsOnItem, EvidenceRef, FitIndicatorItem, GapAnalysisV2Request, GapAnalysisV2Response,
    GeneralRoadmap, LaterItem, MatchTextRequest, MatchTextResponse, NotApplicableItem, ProvenanceSummary,
    RequirementResult, RoadmapItem, RoleHistoryItem, RoleOption, RoleResolution, ScoreBreakdown, TypeScore, Verdict,
    WorkActivityItem,
)
from src.models.schemas import HourRange
from src.parsers.section_segmenter import extract_work_history, segment

log = logging.getLogger(__name__)

RELATED_LIMIT = 5              # related occupations scored for alternatives and the over-qualified check
ALTERNATIVE_MARGIN = 0.05      # an alternative within this of the target's score (or above it) is returned
STRENGTHS_TOP = 10
GAPS_TOP = 15
SEARCH_K = 5
ROLE_TITLES_MAX = 6            # past titles resolved per request
# Module 1 /related tiers close enough for a past title to imply the target's tasks (fixture stand-ins excluded).
ROLE_RELATED_TIERS = {"Primary-Short", "Primary-Long"}
JOB_TEXT_BLEND = 0.6           # match_text: weight of the job's own text vs the occupation's core requirements
JOB_MIN_WORDS = 3              # job-text clauses shorter than this aren't requirements ("Pune", "Full time")
JOB_MAX_REQUIREMENTS = 60
MATCH_TEXT_TOP = 15
NOT_APPLICABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "general" / "india_not_applicable.json"
CURATED_NOTE = ("Curated rows are hand-written in module 1 and count at half weight until module 1 replaces them "
                "with O*NET or posting data.")
_DEFAULT = object()


class RoleNotResolved(Exception):
    pass


@dataclass
class OccupationData:
    soc: str
    title: str
    profile: dict
    items: list[RequirementItem]            # every layer, minus not-applicable ones
    core: list[RequirementItem]
    report: FilterReport
    vectors: np.ndarray                     # expanded rows of core (matcher.expand)
    version: str
    starts: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))   # first row of each core item
    vec_by_id: dict[str, np.ndarray] = field(default_factory=dict)              # each core item's full-text vector
    not_applicable: list[tuple[RequirementItem, str]] = field(default_factory=list)

    @property
    def job_zone(self) -> int | None:
        return (self.profile.get("job_zone") or {}).get("job_zone")


@dataclass
class Evidence:
    units: list[EvidenceUnit]
    years: float | None
    warnings: list[str]
    history: list[tuple[str, float | None]]   # (past title, years)


@dataclass
class Role:
    title: str
    years: float | None
    soc: str
    occupation_title: str
    confidence: float


@lru_cache(maxsize=1)
def india_not_applicable() -> dict[tuple[str, str, str], str]:
    """(soc, item_type, item_id) -> reason, from data/general/india_not_applicable.json."""
    data = json.loads(NOT_APPLICABLE_PATH.read_text(encoding="utf-8"))
    return {(e["soc"], e["item_type"], str(e["item_id"])): e["reason"] for e in data["items"]}


def prepare_occupation(soc: str, profile: dict, rows: list[dict], encoder: Encoder, version: str) -> OccupationData:
    """Filter and weight the rows (off-domain check against the title and domain labels), set aside requirements
    outside India's scope of practice, and encode the core requirements with their clauses, cached on disk per
    (model, version, SOC, texts)."""
    key = f"{version}-{soc}"
    title = profile.get("title") or soc
    domain = encoder.encode_cached(f"{key}-domain", domain_texts(title, profile.get("domain"), profile.get("description")))

    def similarity(texts: list[str]) -> list[float]:
        return cosine(encoder.encode_cached(f"{key}-offdomain", texts), domain).max(axis=1).tolist()

    items, report = normalise(rows, title=title, domain_similarity=similarity)
    na = india_not_applicable()
    not_applicable = [(i, na[(soc, i.item_type, i.item_id)]) for i in items if (soc, i.item_type, i.item_id) in na]
    if not_applicable:
        report.dropped["not_applicable_in_india"] = [i.name for i, _ in not_applicable]
    skip = {id(i) for i, _ in not_applicable}
    items = [i for i in items if id(i) not in skip]
    core = [i for i in items if i.item_type in SCORED_TYPES]
    texts, starts = expand(core)
    vectors = encoder.encode_cached(f"{key}-x", texts)
    return OccupationData(soc, title, profile, items, core, report, vectors, version, starts,
                          {i.item_id: vectors[starts[n]] for n, i in enumerate(core)}, not_applicable)


def apply_role_history(matches: list[RequirementMatch], roles: list[Role]) -> list[RequirementMatch]:
    """Core tasks/DWAs with no real evidence get an implied partial credit from a past title in this occupation
    (scoring.implied_credit by years). Real evidence always wins; never 'met'."""
    if not roles:
        return matches
    known = [r.years for r in roles if r.years is not None]
    years = sum(known) if known else None
    credit = scoring.implied_credit(years)
    title = roles[0].title
    text = f"{years:g} years as {title}" if years is not None else f"Worked as {title}"
    out = []
    for m in matches:
        if m.item.item_type in scoring.IMPLIED_TYPES and m.status == "missing" and credit > 0:
            m = m.model_copy(update={"status": "partial", "reason": "implied_by_role", "implied_credit": round(credit, 4),
                                     "evidence_text": text, "evidence_type": "work", "evidence_section": "role_history",
                                     "evidence_span": None, "evidence_context_span": None})
        out.append(m)
    return out


def evidence_ref(m: RequirementMatch) -> EvidenceRef | None:
    if not m.evidence_text or m.status == "missing":
        return None
    return EvidenceRef(text=m.evidence_text, evidence_type=m.evidence_type, section=m.evidence_section or "",
                       span=m.evidence_span, context_span=m.evidence_context_span,
                       translated=m.evidence_translated, original_text=m.evidence_original)


def advice(m: RequirementMatch) -> str | None:
    if m.status == "met" and m.evidence_type in scoring.WEAK_EVIDENCE:
        return (f"You mention \"{m.evidence_text}\", but nothing in your work or projects shows it. "
                f"Add an example of where you did this.")
    if m.reason == "implied_by_role":
        return "Your past role suggests this, but your description doesn't show it. Add an example of where you did it."
    return None


def result(m: RequirementMatch) -> RequirementResult:
    return RequirementResult(
        requirement=m.item.name, item_type=m.item.item_type, item_id=m.item.item_id, status=m.status,
        similarity=m.similarity, credit=round(scoring.credit(m), 4), weight=round(m.item.weight, 4),
        required_level=m.item.level, provenance=m.item.provenance, reason=m.reason, evidence=evidence_ref(m),
        advice=advice(m), flags=m.item.flags)


class GeneralEngine:
    def __init__(self, client=None, encoder: Encoder | None = None, translator: Translator | None = _DEFAULT):
        from src.general.translate import default_translator

        self.client = client or M1Client()
        self.encoder = encoder or Encoder()
        self.translator = default_translator() if translator is _DEFAULT else translator
        self._occupations: dict[str, OccupationData] = {}
        self._titles: dict[str, Role | None] = {}
        self._related: dict[str, set[str]] = {}

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

    def prewarm(self, socs: list[str], with_related: bool = True) -> list[str]:
        """Prepare occupations (and their related ones) ahead of the first request. Returns the SOCs warmed."""
        done = []
        for soc in socs:
            try:
                self.occupation(soc)
                done.append(soc)
                if with_related:
                    for r in self.client.related(soc, limit=RELATED_LIMIT)[:RELATED_LIMIT]:
                        try:
                            self.occupation(r.get("related_soc_code"))
                            done.append(r.get("related_soc_code"))
                        except M1Error:
                            continue
            except M1Error as e:
                log.warning("prewarm %s skipped: %s", soc, e.code)
        return done

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

    # --- evidence and role history -------------------------------------------------------------
    def evidence(self, req) -> Evidence:
        units, warnings, history = [], [], []
        if req.free_text and req.free_text.strip():
            units += from_text(req.free_text, translator=self.translator, warnings=warnings)
            history += role_history(req.free_text)
        units += from_skills([s for s in req.skills if s.strip()])
        if req.profile:
            units += from_profile(req.profile)
            history += profile_history(req.profile)
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
        if not any(u.matchable for u in units):
            warnings.append("No usable evidence was found in the input.")
        return Evidence(units, years, warnings, history)

    def _resolve_title(self, title: str) -> Role | None:
        key = title.lower().strip()
        if key not in self._titles:
            role = None
            try:
                res = self.client.search(title, 1)
                if res.matches and res.matches[0].confidence >= scoring.ROLE_MIN_CONFIDENCE:
                    m = res.matches[0]
                    role = Role(title, None, m.soc_code, m.title, m.confidence)
            except M1Error:
                pass
            self._titles[key] = role
        return self._titles[key]

    def roles(self, history: list[tuple[str, float | None]]) -> list[Role]:
        out = []
        for title, years in history[:ROLE_TITLES_MAX]:
            role = self._resolve_title(title)
            if role:
                out.append(Role(title, years, role.soc, role.occupation_title, role.confidence))
        return out

    def _close_to(self, soc: str) -> set[str]:
        """The SOC and its closely related occupations (ROLE_RELATED_TIERS), for role history."""
        if soc not in self._related:
            close = {soc}
            try:
                close |= {r["related_soc_code"] for r in self.client.related(soc, limit=RELATED_LIMIT)
                          if r.get("relatedness_tier") in ROLE_RELATED_TIERS}
            except M1Error:
                pass
            self._related[soc] = close
        return self._related[soc]

    def roles_for(self, occ: OccupationData, roles: list[Role]) -> list[Role]:
        return [r for r in roles if occ.soc in self._close_to(r.soc)]

    # --- scoring ---------------------------------------------------------------------------------
    def _match(self, occ: OccupationData, units: list[EvidenceUnit], unit_vectors: np.ndarray,
               roles: list[Role]) -> list[RequirementMatch]:
        matches = classify(occ.core, units, score(occ.core, units, self.encoder, req_vectors=occ.vectors,
                                                  starts=occ.starts, unit_vectors=unit_vectors))
        return apply_role_history(matches, self.roles_for(occ, roles))

    def _score(self, occ, units, unit_vectors, years, roles) -> tuple[float, list[RequirementMatch], float]:
        matches = self._match(occ, units, unit_vectors, roles)
        factor = scoring.experience_factor(years, scoring.experience_band(occ.profile))
        return scoring.skill_score(matches) * factor, matches, factor

    # --- gap analysis ---------------------------------------------------------------------------
    def analyze(self, req: GapAnalysisV2Request) -> GapAnalysisV2Response:
        resolution = self.resolve(req.target_role, req.soc_code)
        occ = self.occupation(resolution.soc_code)
        ev = self.evidence(req)
        units, years, warnings = ev.units, ev.years, ev.warnings
        roles = self.roles(ev.history)
        unit_vectors = self.encoder.encode([u.text for u in units])
        band = scoring.experience_band(occ.profile)
        match_score, matches, factor = self._score(occ, units, unit_vectors, years, roles)
        skill = scoring.skill_score(matches)

        alternatives, better_fit = self._alternatives(occ, units, unit_vectors, years, roles, match_score, warnings)
        if resolution.low_confidence:
            alternatives += [CloseAlternative(soc_code=o.soc_code, title=o.title, score=o.confidence, source="search",
                                              message=f"Did you mean {o.title}?") for o in resolution.did_you_mean]
        label, reason = scoring.verdict(match_score, years, band, better_fit)
        suggested = RoleOption(soc_code=better_fit[0], title=better_fit[1], confidence=round(better_fit[2], 4)) \
            if label == "over_qualified" and better_fit else None
        percent = scoring.fit_percent(match_score)

        met = sorted((m for m in matches if m.status == "met"), key=lambda m: -m.item.weight * scoring.credit(m))
        gaps = sorted((m for m in matches if m.status != "met"), key=lambda m: -m.item.weight)
        generic = inference.infer(occ.items, matches, units, self.encoder)
        return GapAnalysisV2Response(
            resolution=resolution, match_score=round(match_score, 4), fit_percent=percent,
            fit_label=scoring.fit_label(percent),
            verdict=Verdict(label=label, reason=reason, suggested_role=suggested),
            score_breakdown=ScoreBreakdown(
                skill_score=round(skill, 4), by_type={t: TypeScore(**v) for t, v in scoring.by_type(matches).items()},
                experience_years=years, experience_band=(band.low, band.high) if band else None,
                experience_band_source=band.source if band else "none", experience_factor=round(factor, 4)),
            strengths=[result(m) for m in met[:STRENGTHS_TOP]],
            gaps=[result(m) for m in gaps[:GAPS_TOP]], gaps_total=len(gaps),
            not_applicable_in_india=[NotApplicableItem(requirement=i.name, item_type=i.item_type, reason=r)
                                     for i, r in occ.not_applicable],
            role_history=[RoleHistoryItem(title=r.title, years=r.years, soc_code=r.soc,
                                          occupation_title=r.occupation_title, confidence=r.confidence,
                                          applies_to_target=r in self.roles_for(occ, roles)) for r in roles],
            draws_on=[DrawsOnItem(name=d.item.name, item_type=d.item.item_type, importance=d.item.importance,
                                  inferred=d.inferred, support=d.support) for d in generic.draws_on],
            work_activities=[WorkActivityItem(name=w.item.name, status=w.status, via=w.via)
                             for w in generic.work_activities],
            fit_indicators=[FitIndicatorItem(**f.model_dump()) for f in generic.fit_indicators],
            close_alternatives=alternatives,
            roadmap=self._roadmap(occ, gaps, units, req.hours_per_week),
            provenance_summary=self._provenance(occ),
            m1_version=occ.version, warnings=warnings)

    def _alternatives(self, occ, units, unit_vectors, years, roles, target_score, warnings):
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
            s, _, _ = self._score(other, units, unit_vectors, years, roles)
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
        """Main roadmap: the ROADMAP_MAX_ITEMS heaviest gaps (role-implied ones after the rest); the remainder goes
        to `later`. Totals cover the main roadmap only."""
        known = roadmap.known_skill_ids({sid for u in units if u.matchable for sid in u.skill_ids})
        ideas = roadmap.practice_ideas(gaps, occ.vec_by_id)
        real = [m for m in gaps if m.reason != "implied_by_role"]
        implied = [m for m in gaps if m.reason == "implied_by_role"]
        ordered = roadmap.order(real, known) + roadmap.order(implied, known)
        main, rest = ordered[:roadmap.ROADMAP_MAX_ITEMS], ordered[roadmap.ROADMAP_MAX_ITEMS:]
        items = []
        for step, m in enumerate(main, start=1):
            h = roadmap.hours(m, occ.job_zone)
            items.append(RoadmapItem(step=step, requirement=m.item.name, item_type=m.item.item_type, status=m.status,
                                     provenance=m.item.provenance, weight=round(m.item.weight, 4),
                                     implied_by_role=m.reason == "implied_by_role",
                                     prerequisites=roadmap.prerequisites(m, known),
                                     practice_ideas=ideas.get(m.item.item_id, []), hours=h,
                                     weeks=roadmap.weeks(h, hours_per_week)))
        total = HourRange(low=sum(i.hours.low for i in items), high=sum(i.hours.high for i in items))
        later = [LaterItem(requirement=m.item.name, item_type=m.item.item_type, status=m.status,
                           weight=round(m.item.weight, 4)) for m in rest]
        return GeneralRoadmap(items=items, later=later, total_hours=total,
                              total_weeks=roadmap.weeks(total, hours_per_week), hours_per_week=hours_per_week,
                              note=roadmap.ROADMAP_NOTE)

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
        ev = self.evidence(req)
        units, years, warnings = ev.units, ev.years, ev.warnings
        unit_vectors = self.encoder.encode([u.text for u in units])
        job_items = job_requirements(req.job_text)
        if not job_items:
            warnings.append("No requirement-like sentences found in job_text.")
        job_matches = classify(job_items, units, score(job_items, units, self.encoder, unit_vectors=unit_vectors))
        job_score = coverage(job_matches, credit=scoring.credit) if job_matches else 0.0
        occ_score, occ_matches, occ, version = None, [], None, None
        if req.soc_code:
            occ = self.occupation(req.soc_code)
            occ_score, occ_matches, _ = self._score(occ, units, unit_vectors, years, self.roles(ev.history))
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
    clauses is replaced by its clauses; title lines are skipped."""
    units = [u for u in from_text(job_text) if u.matchable]
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


__all__ = ["GeneralEngine", "RoleNotResolved", "prepare_occupation", "job_requirements", "requirement_skill_id",
           "apply_role_history"]
