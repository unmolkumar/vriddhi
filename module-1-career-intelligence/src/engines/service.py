"""
Career Intelligence Service Orchestrator.
Coordinates database access, demand evaluation, task-level AI exposure analysis,
probabilistic forecasting, knowledge graph generation, and domain exploration.
"""
from typing import Dict, List, Optional, Any
from .database import CareerDatabase
from .demand_engine import DemandEngine
from .ai_exposure import AIExposureEngine
from .forecaster import CareerForecaster
from .evidence_engine import EvidenceEngine
from .ranking_engine import RankingEngine
from .knowledge_graph import KnowledgeGraphEngine
from ..models.schemas import (
    CareerAnalysisResponse,
    CareerRankResponse,
    RegionMetricDetail,
    TaskExposureDetail,
    DomainSearchResponse,
    DomainSearchItem,
)


class CareerIntelligenceService:
    def __init__(self, db: Optional[CareerDatabase] = None):
        self.db = db or CareerDatabase()
        self.demand_engine = DemandEngine()
        self.ai_engine = AIExposureEngine()
        self.forecaster = CareerForecaster()
        self.evidence_engine = EvidenceEngine()
        self.ranking_engine = RankingEngine()
        self.kg_engine = KnowledgeGraphEngine()

    def analyze_career(self, occupation: str, region: str = "all") -> CareerAnalysisResponse:
        """
        Full 360-degree career analysis answering:
        'Which careers/jobs are likely to be valuable over the next five years?'
        Includes multi-year historical + 5-year forecast trajectory and knowledge graph.
        """
        occ_rec = self.db.find_occupation(occupation)
        soc_code = occ_rec.get("soc_code") if occ_rec else None
        target_name = occ_rec.get("title") if occ_rec else occupation
        domain_name = occ_rec.get("domain") if occ_rec else "Technology & Analytics"

        # 1. Fetch O*NET Tasks and evaluate AI exposure
        tasks = self.db.get_tasks_for_soc(soc_code) if soc_code else []
        ai_metrics = self.ai_engine.analyze_occupation_tasks(tasks)

        # 2. Fetch real posting history across India and Global markets
        posting_history = self.db.get_posting_history(target_name)
        yearly_breakdown = self.db.get_yearly_breakdown(target_name)
        demand_metrics = self.demand_engine.calculate_demand(posting_history, region=region)

        # 3. Fetch top skills and O*NET technology taxonomy
        skills_data = self.db.get_top_skills(target_name, limit=6)
        all_skills = list(dict.fromkeys(skills_data.get("india", []) + skills_data.get("global", [])))
        soc_skills_dict = self.db.get_skills_for_soc(soc_code) if soc_code else {"skills": [], "technologies": []}

        # 4. Generate 5-year probabilistic forecast & continuous time-series trajectory
        forecast = self.forecaster.forecast(
            occupation=target_name,
            current_demand_score=demand_metrics["current_demand_score"],
            ai_exposure_score=ai_metrics["ai_exposure_score"],
            posting_history=posting_history,
            tasks_count=len(tasks),
            yearly_counts=yearly_breakdown
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

        # 7. Construct Knowledge Graph
        kg = self.kg_engine.build_graph(
            occupation=target_name,
            soc_code=soc_code,
            domain=domain_name,
            tasks=ai_metrics.get("sample_evaluations", []),
            skills_dict=soc_skills_dict,
            top_market_skills=all_skills
        )

        # 8. Derive empirical experience band & market salary percentiles
        exp_band = self.db.get_experience_band(target_name)
        salary_percentiles = self.db.get_salary_percentiles(target_name)

        return CareerAnalysisResponse(
            occupation=target_name,
            soc_code=soc_code,
            current_demand_score=forecast["current_demand_score"],
            growth_score=forecast["growth_score"],
            ai_exposure_score=forecast["ai_exposure_score"],
            confidence_score=forecast["confidence_score"],
            outlook=forecast["outlook"],
            top_skills=all_skills[:6],
            top_skill_weights={sk: skills_data.get("weights", {}).get(sk, 1.0) for sk in all_skills[:6]},
            drivers=drivers,
            tasks_analyzed=ai_metrics.get("task_count", 0),
            sample_tasks=sample_task_details,
            regional_breakdown=regional_breakdown,
            typical_experience=exp_band,
            market_salary_percentiles=salary_percentiles,
            yearly_trajectory=forecast.get("trajectory"),
            knowledge_graph=kg
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

    def search_by_domain(self, domain_query: str, top_k: int = 6) -> DomainSearchResponse:
        """
        User enters an interest domain / keywords, and the engine discovers matching
        career pathways with outlook and projected growth scores.
        """
        matched_occs = self.db.search_occupations_by_domain(domain_query, limit=top_k)
        results = []

        for item in matched_occs:
            # Quick profile evaluation
            occ_title = item["title"]
            analysis = self.analyze_career(occ_title)

            results.append(DomainSearchItem(
                occupation=occ_title,
                soc_code=item["soc_code"],
                domain=item.get("domain", "Technology"),
                relevance_score=item.get("relevance_score", 0.85),
                matching_skills=item.get("matching_skills", analysis.top_skills[:3]),
                outlook=analysis.outlook,
                growth_score=analysis.growth_score
            ))

        return DomainSearchResponse(
            domain_query=domain_query,
            results=results
        )

    def get_regional_comparison(self) -> Dict[str, Any]:
        """Query materialized comparison view for India vs Global trends."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT region, year, total_postings, unique_employers, avg_salary_min_usd, avg_salary_max_usd FROM v_india_vs_global")
            postings_comp = [dict(row) for row in cursor.fetchall()]

            cursor.execute("SELECT skill_normalized, region, total_demand FROM v_skill_comparison LIMIT 25")
            skills_comp = [dict(row) for row in cursor.fetchall()]

            return {
                "postings_by_region_and_year": postings_comp,
                "top_skills_by_region": skills_comp
            }
