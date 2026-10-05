"""
FastAPI Routes for Module 1 Career Intelligence Engine.
Implements the contract defined in MODULE-1-CAREER-INTELLIGENCE.md and INTEGRATION.md.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List, Optional
from ..models.schemas import (
    CareerAnalysisRequest,
    CareerAnalysisResponse,
    CareerRankRequest,
    CareerRankResponse,
    DomainSearchRequest,
    DomainSearchResponse,
)
from ..engines.service import CareerIntelligenceService

router = APIRouter(prefix="/api/v1/career", tags=["Career Intelligence"])


def get_service() -> CareerIntelligenceService:
    return CareerIntelligenceService()


@router.post("/analyze", response_model=CareerAnalysisResponse)
async def analyze_career(
    request: CareerAnalysisRequest,
    service: CareerIntelligenceService = Depends(get_service)
) -> CareerAnalysisResponse:
    """
    Analyze 5-year outlook, current market demand, task-level AI exposure,
    and regional signals for a specified occupation.
    """
    if not request.occupation or not request.occupation.strip():
        raise HTTPException(status_code=400, detail="Occupation cannot be empty.")
    try:
        return service.analyze_career(request.occupation, region=request.region)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.post("/rank", response_model=CareerRankResponse)
async def rank_careers(
    request: CareerRankRequest,
    service: CareerIntelligenceService = Depends(get_service)
) -> CareerRankResponse:
    """
    Rank a list of candidate occupations using configurable multi-signal weights.
    """
    if not request.occupations:
        raise HTTPException(status_code=400, detail="Must supply at least one occupation.")
    try:
        return service.rank_careers(
            occupations=request.occupations,
            weights=request.weights,
            region=request.region,
            top_k=request.top_k
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ranking failed: {str(e)}")


@router.get("/compare")
async def compare_regions(
    service: CareerIntelligenceService = Depends(get_service)
) -> Dict[str, Any]:
    """
    Retrieve side-by-side India vs Global posting trends and skill demand comparison.
    """
    return service.get_regional_comparison()


@router.get("/occupations")
async def list_occupations(
    limit: int = 100,
    service: CareerIntelligenceService = Depends(get_service)
) -> List[Dict[str, Any]]:
    """
    List available standard O*NET occupations.
    """
    return service.db.list_occupations(limit=limit)


@router.post("/search_by_domain", response_model=DomainSearchResponse)
async def search_by_domain(
    request: DomainSearchRequest,
    service: CareerIntelligenceService = Depends(get_service)
) -> DomainSearchResponse:
    """
    Search career pathways by entering an interest domain or keywords
    (e.g., 'Artificial Intelligence', 'FinTech', 'Cloud', 'Data Analytics').
    """
    if not request.domain_query or not request.domain_query.strip():
        raise HTTPException(status_code=400, detail="Domain query cannot be empty.")
    try:
        return service.search_by_domain(request.domain_query, top_k=request.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Domain search failed: {str(e)}")
