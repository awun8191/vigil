"""Local embedding integration: pinned bge-small-en-v1.5 int8 ONNX encoder."""
from src.integrations.embedding.contract import (
    EmbeddingArtifactError,
    EmbeddingError,
    EmbeddingModel,
)
from src.integrations.embedding.local import LocalEmbeddingModel, get_local_embedder

__all__ = [
    "EmbeddingArtifactError",
    "EmbeddingError",
    "EmbeddingModel",
    "LocalEmbeddingModel",
    "get_local_embedder",
]
