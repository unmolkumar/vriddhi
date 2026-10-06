"""Module 1's occupation requirement rows -> weighted RequirementItems, with noise and hand-made data handled.

Module 1's rows can carry noise (v2.0: industry labels, cities, seniority words, benefits, one-posting tails,
duplicates) and hand-written rows (v2.1: source 'curated', and 'tool_*' rows labelled onet). Every filter is a named
constant, records its reason on the item (down-weights, provenance) or in the per-SOC FilterReport (drops), and does
nothing when the data is clean. WORKING.md section 11.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# --- layers and weights -------------------------------------------------------------------------
# core: matched against evidence and scored. transferable: inferred and reported as "what the role draws on", never
# scored or listed as gaps (they don't discriminate between occupations). fit_indicator: abilities, reported only.
LAYER_OF = {"market_skill": "core", "tech": "core", "tool": "core", "task": "core", "dwa": "core",
            "knowledge": "transferable", "skill": "transferable", "work_activity": "transferable",
            "ability": "fit_indicator"}
SCORED_TYPES = tuple(t for t, layer in LAYER_OF.items() if layer == "core")
LAYER_WEIGHT = {"core": 1.0, "transferable": 0.5, "fit_indicator": 0.0}   # abilities are never something to learn
DEFAULT_LEVEL = {"market_skill": 0.5, "tech": 0.5, "tool": 0.5, "task": 0.6, "dwa": 0.6,
                 "knowledge": 0.5, "skill": 0.5, "work_activity": 0.5, "ability": 0.5}  # when level_norm is null

# --- provenance -----------------------------------------------------------------------------------
# Hand-written rows must not drive results until module 1 replaces them with real data.
Provenance = Literal["onet", "india_postings", "curated", "job_text"]   # job_text: /match_text only
CURATED_WEIGHT = 0.5               # weight factor for curated rows
CURATED_SOURCES = {"curated"}
CURATED_TOOL_ID_PREFIX = "tool_"   # v2.1 tool rows are hand-written in module 1's ETL but labelled source='onet'
POSTING_SOURCES = {"india_postings", "global_postings"}

# --- filters --------------------------------------------------------------------------------------
MARKET_MIN_SHARE = 0.005       # safety net (module 1 v2.1 filters already): drops single-posting noise ...
MARKET_MIN_POSTINGS = 3        # ... replaced by posting_count >= 3 whenever module 1 sends posting_count
MARKET_CAP = 50                # most posting-derived market_skill rows kept per SOC, by share
OFF_DOMAIN_TYPES = ("tech", "tool")
OFF_DOMAIN_SIM = 0.18          # less similar than this to the occupation's domain (domain_texts) ...
OFF_DOMAIN_FACTOR = 0.3        # ... keeps this share of its weight (down-weighted, never deleted)
MARKET_OFF_DOMAIN_MAX_SHARE = 0.05  # posting-derived market skills below this share get the off-domain check too
NOISE_TYPES = ("market_skill",)
GENERIC_DESCRIPTIONS = {"extracted from indian job postings", "curated domain competency"}

INDUSTRY_LABELS = {"hotels", "it hardware", "ites", "medical", "financial services", "hr", "it services", "bpo",
                   "banking", "telecom", "retail", "fmcg", "pharma", "automobile", "insurance", "real estate",
                   "ecommerce", "e-commerce", "hospitality", "healthcare services"}
INDUSTRY_PATTERNS = [re.compile(r"^it software\b"), re.compile(r"^it\s*[-/]\s*")]
INDIAN_PLACES = {
    "ahmedabad", "bengaluru", "bangalore", "chennai", "delhi", "new delhi", "delhi ncr", "ncr", "gurugram", "gurgaon",
    "noida", "hyderabad", "kolkata", "mumbai", "navi mumbai", "pune", "jaipur", "lucknow", "chandigarh", "indore",
    "bhopal", "nagpur", "surat", "vadodara", "kochi", "coimbatore", "visakhapatnam", "thane", "patna", "bhubaneswar",
    "gujarat", "maharashtra", "karnataka", "tamil nadu", "kerala", "telangana", "andhra pradesh", "uttar pradesh",
    "rajasthan", "west bengal", "madhya pradesh", "punjab", "haryana", "bihar", "odisha", "goa", "india"}
SENIORITY_WORDS = {"senior", "junior", "entry level", "entry-level", "mid level", "mid-level", "fresher", "freshers",
                   "experienced", "trainee", "intern", "internship", "lead", "head", "associate", "executive"}
GENERIC_TITLES = {"site engineer", "draughtsman", "draftsman", "animator", "engineer", "manager", "designer",
                  "developer", "technician", "supervisor", "operator"}
BENEFITS = {"health insurance", "vision", "dental", "provident fund", "pf", "gratuity", "paid leave", "paid time off",
            "401k", "cab facility", "food allowance", "life insurance", "medical insurance", "bonus"}
GENERIC_TERMS = {"site", "basic", "be", "com", "program", "machine", "application software", "others", "other",
                 "na", "n/a", "general"}
NOISE_LISTS = {"industry_label": INDUSTRY_LABELS, "place": INDIAN_PLACES, "seniority": SENIORITY_WORDS,
               "job_title": GENERIC_TITLES, "benefit": BENEFITS, "generic_term": GENERIC_TERMS}


class RequirementItem(BaseModel):
    soc: str
    item_type: str
    item_id: str
    name: str
    description: str
    importance: float = Field(ge=0.0, le=1.0, description="Module 1's importance_norm")
    level: float = Field(ge=0.0, le=1.0, description="Required proficiency: level_norm, or DEFAULT_LEVEL[type]")
    source: str = Field(description="Module 1's source label, as sent")
    provenance: Provenance = Field(
        description="Where the row really comes from; 'curated' = hand-written in module 1 (weight x CURATED_WEIGHT)")
    reliable: bool
    layer: str
    weight: float = Field(description="importance x LAYER_WEIGHT[layer] x reliability")
    reliability: float = Field(default=1.0, description="CURATED_WEIGHT and OFF_DOMAIN_FACTOR multiplied together")
    hot_technology: bool = False
    india_demand_share: float | None = Field(default=None, description="Share of Indian postings; never set for curated")
    flags: list[str] = Field(default_factory=list, description="Why the weight was lowered: 'curated', 'off_domain(0.12)'")

    @property
    def base_weight(self) -> float:
        """Weight before the provenance and off-domain factors."""
        return self.importance * LAYER_WEIGHT[self.layer]

    def scale(self, factor: float, flag: str) -> None:
        self.reliability *= factor
        self.weight = self.base_weight * self.reliability
        self.flags.append(flag)

    @property
    def text(self) -> str:
        """What gets embedded: '{name}: {description}', or the name when the description adds nothing."""
        d = self.description.strip()
        if not d or d.lower() == self.name.lower() or d.lower() in GENERIC_DESCRIPTIONS:
            return self.name
        return f"{self.name}: {d}"


class FilterReport(BaseModel):
    soc: str
    rows_in: int
    kept: dict[str, int] = Field(default_factory=dict, description="Items kept per item_type")
    dropped: dict[str, list[str]] = Field(default_factory=dict, description="reason -> names dropped")
    down_weighted: dict[str, list[str]] = Field(default_factory=dict,
                                                description="reason ('off_domain', 'curated') -> names down-weighted")

    def drop(self, reason: str, name: str) -> None:
        self.dropped.setdefault(reason, []).append(name)


def provenance_of(row: dict) -> Provenance:
    """onet | india_postings | curated. 'tool_*' ids are hand-written in module 1 v2.1 whatever their source says."""
    source = row.get("source") or "onet"
    if source in CURATED_SOURCES or (row.get("item_type") == "tool"
                                     and str(row.get("item_id") or "").startswith(CURATED_TOOL_ID_PREFIX)):
        return "curated"
    return "india_postings" if source in POSTING_SOURCES else "onet"


def norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w+#&./ -]", " ", name.lower())).strip()


def noise_reason(name: str, title_terms: set[str]) -> str | None:
    """Why a market_skill name is not a skill (case-insensitive), or None."""
    n = norm_name(name)
    if n in title_terms or n.rstrip("s") in title_terms:
        return "occupation_title"
    for reason, words in NOISE_LISTS.items():
        if n in words:
            return reason
    if any(p.search(n) for p in INDUSTRY_PATTERNS):
        return "industry_label"
    return None


def _title_terms(title: str, aliases: list[str]) -> set[str]:
    terms = set()
    for t in [title, *aliases]:
        n = norm_name(t)
        terms |= {n, n.rstrip("s")}
        terms |= {norm_name(part).rstrip("s") for part in re.split(r",| and | or ", t) if part.strip()}
    return {t for t in terms if t}


DOMAIN_FIELDS = ("major_group_title", "career_cluster", "india_industry")


def domain_texts(title: str, domain: dict | None = None, description: str | None = None) -> list[str]:
    """What off-domain items are compared with: the title and module 1's domain labels (major group, cluster,
    Indian industry). The description only when there are no labels. Measured on the fixture, task sentences
    let 'Epic Systems' through for accountants (0.34) while flagging Excel; the labels separate them."""
    labels = [str(domain[f]) for f in DOMAIN_FIELDS if domain and domain.get(f)]
    return [title, *labels] if labels else [t for t in (title, description) if t]


def _weak_support(row: dict, share: float | None) -> bool:
    """posting_count >= MARKET_MIN_POSTINGS when module 1 sends it, else the share safety net."""
    postings = row.get("posting_count")
    if postings is not None:
        return postings < MARKET_MIN_POSTINGS
    return (share or 0) < MARKET_MIN_SHARE


def normalise(rows: list[dict], *, title: str = "", aliases: list[str] | None = None,
              domain_similarity: Callable[[list[str]], list[float]] | None = None
              ) -> tuple[list[RequirementItem], FilterReport]:
    """Filter and weight one SOC's requirement rows.

    domain_similarity(texts) -> similarity of each text to the occupation's domain_texts(); without it the
    off-domain down-weight is skipped.
    """
    soc = rows[0]["soc_code"] if rows else ""
    report = FilterReport(soc=soc, rows_in=len(rows))
    title_terms = _title_terms(title, aliases or [])
    seen: dict[tuple[str, str], RequirementItem] = {}
    for r in rows:
        name, item_type = (r.get("item_name") or "").strip(), r.get("item_type", "")
        if not name or item_type not in LAYER_OF:
            report.drop("unknown_type_or_empty", f"{item_type}:{name}")
            continue
        if not r.get("reliable", 1):
            report.drop("unreliable", name)
            continue
        provenance = provenance_of(r)
        share = r.get("india_demand_share") if provenance != "curated" else None   # never shown as market data
        if item_type == "market_skill" and provenance == "india_postings" and _weak_support(r, share):
            report.drop("low_support", name)
            continue
        if item_type in NOISE_TYPES and (reason := noise_reason(name, title_terms)):
            report.drop(reason, name)
            continue
        importance = float(r.get("importance_norm") or 0.0)
        level = r.get("level_norm")
        layer = LAYER_OF[item_type]
        item = RequirementItem(
            soc=r.get("soc_code", soc), item_type=item_type, item_id=str(r.get("item_id") or norm_name(name)), name=name,
            description=r.get("item_description") or "", importance=importance,
            level=float(level) if level is not None else DEFAULT_LEVEL[item_type], source=r.get("source", ""),
            provenance=provenance, reliable=True, layer=layer, weight=importance * LAYER_WEIGHT[layer],
            hot_technology=bool(r.get("hot_technology")), india_demand_share=share)
        if provenance == "curated":
            item.scale(CURATED_WEIGHT, "curated")
        key = (item_type, norm_name(name))
        if key in seen:
            report.drop("duplicate", name)
            if item.importance > seen[key].importance:      # keep the stronger copy
                seen[key] = item
            continue
        seen[key] = item
    items = list(seen.values())

    market = sorted((i for i in items if i.item_type == "market_skill" and i.provenance == "india_postings"),
                    key=lambda i: -(i.india_demand_share or 0))
    for extra in market[MARKET_CAP:]:
        report.drop("market_cap", extra.name)
    capped = {id(i) for i in market[MARKET_CAP:]}
    items = [i for i in items if id(i) not in capped]

    off = [i for i in items if i.item_type in OFF_DOMAIN_TYPES or (
        i.item_type == "market_skill" and i.provenance == "india_postings"
        and (i.india_demand_share or 0) < MARKET_OFF_DOMAIN_MAX_SHARE)]
    if domain_similarity and off:
        for item, sim in zip(off, domain_similarity([i.text for i in off])):
            if sim < OFF_DOMAIN_SIM:
                item.scale(OFF_DOMAIN_FACTOR, f"off_domain({sim:.2f})")
                report.down_weighted.setdefault("off_domain", []).append(item.name)
    curated = [i.name for i in items if i.provenance == "curated"]
    if curated:
        report.down_weighted["curated"] = curated

    for i in items:
        report.kept[i.item_type] = report.kept.get(i.item_type, 0) + 1
    if report.dropped or report.down_weighted:
        log.info("requirements %s: dropped %s, down-weighted %s", soc,
                 {k: len(v) for k, v in report.dropped.items()}, {k: len(v) for k, v in report.down_weighted.items()})
    return items, report
