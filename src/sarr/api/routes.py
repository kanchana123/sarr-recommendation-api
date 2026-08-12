"""HTTP routes for health and search."""

from fastapi import APIRouter, HTTPException

from sarr.api.search_service import SearchService
from sarr.common.schemas import SearchRequest, SearchResponse

router = APIRouter()
_service: SearchService | None = None


def get_search_service() -> SearchService:
    global _service
    if _service is None:
        _service = SearchService()
    return _service


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    try:
        return get_search_service().search(request)
    except Exception as exc:  # noqa: BLE001 — surfaced as HTTP 502 for MVP
        raise HTTPException(status_code=502, detail=str(exc)) from exc
