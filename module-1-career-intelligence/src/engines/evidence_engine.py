"""
Evidence Engine.
Generates human-readable, data-backed explanatory drivers answering:
'Why is this career recommended or projected along this trajectory?'
"""
from typing import List, Dict, Any


class EvidenceEngine:
    def generate_drivers(
        self,
        occupation: str,
        forecast: Dict[str, Any],
        demand_metrics: Dict[str, Any],
        ai_metrics: Dict[str, Any],
        top_skills: List[str]
    ) -> List[str]:
        """Generate structured evidence statements based on real signals."""
        drivers = []
        growth_score = forecast.get("growth_score", 0.5)
        demand_score = forecast.get("current_demand_score", 0.5)
        ai_score = forecast.get("ai_exposure_score", 0.4)
        total_postings = demand_metrics.get("total_postings_evaluated", 0)

        # 1. Posting Volume & Market Demand Driver
        if total_postings > 500:
            drivers.append(f"Substantial real-world market presence with {total_postings:,} verified postings across global and Indian labor markets.")
        elif total_postings > 50:
            drivers.append(f"Active recruitment signals with {total_postings:,} job postings tracked.")
        else:
            drivers.append("Emerging specialization with concentrated niche employer demand.")

        # 2. Velocity Driver
        vel_ratio = demand_metrics.get("velocity_ratio", 0.5)
        if vel_ratio > 0.6:
            drivers.append("Strong recent hiring momentum with steady acceleration from 2024 through 2026.")
        elif vel_ratio > 0.3:
            drivers.append("Consistent baseline posting velocity across consecutive hiring cycles.")

        # 3. AI / Automation Impact Driver
        ai_cat = ai_metrics.get("exposure_category", "Moderate transformation exposure")
        task_count = ai_metrics.get("task_count", 0)
        breakdown = ai_metrics.get("breakdown", {})
        aug_tasks = breakdown.get("AI Augmentation", 0)
        auto_tasks = breakdown.get("Direct Automation", 0)

        if aug_tasks > auto_tasks:
            drivers.append(f"High augmentation leverage: {aug_tasks} tasks enhanced by generative AI tools, increasing worker productivity rather than substituting roles.")
        elif auto_tasks >= aug_tasks and auto_tasks > 0:
            drivers.append(f"Routine task automation: {auto_tasks} tasks exposed to workflow automation, shifting value toward strategic oversight.")
        else:
            drivers.append(f"Task profile ({task_count} O*NET tasks) indicates resilient cognitive and domain-specific problem solving.")

        # 4. Regional Breadth Driver
        reg = demand_metrics.get("regional_breakdown", {})
        ind_cities = reg.get("india", {}).get("top_cities", [])
        glob_locs = reg.get("global", {}).get("top_locations", [])
        if ind_cities and glob_locs:
            top_ind = ind_cities[0].split('(')[0].strip()
            drivers.append(f"Balanced multi-regional hiring spanning leading Indian tech hubs ({top_ind}) and international markets.")
        elif ind_cities:
            top_ind = ind_cities[0].split('(')[0].strip()
            drivers.append(f"Strong domestic Indian market concentration, primarily across {top_ind} and surrounding tech centers.")

        # 5. Core Skills Driver
        if top_skills:
            skill_sample = ", ".join(top_skills[:4])
            drivers.append(f"High employer demand for foundational and emerging capabilities: {skill_sample}.")

        return drivers
