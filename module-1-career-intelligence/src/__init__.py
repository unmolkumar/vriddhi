"""
Module 1: Career Intelligence & Forecasting Engine.
Exposes public schema models and the master CareerIntelService for consumption
by downstream modules (Module 2, Module 3, and Integration Layer).
"""

from .models.schemas import (
    CareerAnalysisRequest,
    CareerAnalysisResponse,
    CareerRankRequest,
    CareerRankResponse,
    RankedCareerItem,
    RankingWeightConfig,
    DomainSearchRequest,
    DomainSearchResponse,
    DomainSearchItem,
    YearlyTrajectory,
    YearlyDataPoint,
    KnowledgeGraph,
    GraphNode,
    GraphEdge,
    RegionMetricDetail,
    TaskExposureDetail,
)
from .engines.service import CareerIntelligenceService

__all__ = [
    "CareerIntelligenceService",
    "CareerAnalysisRequest",
    "CareerAnalysisResponse",
    "CareerRankRequest",
    "CareerRankResponse",
    "RankedCareerItem",
    "RankingWeightConfig",
    "DomainSearchRequest",
    "DomainSearchResponse",
    "DomainSearchItem",
    "YearlyTrajectory",
    "YearlyDataPoint",
    "KnowledgeGraph",
    "GraphNode",
    "GraphEdge",
    "RegionMetricDetail",
    "TaskExposureDetail",
]
