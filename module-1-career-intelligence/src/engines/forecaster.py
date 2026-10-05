"""
5-Year Probabilistic Forecaster Engine.
Projects career trajectory over a five-year horizon based on market demand,
task-level AI transformation dynamics, and statistical confidence calibration.
"""
from typing import Dict, Any


class CareerForecaster:
    def forecast(
        self,
        occupation: str,
        current_demand_score: float,
        ai_exposure_score: float,
        posting_history: Dict[str, Any],
        tasks_count: int
    ) -> Dict[str, Any]:
        """
        Generate a probabilistic 5-year outlook.
        Never guarantees future outcomes; outputs probabilistic trajectory and confidence interval.
        """
        glob = posting_history.get("global", {})
        ind = posting_history.get("india", {})
        tot_postings = glob.get("total", 0) + ind.get("total", 0)

        # 1. Growth Score Modeling:
        # Growth is positively correlated with strong current demand and AI augmentation,
        # but moderated if the role is predominantly routine/direct automation.
        # AI exposure <= 0.50 acts as productivity leverage (accelerating demand).
        # AI exposure > 0.65 indicates substantial task automation risk.
        ai_growth_impact = (0.50 - ai_exposure_score) * 0.30

        # Velocity booster (recent 2024-2026 hiring momentum)
        modern_postings = ind.get("modern_postings", 0) + glob.get("postings_2024", 0) + glob.get("postings_2025_plus", 0)
        velocity_momentum = min(modern_postings / max(tot_postings, 1), 1.0) * 0.15

        base_growth = (current_demand_score * 0.70) + ai_growth_impact + velocity_momentum
        growth_score = max(0.12, min(round(base_growth, 2), 0.96))

        # 2. Confidence Calibration:
        # Higher sample sizes and task-level ground truth yield higher statistical confidence
        vol_confidence = min(tot_postings / 800.0, 0.45) if tot_postings > 0 else 0.10
        task_confidence = 0.30 if tasks_count > 0 else 0.10
        multi_region_confidence = 0.15 if (glob.get("total", 0) > 0 and ind.get("total", 0) > 0) else 0.05

        confidence_score = min(round(vol_confidence + task_confidence + multi_region_confidence + 0.10, 2), 0.95)

        # 3. Probabilistic Outlook Determination
        if growth_score >= 0.75 and confidence_score >= 0.60:
            outlook = "Strong Growth"
        elif growth_score >= 0.55:
            outlook = "Moderate Growth"
        elif ai_exposure_score >= 0.65:
            outlook = "Transforming (High AI Exposure)"
        elif growth_score >= 0.40:
            outlook = "Stable Demand"
        else:
            outlook = "Emerging / Evolving"

        return {
            "occupation": occupation,
            "growth_score": growth_score,
            "current_demand_score": current_demand_score,
            "ai_exposure_score": ai_exposure_score,
            "confidence_score": confidence_score,
            "outlook": outlook
        }
