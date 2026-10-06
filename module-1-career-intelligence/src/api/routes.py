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


# Router for Occupations & Cross-Industry Generalisation (v2.0)
occupations_router = APIRouter(prefix="/api/v1/occupations", tags=["Occupations & Taxonomy"])


@occupations_router.get("/search")
async def search_occupations(
    q: str,
    k: int = 5,
    service: CareerIntelligenceService = Depends(get_service)
) -> List[Dict[str, Any]]:
    """
    Free-text title -> top-k SOC candidates with confidence (alt titles + Indian aliases + embeddings).
    Example: q="staff nurse" -> 29-1141.00, q="CA" -> 13-2011.00, q="site engineer" -> 17-2051.00
    """
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty.")
    try:
        return service.search_occupations(query=q, top_k=k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Occupation search failed: {str(e)}")


@occupations_router.get("/{soc}/requirements")
async def get_occupation_requirements(
    soc: str,
    item_type: Optional[str] = None,
    limit: Optional[int] = None,
    service: CareerIntelligenceService = Depends(get_service)
) -> List[Dict[str, Any]]:
    """
    Retrieve unified requirements (v_occupation_requirements) for an occupation.
    Powers M2 semantic embedding matching and M3 job matching.
    """
    if not soc or not soc.strip():
        raise HTTPException(status_code=400, detail="SOC code cannot be empty.")
    try:
        return service.get_occupation_requirements(soc_code=soc, item_type=item_type, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Requirements retrieval failed: {str(e)}")


@occupations_router.get("/{soc}/profile")
async def get_occupation_profile(
    soc: str,
    service: CareerIntelligenceService = Depends(get_service)
) -> Dict[str, Any]:
    """
    Retrieve comprehensive 360-degree occupation profile:
    Job zone, Indian education, experience band, salary percentiles (city x experience x work mode),
    related occupations, and domain.
    """
    if not soc or not soc.strip():
        raise HTTPException(status_code=400, detail="SOC code cannot be empty.")
    try:
        profile = service.get_occupation_profile(soc_code=soc)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Occupation '{soc}' not found.")
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Profile retrieval failed: {str(e)}")


@occupations_router.get("/{soc}/related")
async def get_related_occupations(
    soc: str,
    limit: int = 20,
    service: CareerIntelligenceService = Depends(get_service)
) -> List[Dict[str, Any]]:
    """
    Retrieve related occupations for career transition and lateral mobility pathways.
    """
    if not soc or not soc.strip():
        raise HTTPException(status_code=400, detail="SOC code cannot be empty.")
    try:
        return service.get_related_occupations(soc_code=soc, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Related occupations retrieval failed: {str(e)}")


meta_router = APIRouter(prefix="/api/v1", tags=["Metadata"])


@meta_router.get("/meta")
async def get_meta(
    service: CareerIntelligenceService = Depends(get_service)
) -> Dict[str, Any]:
    """
    Retrieve database build metadata, schema version, export hash, and table counts.
    Allows Module 2 and Module 3 to verify database build compatibility and invalidate caches.
    """
    meta = service.get_db_meta()
    if not meta:
        raise HTTPException(status_code=404, detail="Database metadata not found.")
    return meta

