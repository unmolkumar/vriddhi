"""City normalisation. Canonical names match modules 1 and 2 (Bengaluru, Gurugram, ...)."""
from __future__ import annotations

import re

CITY_ALIASES = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "blr": "Bengaluru",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "mumbai": "Mumbai", "bombay": "Mumbai", "navi mumbai": "Mumbai", "thane": "Mumbai",
    "delhi": "Delhi", "new delhi": "Delhi",
    "noida": "Noida", "greater noida": "Noida",
    "delhi ncr": "Delhi NCR", "delhi-ncr": "Delhi NCR", "ncr": "Delhi NCR", "delhi/ncr": "Delhi NCR",
    "hyderabad": "Hyderabad", "secunderabad": "Hyderabad", "hyd": "Hyderabad",
    "chennai": "Chennai", "madras": "Chennai",
    "kolkata": "Kolkata", "calcutta": "Kolkata",
    "pune": "Pune", "poona": "Pune",
    "kochi": "Kochi", "cochin": "Kochi", "ahmedabad": "Ahmedabad", "jaipur": "Jaipur",
    "chandigarh": "Chandigarh", "indore": "Indore", "coimbatore": "Coimbatore",
    "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram",
    "remote": "Remote", "work from home": "Remote", "anywhere in india": "Remote",
}
REGIONS = {"Delhi NCR": ["Delhi", "Noida", "Gurugram"]}
# What each provider expects in its location parameter (Adzuna uses the older names).
PROVIDER_QUERY_NAME = {"Bengaluru": "Bangalore", "Gurugram": "Gurgaon", "Delhi": "New Delhi"}


def normalise_city(name: str | None) -> str | None:
    """'bangalore, karnataka' -> 'Bengaluru'; unknown cities are title-cased, not dropped."""
    if not name or not name.strip():
        return None
    key = re.sub(r"\s+", " ", name.lower().strip())
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    first = key.split(",")[0].strip()
    return CITY_ALIASES.get(first, first.title())


def expand_cities(name: str | None) -> list[str]:
    """'Delhi-NCR' -> ['Delhi', 'Noida', 'Gurugram']; a single city -> [city]."""
    city = normalise_city(name)
    if city is None:
        return []
    return REGIONS.get(city, [city])


def city_from_parts(parts: list[str]) -> str | None:
    """Most specific known city in a location hierarchy like ['India', 'Karnataka', 'Bangalore']."""
    for part in reversed(parts):
        city = normalise_city(part)
        if city and part.lower().strip() in CITY_ALIASES:
            return city
    return normalise_city(parts[-1]) if parts else None


def provider_query_name(city: str) -> str:
    return PROVIDER_QUERY_NAME.get(city, city)
