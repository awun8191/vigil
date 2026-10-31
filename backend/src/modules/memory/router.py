"""Memory router factory providing pgvector semantic memory search and chunk management."""
from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_current_user, get_memory_service
from src.modules.auth.repository import UserDTO
from src.modules.memory.data import (
    MemoryChunkResponse,
    MemoryExcludeRequest,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResultItem,
)
from src.modules.memory.repository import MemoryRepository


def build_memory_router(settings: Settings) -> APIRouter:
    """
    Build and configure memory APIRouter instance.
    Endpoints:
      - POST /api/v1/memory/search
      - GET /api/v1/memory/chunks/{id}
      - PATCH /api/v1/memory/chunks/{id}/exclude
      - DELETE /api/v1/memory/chunks/{id}
    """
    router = APIRouter(prefix="/memory", tags=["Memory"])

    @router.post(
        "/search",
        response_model=MemorySearchResponse,
        status_code=status.HTTP_200_OK,
        summary="Search Long-Term Memory",
    )
    async def search_memory(
        payload: MemorySearchRequest,
        service: MemoryRepository = Depends(get_memory_service),
        user: UserDTO = Depends(get_current_user),
    ) -> MemorySearchResponse:
        results = await service.search_memory(  # type: ignore[attr-defined]
            user_id=user.id,
            query=payload.query,
            scopes=payload.scopes,
            top_n=payload.top_n,
        )
        items = [
            MemorySearchResultItem(
                id=r.id,
                content=r.content,
                summary=r.summary,
                similarity=r.similarity,
                age_days=r.age_days,
                final_score=r.final_score,
            )
            for r in results
        ]
        return MemorySearchResponse(results=items, query=payload.query)

    @router.get(
        "/chunks/{chunk_id}",
        response_model=MemoryChunkResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Memory Chunk",
    )
    async def get_chunk(
        chunk_id: UUID,
        service: MemoryRepository = Depends(get_memory_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> MemoryChunkResponse:
        chunk = await service.get_chunk_by_id(chunk_id)
        if not chunk:
            raise NotFoundError(f"Memory chunk {chunk_id} not found")
        return MemoryChunkResponse.model_validate(chunk)

    @router.patch(
        "/chunks/{chunk_id}/exclude",
        status_code=status.HTTP_200_OK,
        summary="Exclude Chunk from Retrieval",
    )
    async def set_exclude(
        chunk_id: UUID,
        payload: MemoryExcludeRequest,
        service: MemoryRepository = Depends(get_memory_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> dict[str, bool]:
        await service.set_excluded(chunk_id, payload.excluded)
        return {"excluded": payload.excluded}

    @router.delete(
        "/chunks/{chunk_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete Memory Chunk",
    )
    async def delete_chunk(
        chunk_id: UUID,
        service: MemoryRepository = Depends(get_memory_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> None:
        await service.delete_chunk(chunk_id)

    return router
