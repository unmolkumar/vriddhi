"""
Configurable Career Ranking Engine.
Ranks multiple occupations across multi-dimensional signals using configurable scoring weights.
Weights are dynamic and never permanently hard-coded.
"""
from typing import List, Dict, Optional, Any, Tuple
from ..models.schemas import RankedCareerItem, RankingWeightConfig


class RankingEngine:
    DEFAULT_WEIGHTS = {
        "current_demand": 0.30,
        "growth": 0.30,
        "ai_resilience": 0.20,
        "salary_level": 0.10,
        "confidence": 0.10,
    }

    def normalize_weights(self, weights: Optional[Dict[str, float]]) -> Dict[str, float]:
        """Ensure weights are non-empty, non-negative, and sum to 1.0."""
        if not weights:
            return dict(self.DEFAULT_WEIGHTS)

        clean = {k: max(0.0, float(v)) for k, v in weights.items() if k in self.DEFAULT_WEIGHTS}
        # Fill missing with default
        for k, v in self.DEFAULT_WEIGHTS.items():
            if k not in clean:
                clean[k] = v

        total = sum(clean.values())
        if total <= 0:
            return dict(self.DEFAULT_WEIGHTS)
        return {k: round(v / total, 4) for k, v in clean.items()}

    def rank_careers(
        self,
        evaluated_careers: List[Dict[str, Any]],
        custom_weights: Optional[Dict[str, float]] = None,
        top_k: Optional[int] = None
    ) -> Tuple[List[RankedCareerItem], Dict[str, float]]:
        """
        Rank evaluated careers according to normalized weights.
        Returns ranked list and weights applied.
        """
        weights = self.normalize_weights(custom_weights)
        ranked_items = []

        for item in evaluated_careers:
            occ = item["occupation"]
            d_score = item["current_demand_score"]
            g_score = item["growth_score"]
            ai_score = item["ai_exposure_score"]
            conf_score = item["confidence_score"]

            # AI Resilience: roles with moderate AI exposure (augmentation) score higher than those with high routine risk
            ai_resilience = max(0.0, 1.0 - (ai_score * 0.70))

            # Salary factor: use proxy from demand if explicit salary not provided
            sal_score = d_score * 0.9

            composite = (
                (d_score * weights["current_demand"]) +
                (g_score * weights["growth"]) +
                (ai_resilience * weights["ai_resilience"]) +
                (sal_score * weights["salary_level"]) +
                (conf_score * weights["confidence"])
            )

            ranked_items.append({
                "occupation": occ,
                "composite_score": round(composite, 3),
                "current_demand_score": d_score,
                "growth_score": g_score,
                "ai_exposure_score": ai_score,
                "confidence_score": conf_score,
                "outlook": item.get("outlook", "Moderate Growth"),
                "top_skills": item.get("top_skills", []),
                "primary_driver": item.get("drivers", ["Strong market alignment."])[0] if item.get("drivers") else "Consistent demand signals."
            })

        # Sort descending by composite score
        ranked_items.sort(key=lambda x: x["composite_score"], reverse=True)

        # Assign ranks
        results = []
        for idx, item in enumerate(ranked_items, start=1):
            results.append(RankedCareerItem(
                rank=idx,
                occupation=item["occupation"],
                composite_score=item["composite_score"],
                current_demand_score=item["current_demand_score"],
                growth_score=item["growth_score"],
                ai_exposure_score=item["ai_exposure_score"],
                confidence_score=item["confidence_score"],
                outlook=item["outlook"],
                top_skills=item["top_skills"],
                primary_driver=item["primary_driver"]
            ))

        if top_k and top_k > 0:
            results = results[:top_k]

        return results, weights
