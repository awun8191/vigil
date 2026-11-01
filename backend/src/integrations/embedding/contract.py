"""Pure contract for the local embedding model.

The embedding path is CPU only and offline at inference time. This module holds no
runtime dependency, so tests and callers can use the contract without loading ONNX.
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


class EmbeddingError(RuntimeError):
    """Base error for the local embedding path."""


class EmbeddingArtifactError(EmbeddingError):
    """The pinned artifact is missing, unreadable, or fails its checksum."""


@runtime_checkable
class EmbeddingModel(Protocol):
    """Local text encoder contract.

    The model maps natural language to a unit-length vector. It never accepts tool
    output, shell logs, or JSON blobs, because those pollute the vector space.
    """

    model_id: str
    dimensions: int

    def count_tokens(self, text: str) -> int:
        """Return the token count of a text for this model."""
        ...

    def truncate(self, text: str, max_tokens: int | None = None) -> str:
        """Cut a text to the model token cap and return the head."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode stored text. The document prefix is empty for this model."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Encode a search query. The query instruction prefix is added."""
        ...

    def unload(self) -> None:
        """Drop the ONNX session to release memory."""
        ...
