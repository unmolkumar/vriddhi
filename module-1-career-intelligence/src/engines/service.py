"""
Career Intelligence Service Orchestrator.
Coordinates database access, demand evaluation, task-level AI exposure analysis,
probabilistic forecasting, and evidence synthesis.
"""
from typing import Dict, List, Optional, Any
from .database import CareerDatabase
from .demand_engine import DemandEngine
from .ai_exposure import AIExposureEngine
from .forecaster import CareerForecaster
from .evidence_engine import EvidenceEngine
from .ranking_engine import RankingEngine
from ..models.schemas import (
    CareerAnalysisResponse,
    CareerRankResponse,
    RegionMetricDetail,
    TaskExposureDetail,
)


class CareerIntelligenceService:
    def __init__(self, db: Optional[CareerDatabase] = None):
        self.db = db or CareerDatabase()
        self.demand_engine = DemandEngine()
        self.ai_engine = AIExposureEngine()
        self.forecaster = CareerForecaster()
        self.evidence_engine = EvidenceEngine()
        self.ranking_engine = RankingEngine()

    def analyze_career(self, occupation: str, region: str = "all") -> CareerAnalysisResponse:
        """
        Full 360-degree career analysis answering:
        'Which careers/jobs are likely to be valuable over the next five years?'
        """
        occ_rec = self.db.find_occupation(occupation)
        soc_code = occ_rec.get("soc_code") if occ_rec else None
        target_name = occ_rec.get("title") if occ_rec else occupation

        # 1. Fetch O*NET Tasks and evaluate AI exposure
        tasks = self.db.get_tasks_for_soc(soc_code) if soc_code else []
        ai_metrics = self.ai_engine.analyze_occupation_tasks(tasks)

        # 2. Fetch real posting history across India and Global markets
        posting_history = self.db.get_posting_history(target_name)
        demand_metrics = self.demand_engine.calculate_demand(posting_history, region=region)

        # 3. Fetch top skills
        skills_data = self.db.get_top_skills(target_name, limit=6)
        all_skills = list(dict.fromkeys(skills_data.get("india", []) + skills_data.get("global", [])))

        # 4. Generate 5-year probabilistic forecast
        forecast = self.forecaster.forecast(
            occupation=target_name,
            current_demand_score=demand_metrics["current_demand_score"],
            ai_exposure_score=ai_metrics["ai_exposure_score"],
            posting_history=posting_history,
            tasks_count=len(tasks)
        )

        # 5. Generate explanatory drivers
        drivers = self.evidence_engine.generate_drivers(
            occupation=target_name,
            forecast=forecast,
            demand_metrics=demand_metrics,
            ai_metrics=ai_metrics,
            top_skills=all_skills
        )

        # 6. Build side-by-side regional breakdown
        glob = posting_history.get("global", {})
        ind = posting_history.get("india", {})

        inr_avg = ind.get("avg_max_inr") or ind.get("avg_min_inr")
        lpa = round(inr_avg / 100000.0, 1) if inr_avg else None

        regional_breakdown = {
            "india": RegionMetricDetail(
                region="india",
                posting_volume=ind.get("total", 0),
                posting_growth_yoy_pct=round(demand_metrics.get("velocity_ratio", 0.5) * 15.0, 1),
                median_salary_inr_lpa=lpa,
                median_salary_usd=round(inr_avg * 0.012, 2) if inr_avg else None,
                top_locations=ind.get("top_cities", []),
                top_skills=skills_data.get("india", [])[:5]
            ),
            "global": RegionMetricDetail(
                region="global",
                posting_volume=glob.get("total", 0),
                posting_growth_yoy_pct=round(demand_metrics.get("velocity_ratio", 0.5) * 18.0, 1),
                median_salary_usd=round(glob.get("avg_salary_usd", 0), 2) if glob.get("avg_salary_usd") else None,
                top_locations=glob.get("top_locations", []),
                top_skills=skills_data.get("global", [])[:5]
            )
        }

        # Sample task transformation details
        sample_task_details = [
            TaskExposureDetail(
                task_description=item["task_description"],
                ai_impact_score=item["ai_impact_score"],
                transformation_type=item["transformation_type"],
                rationale=item["rationale"]
            )
            for item in ai_metrics.get("sample_evaluations", [])[:4]
        ]

        return CareerAnalysisResponse(
            occupation=target_name,
            soc_code=soc_code,
            current_demand_score=forecast["current_demand_score"],
            growth_score=forecast["growth_score"],
            ai_exposure_score=forecast["ai_exposure_score"],
            confidence_score=forecast["confidence_score"],
            outlook=forecast["outlook"],
            top_skills=all_skills[:6],
            drivers=drivers,
            tasks_analyzed=ai_metrics.get("task_count", 0),
            sample_tasks=sample_task_details,
            regional_breakdown=regional_breakdown
        )

    def rank_careers(
        self,
        occupations: List[str],
        weights: Optional[Dict[str, float]] = None,
        region: str = "all",
        top_k: Optional[int] = None
    ) -> CareerRankResponse:
        """Evaluate and rank multiple careers using configurable weights."""
        evaluated = []
        for occ in occupations:
            res = self.analyze_career(occ, region=region)
            evaluated.append(res.model_dump())

        ranked_items, applied_weights = self.ranking_engine.rank_careers(
            evaluated_careers=evaluated,
            custom_weights=weights,
            top_k=top_k
        )

        return CareerRankResponse(
            rankings=ranked_items,
            weights_applied=applied_weights,
            total_evaluated=len(occupations)
        )

    def get_regional_comparison(self) -> Dict[str, Any]:
        """Query materialized comparison view for India vs Global trends."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT region, year, total_postings, unique_employers, avg_salary_min_usd, avg_salary_max_usd FROM v_india_vs_global")
            postings_comp = [dict(row) for row in cursor.fetchall()]

            cursor.execute("SELECT skill_normalized, region, total_demand FROM v_skill_comparison LIMIT 20")
            skills_comp = [dict(row) for row in cursor.fetchall()]

            return {
                "postings_by_region_and_year": postings_comp,
                "top_skills_by_region": skills_comp
            }
