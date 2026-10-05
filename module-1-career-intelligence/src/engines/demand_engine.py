"""
Current Demand Engine.
Calculates multi-signal market demand scores for careers using real posting volume,
employer diversification, geographic breadth, and compensation premiums.
"""
import math
from typing import Dict, Any, Optional


class DemandEngine:
    def calculate_demand(self, posting_history: Dict[str, Any], region: str = "all") -> Dict[str, Any]:
        """
        Calculate current demand score (0.0 to 1.0) using composite market signals.
        Supports 'india', 'global', or 'all' regional scope.
        """
        glob = posting_history.get("global", {})
        ind = posting_history.get("india", {})

        glob_total = glob.get("total", 0)
        ind_total = ind.get("total", 0)

        # 1. Total Volume Signal (log-normalized)
        if region == "india":
            vol = ind_total
            max_vol = 5000.0
        elif region == "global":
            vol = glob_total
            max_vol = 10000.0
        else:
            vol = glob_total + ind_total
            max_vol = 15000.0

        if vol <= 0:
            volume_score = 0.15  # Baseline for sparse data
        else:
            volume_score = min(math.log1p(vol) / math.log1p(max_vol), 1.0)

        # 2. Employer Diversification Signal
        # High unique employers relative to postings signals widespread multi-industry demand
        glob_comp = glob.get("unique_companies", 0)
        ind_comp = ind.get("unique_companies", 0)
        tot_comp = glob_comp + ind_comp
        if vol > 0:
            diversification = min(tot_comp / (vol * 0.4 + 1.0), 1.0)
        else:
            diversification = 0.50

        # 3. Recent Velocity Signal (2024-2026 postings vs older)
        modern_postings = ind.get("modern_postings", 0) + glob.get("postings_2024", 0) + glob.get("postings_2025_plus", 0)
        if vol > 0:
            velocity_ratio = min((modern_postings / vol) * 1.2, 1.0)
        else:
            velocity_ratio = 0.50

        # 4. Geographic Spread
        locations_count = len(ind.get("top_cities", [])) + len(glob.get("top_locations", []))
        geo_score = min(locations_count / 8.0, 1.0)

        # Weighted composite score
        demand_score = (
            (volume_score * 0.40) +
            (diversification * 0.25) +
            (velocity_ratio * 0.20) +
            (geo_score * 0.15)
        )
        demand_score = max(0.10, min(round(demand_score, 2), 0.98))

        return {
            "current_demand_score": demand_score,
            "volume_score": round(volume_score, 2),
            "employer_diversification": round(diversification, 2),
            "velocity_ratio": round(velocity_ratio, 2),
            "total_postings_evaluated": vol,
            "unique_employers_evaluated": tot_comp,
            "regional_breakdown": {
                "india": {
                    "total_postings": ind_total,
                    "unique_companies": ind_comp,
                    "top_cities": ind.get("top_cities", [])
                },
                "global": {
                    "total_postings": glob_total,
                    "unique_companies": glob_comp,
                    "top_locations": glob.get("top_locations", [])
                }
            }
        }
