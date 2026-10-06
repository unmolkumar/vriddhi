"""Module 1's occupation requirement rows -> weighted RequirementItems, with the noise filtered out.

Module 1 v2.0's rows carry known noise (industry labels, cities, seniority words, benefits, one-posting tails,
duplicates). Every filter is a named constant, records its reason on the item (down-weights) or in the per-SOC
FilterReport (drops), and does nothing when the data is clean. WORKING.md "General engine (v2) - A1".
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# --- layers and weights -------------------------------------------------------------------------
LAYER_OF = {"market_skill": "core", "tech": "core", "tool": "core", "knowledge": "core", "task": "core", "dwa": "core",
            "skill": "transferable", "work_activity": "transferable", "ability": "fit_indicator"}
LAYER_WEIGHT = {"core": 1.0, "transferable": 0.5, "fit_indicator": 0.0}   # abilities are never something to learn
DEFAULT_LEVEL = {"market_skill": 0.5, "tech": 0.5, "tool": 0.5, "task": 0.6, "dwa": 0.6,
                 "knowledge": 0.5, "skill": 0.5, "work_activity": 0.5, "ability": 0.5}  # when level_norm is null

# --- filters --------------------------------------------------------------------------------------
MARKET_MIN_SHARE = 0.02        # market_skill kept if india_demand_share >= this ...
MARKET_MIN_POSTINGS = 3        # ... or seen in this many postings (when module 1 sends a count)
MARKET_CAP = 50                # most market_skill rows kept per SOC, by share
OFF_DOMAIN_TYPES = ("tech", "tool")
OFF_DOMAIN_SIM = 0.18          # tech/tool less similar than this to the occupation's domain (domain_texts) ...
OFF_DOMAIN_FACTOR = 0.3        # ... keeps this share of its weight (down-weighted, never deleted)
NOISE_TYPES = ("market_skill",)
GENERIC_DESCRIPTIONS = {"extracted from indian job postings"}

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
    source: str
    reliable: bool
    layer: str
    weight: float = Field(description="importance x LAYER_WEIGHT[layer] x reliability")
    reliability: float = 1.0
    hot_technology: bool = False
    india_demand_share: float | None = None
    flags: list[str] = Field(default_factory=list, description="Why the weight was lowered, e.g. 'off_domain(0.18)'")

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
    down_weighted: dict[str, list[str]] = Field(default_factory=dict, description="reason -> names down-weighted")

    def drop(self, reason: str, name: str) -> None:
        self.dropped.setdefault(reason, []).append(name)


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
    """What off-domain tech is compared with: the title and module 1's domain labels (major group, cluster,
    Indian industry). The description only when there are no labels. Measured on the fixture, task sentences
    let 'Epic Systems' through for accountants (0.34) while flagging Excel; the labels separate them."""
    labels = [str(domain[f]) for f in DOMAIN_FIELDS if domain and domain.get(f)]
    return [title, *labels] if labels else [t for t in (title, description) if t]


def normalise(rows: list[dict], *, title: str = "", aliases: list[str] | None = None,
              domain_similarity: Callable[[list[str]], list[float]] | None = None
              ) -> tuple[list[RequirementItem], FilterReport]:
    """Filter and weight one SOC's requirement rows.

    domain_similarity(texts) -> similarity of each tech/tool text to the occupation's domain_texts(); without
    it the off-domain down-weight is skipped.
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
        share = r.get("india_demand_share")
        if item_type == "market_skill":
            postings = r.get("posting_count")
            if (share or 0) < MARKET_MIN_SHARE and not (postings is not None and postings >= MARKET_MIN_POSTINGS):
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
            reliable=True, layer=layer, weight=importance * LAYER_WEIGHT[layer],
            hot_technology=bool(r.get("hot_technology")), india_demand_share=share)
        key = (item_type, norm_name(name))
        if key in seen:
            report.drop("duplicate", name)
            if item.importance > seen[key].importance:      # keep the stronger copy
                seen[key] = item
            continue
        seen[key] = item
    items = list(seen.values())

    market = sorted((i for i in items if i.item_type == "market_skill"), key=lambda i: -(i.india_demand_share or 0))
    for extra in market[MARKET_CAP:]:
        report.drop("market_cap", extra.name)
    capped = {id(i) for i in market[MARKET_CAP:]}
    items = [i for i in items if id(i) not in capped]

    off = [i for i in items if i.item_type in OFF_DOMAIN_TYPES]
    if domain_similarity and off:
        for item, sim in zip(off, domain_similarity([i.text for i in off])):
            if sim < OFF_DOMAIN_SIM:
                item.reliability = OFF_DOMAIN_FACTOR
                item.weight = item.importance * LAYER_WEIGHT[item.layer] * item.reliability
                item.flags.append(f"off_domain({sim:.2f})")
                report.down_weighted.setdefault("off_domain", []).append(item.name)

    for i in items:
        report.kept[i.item_type] = report.kept.get(i.item_type, 0) + 1
    if report.dropped or report.down_weighted:
        log.info("requirements %s: dropped %s, down-weighted %s", soc,
                 {k: len(v) for k, v in report.dropped.items()}, {k: len(v) for k, v in report.down_weighted.items()})
    return items, report
