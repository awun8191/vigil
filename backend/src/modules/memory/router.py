"""Memory router factory providing pgvector semantic memory search and chunk management."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.exceptions import NotFoundError, ValidationError
from src.config.settings import Settings
from src.dependencies import get_current_user, get_db_session, get_memory_service
from src.modules.auth.repository import UserDTO
from src.modules.memory.data import (
    MemoryChunkListResponse,
    MemoryChunkResponse,
    MemoryExcludeRequest,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResultItem,
    PersonalizationPresetItem,
    PersonalizationResponse,
    PersonalizationUpdateRequest,
    PersonalizationUsage,
)
from src.modules.memory.repository import MemoryRepository
from src.modules.memory.store import PERSONA_PRESETS, MemoryStore


def _to_items(results: list) -> list[MemorySearchResultItem]:
    """Map scored DTOs to wire items, including similarity, final score, and age."""
    return [
        MemorySearchResultItem(
            id=result.id,
            content=result.content,
            summary=result.summary,
            similarity=result.similarity,
            age_days=result.age_days,
            final_score=result.final_score,
        )
        for result in results
    ]


def build_memory_router(settings: Settings) -> APIRouter:
    """
    Build and configure memory APIRouter instance.
    Endpoints:
      - GET /api/v1/memory/search
      - POST /api/v1/memory/search
      - GET /api/v1/memory/chunks/{id}
      - PATCH /api/v1/memory/chunks/{id}/exclude
      - DELETE /api/v1/memory/chunks/{id}
    """
    router = APIRouter(prefix="/memory", tags=["Memory"])

    @router.get(
        "/search",
        response_model=MemorySearchResponse,
        status_code=status.HTTP_200_OK,
        summary="Search Long-Term Memory",
    )
    async def search_memory_get(
        q: str = Query(..., min_length=1, max_length=2000),
        scopes: list[str] = Query(default=["global"]),
        top_n: int = Query(default=8, ge=1, le=50),
        service: MemoryRepository = Depends(get_memory_service),
        user: UserDTO = Depends(get_current_user),
    ) -> MemorySearchResponse:
        results = await service.search_memory(  # type: ignore[attr-defined]
            user_id=user.id,
            query=q,
            scopes=scopes,
            top_n=top_n,
        )
        return MemorySearchResponse(
            results=_to_items(results),
            query=q,
            path=getattr(service, "last_retrieval_path", None),
        )

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
        return MemorySearchResponse(
            results=_to_items(results),
            query=payload.query,
            path=getattr(service, "last_retrieval_path", None),
        )

    @router.get(
        "/chunks",
        response_model=MemoryChunkListResponse,
        status_code=status.HTTP_200_OK,
        summary="List Stored Memory Chunks",
    )
    async def list_chunks(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        include_excluded: bool = Query(default=True),
        service: MemoryRepository = Depends(get_memory_service),
        user: UserDTO = Depends(get_current_user),
    ) -> MemoryChunkListResponse:
        items, total = await service.list_chunks(  # type: ignore[attr-defined]
            user_id=user.id,
            limit=limit,
            offset=offset,
            include_excluded=include_excluded,
        )
        return MemoryChunkListResponse(
            items=[MemoryChunkResponse.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/chunks/{chunk_id}",
        response_model=MemoryChunkResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Memory Chunk",
    )
    async def get_chunk(
        chunk_id: UUID,
        service: MemoryRepository = Depends(get_memory_service),
        user: UserDTO = Depends(get_current_user),
    ) -> MemoryChunkResponse:
        chunk = await service.get_chunk_by_id(chunk_id)
        if not chunk or chunk.user_id != user.id:
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
        user: UserDTO = Depends(get_current_user),
    ) -> dict[str, bool]:
        chunk = await service.get_chunk_by_id(chunk_id)
        if not chunk or chunk.user_id != user.id:
            raise NotFoundError(f"Memory chunk {chunk_id} not found")
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
        user: UserDTO = Depends(get_current_user),
    ) -> None:
        chunk = await service.get_chunk_by_id(chunk_id)
        # A chunk of another user is not found, so the endpoint never leaks one.
        if not chunk or chunk.user_id != user.id:
            raise NotFoundError(f"Memory chunk {chunk_id} not found")
        deleted = await service.delete_chunk(chunk_id)
        if not deleted:
            raise NotFoundError(f"Memory chunk {chunk_id} not found")

    def _build_personalization_response(
        store: MemoryStore,
        display_name: str,
    ) -> PersonalizationResponse:
        soul_raw = store.load_soul_raw()
        user_entries = store.get_entries("user")
        memory_entries = store.get_entries("memory")
        user_raw = store.get_raw("user")
        memory_raw = store.get_raw("memory")
        learned_name = store.extract_user_name()
        effective_name = (
            learned_name
            if (not display_name or display_name == "Paired user") and learned_name
            else (display_name or "Paired user")
        )
        return PersonalizationResponse(
            display_name=effective_name,
            soul_md=soul_raw,
            soul_usage=PersonalizationUsage(**store.soul_usage()),
            user_md=user_raw,
            user_entries=user_entries,
            user_usage=PersonalizationUsage(**store.usage("user")),
            memory_md=memory_raw,
            memory_entries=memory_entries,
            memory_usage=PersonalizationUsage(**store.usage("memory")),
            presets=[PersonalizationPresetItem(**p) for p in PERSONA_PRESETS],
        )

    @router.get(
        "/personalization",
        response_model=PersonalizationResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Agent Personalization & Curated Memory (SOUL.md, USER.md, MEMORY.md)",
    )
    async def get_personalization(
        user: UserDTO = Depends(get_current_user),
    ) -> PersonalizationResponse:
        store = MemoryStore(settings=settings)
        return _build_personalization_response(store, user.display_name)

    @router.put(
        "/personalization",
        response_model=PersonalizationResponse,
        status_code=status.HTTP_200_OK,
        summary="Update Agent Personalization & Curated Memory (SOUL.md, USER.md, MEMORY.md)",
    )
    async def update_personalization(
        payload: PersonalizationUpdateRequest,
        user: UserDTO = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> PersonalizationResponse:
        store = MemoryStore(settings=settings)
        display_name = user.display_name

        if payload.reset_soul:
            res = store.reset_soul()
            if not res.get("success"):
                raise ValidationError(str(res.get("error") or "Failed to reset SOUL.md"))
        elif payload.soul_md is not None:
            res = store.save_soul(payload.soul_md)
            if not res.get("success"):
                raise ValidationError(str(res.get("error") or "Invalid SOUL.md content"))

        if payload.user_entries is not None:
            res = store.set_entries("user", payload.user_entries)
            if not res.get("success"):
                raise ValidationError(str(res.get("error") or "Invalid USER.md entries"))
        elif payload.user_md is not None:
            res = store.set_raw("user", payload.user_md)
            if not res.get("success"):
                raise ValidationError(str(res.get("error") or "Invalid USER.md content"))

        if payload.memory_entries is not None:
            res = store.set_entries("memory", payload.memory_entries)
            if not res.get("success"):
                raise ValidationError(str(res.get("error") or "Invalid MEMORY.md entries"))
        elif payload.memory_md is not None:
            res = store.set_raw("memory", payload.memory_md)
            if not res.get("success"):
                raise ValidationError(str(res.get("error") or "Invalid MEMORY.md content"))

        new_name: str | None = None
        if payload.display_name is not None and payload.display_name.strip():
            new_name = payload.display_name.strip()
            name_res = store.upsert_user_name(new_name)
            if not name_res.get("success"):
                raise ValidationError(str(name_res.get("error") or "Failed to update name in USER.md"))
        else:
            extracted = store.extract_user_name()
            if extracted:
                new_name = extracted

        if new_name:
            display_name = new_name
            try:
                from sqlalchemy import update
                from src.schemas.identity import UserModel

                await session.execute(
                    update(UserModel)
                    .where(UserModel.id == user.id)
                    .values(display_name=new_name[:255])
                )
                flush = getattr(session, "flush", None)
                if callable(flush):
                    await flush()
            except Exception:
                pass

        return _build_personalization_response(store, display_name)

    return router
