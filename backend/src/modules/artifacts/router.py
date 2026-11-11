"""Artifacts router factory providing file metadata, registration, and downloads."""
from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_artifacts_service, get_current_device
from src.modules.artifacts.data import ArtifactCreateRequest, ArtifactResponse
from src.modules.artifacts.repository import ArtifactCreateDTO, ArtifactsRepository
from src.modules.auth.repository import DeviceDTO


def build_artifacts_router(settings: Settings) -> APIRouter:
    """
    Build and configure artifacts APIRouter instance.
    Endpoints:
      - GET /api/v1/artifacts/{id}
      - GET /api/v1/artifacts/{id}/download
      - GET /api/v1/artifacts/run/{run_id}
      - POST /api/v1/artifacts
    """
    router = APIRouter(prefix="/artifacts", tags=["Artifacts"])

    @router.post(
        "",
        response_model=ArtifactResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Register Artifact",
    )
    async def register_artifact(
        payload: ArtifactCreateRequest,
        service: ArtifactsRepository = Depends(get_artifacts_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> ArtifactResponse:
        dto = ArtifactCreateDTO(
            run_id=payload.run_id,
            kind=payload.kind,
            path=payload.path,
            size_bytes=payload.size_bytes,
            preview=payload.preview,
        )
        saved = await service.save_artifact(dto)
        return ArtifactResponse.model_validate(saved)

    @router.get(
        "/{artifact_id}",
        response_model=ArtifactResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Artifact Metadata",
    )
    async def get_artifact(
        artifact_id: UUID,
        service: ArtifactsRepository = Depends(get_artifacts_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> ArtifactResponse:
        artifact = await service.get_artifact_by_id(artifact_id)
        if not artifact:
            raise NotFoundError(f"Artifact {artifact_id} not found")
        return ArtifactResponse.model_validate(artifact)

    @router.get(
        "/{artifact_id}/download",
        summary="Download Binary Artifact File",
    )
    async def download_artifact(
        artifact_id: UUID,
        service: ArtifactsRepository = Depends(get_artifacts_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> StreamingResponse:
        stream, filename, media_type, size = await service.get_artifact_stream(artifact_id)  # type: ignore[attr-defined]
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size),
        }
        return StreamingResponse(stream, media_type=media_type, headers=headers)

    @router.get(
        "/{artifact_id}/stream",
        summary="Stream Artifact File Inline",
    )
    async def stream_artifact(
        artifact_id: UUID,
        service: ArtifactsRepository = Depends(get_artifacts_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> StreamingResponse:
        stream, filename, media_type, size = await service.get_artifact_stream(artifact_id)  # type: ignore[attr-defined]
        headers = {
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(size),
        }
        return StreamingResponse(stream, media_type=media_type, headers=headers)

    @router.get(
        "/{artifact_id}/text",
        response_class=PlainTextResponse,
        summary="Read Artifact As Text",
    )
    async def read_artifact_text(
        artifact_id: UUID,
        service: ArtifactsRepository = Depends(get_artifacts_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> PlainTextResponse:
        text = await service.get_artifact_text(artifact_id)  # type: ignore[attr-defined]
        return PlainTextResponse(text)

    @router.get(
        "/run/{run_id}",
        response_model=list[ArtifactResponse],
        status_code=status.HTTP_200_OK,
        summary="List Run Artifacts",
    )
    async def list_run_artifacts(
        run_id: UUID,
        service: ArtifactsRepository = Depends(get_artifacts_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[ArtifactResponse]:
        artifacts = await service.list_artifacts_by_run(run_id)
        return [ArtifactResponse.model_validate(a) for a in artifacts]

    return router
