"""Unit tests for the pinned local embedding model.

The encoding tests load the real bge-small-en-v1.5 int8 artifact. When the file
is absent and the network is down, those tests skip with a reason. The contract
tests (prefix, checksum, idle unload) never need the artifact.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from src.config.settings import Settings
from src.integrations.embedding import (
    EmbeddingArtifactError,
    EmbeddingModel,
    LocalEmbeddingModel,
)
from src.modules.memory.chunker import Chunker, TokenLimiter

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
PINNED_REVISION = "ea104dacec62c0de699686887e3f920caeb4f3e3"


def _settings(**overrides: object) -> Settings:
    """Build isolated settings. The .env file must not leak into a test."""
    return Settings(_env_file=None, **overrides)


# --------------------------------------------------------------------- contract


def test_model_satisfies_the_contract() -> None:
    """The local model is a valid encoder and a valid chunk token limiter."""
    model = LocalEmbeddingModel(_settings())
    assert isinstance(model, EmbeddingModel)
    assert isinstance(model, TokenLimiter)
    chunker = Chunker(model)
    assert chunker.max_tokens == 512


def test_prefix_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The encoder receives the query prefix for a query and no prefix for a document."""
    settings = _settings()
    assert settings.EMBEDDING_DOCUMENT_PREFIX == ""
    assert settings.EMBEDDING_QUERY_PREFIX == QUERY_PREFIX

    model = LocalEmbeddingModel(settings)
    captured: list[list[str]] = []

    def fake_encode(texts: list[str]) -> list[list[float]]:
        captured.append(list(texts))
        return [[0.0] * settings.EMBEDDING_DIMENSIONS for _ in texts]

    monkeypatch.setattr(model, "_encode", fake_encode)

    question = "what did I say about the budget?"
    document = "the user discussed the Q3 budget"
    model.embed_query(question)
    model.embed_documents([document])

    assert captured[0] == [QUERY_PREFIX + question]
    assert captured[1] == [document]
    assert not captured[1][0].startswith(QUERY_PREFIX)


def test_checksum_mismatch(tmp_path: Path) -> None:
    """A wrong digest raises and the session is never created."""
    (tmp_path / "model_int8.onnx").write_bytes(b"this is not the pinned artifact")
    settings = _settings(
        EMBEDDING_MODEL_DIR=str(tmp_path),
        EMBEDDING_ARTIFACT_SHA256="0" * 64,
    )
    created: list[Path] = []

    def factory(path: Path) -> object:
        created.append(path)
        return object()

    model = LocalEmbeddingModel(settings, session_factory=factory)
    with pytest.raises(EmbeddingArtifactError) as error:
        model.load()

    assert "Checksum mismatch" in str(error.value)
    assert model.session is None
    assert created == [], "a bad digest must stop the load before ONNX starts"
    assert not (tmp_path / "tokenizer.json").exists(), "no tokenizer download after a bad digest"


def test_missing_artifact_is_downloaded_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader downloads the pinned revision, then verifies the digest."""
    payload = b"pretend onnx bytes"
    digest = hashlib.sha256(payload).hexdigest()
    settings = _settings(
        EMBEDDING_MODEL_DIR=str(tmp_path),
        EMBEDDING_ARTIFACT_SHA256=digest,
    )
    downloaded: list[str] = []

    def downloader(url: str, destination: Path) -> None:
        downloaded.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith("model_int8.onnx"):
            destination.write_bytes(payload)
        else:
            destination.write_bytes(b'{"version": "1.0"}')

    model = LocalEmbeddingModel(
        settings, downloader=downloader, session_factory=lambda path: object()
    )
    monkeypatch.setattr(model, "_ensure_tokenizer", lambda: object())
    model.load()

    assert downloaded
    assert downloaded[0].endswith("/onnx/model_int8.onnx")
    assert PINNED_REVISION in downloaded[0]
    assert model.session is not None


def test_idle_unload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The session is None after the idle window passes."""
    import time

    payload = b"pretend onnx bytes"
    settings = _settings(
        EMBEDDING_MODEL_DIR=str(tmp_path),
        EMBEDDING_ARTIFACT_SHA256=hashlib.sha256(payload).hexdigest(),
        EMBEDDING_IDLE_UNLOAD_SECONDS=1,
    )
    (tmp_path / "model_int8.onnx").write_bytes(payload)
    model = LocalEmbeddingModel(settings, session_factory=lambda path: object())
    monkeypatch.setattr(model, "_ensure_tokenizer", lambda: object())

    model.load()
    assert model.session is not None

    deadline = time.monotonic() + 10.0
    while model.session is not None and time.monotonic() < deadline:
        time.sleep(0.02)

    assert model.session is None, "the idle sweeper must release the ONNX session"
    model.close()


def test_unload_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated unload is safe and leaves the session at None."""
    payload = b"pretend onnx bytes"
    settings = _settings(
        EMBEDDING_MODEL_DIR=str(tmp_path),
        EMBEDDING_ARTIFACT_SHA256=hashlib.sha256(payload).hexdigest(),
    )
    (tmp_path / "model_int8.onnx").write_bytes(payload)
    model = LocalEmbeddingModel(settings, session_factory=lambda path: object())
    monkeypatch.setattr(model, "_ensure_tokenizer", lambda: object())
    model.load()

    model.unload()
    model.unload()

    assert model.session is None
    assert model.unload_if_idle() is False
    model.close()


# ---------------------------------------------------------------- real artifact


@pytest.fixture(scope="module")
def embedder() -> LocalEmbeddingModel:
    """Load the real model, or skip the module with a reason when unavailable."""
    model = LocalEmbeddingModel(_settings())
    try:
        model.load()
    except EmbeddingArtifactError as exc:
        pytest.skip(f"bge-small-en-v1.5 artifact unavailable: {exc}")
    yield model
    model.close()


def test_model_metadata(embedder: LocalEmbeddingModel) -> None:
    """The pinned model reports its real shape."""
    assert embedder.model_id == "bge-small-en-v1.5"
    assert embedder.dimensions == 384
    assert embedder.max_tokens == 512


def test_cls_pooling_matches_first_token(embedder: LocalEmbeddingModel) -> None:
    """The vector equals the normalized CLS token, not the mean of the tokens."""
    text = "The registration key for Server X is alpha-7."
    vector = np.asarray(embedder.embed_documents([text])[0], dtype=np.float32)

    assert vector.shape == (384,)
    assert abs(float(np.linalg.norm(vector)) - 1.0) < 1e-4

    tokenizer = embedder._ensure_tokenizer()
    encoding = tokenizer.encode(embedder.prepare_document(text))
    ids = np.array([encoding.ids], dtype=np.int64)
    mask = np.array([encoding.attention_mask], dtype=np.int64)
    hidden = embedder.session.run(
        None,
        {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        },
    )[0][0]

    cls = hidden[0].astype(np.float32)
    cls = cls / np.linalg.norm(cls)
    assert np.allclose(vector, cls, atol=1e-5), "pooling must take the CLS token"

    mean = hidden[mask[0] == 1].mean(axis=0).astype(np.float32)
    mean = mean / np.linalg.norm(mean)
    assert not np.allclose(vector, mean, atol=1e-4), "mean pooling must not match"
    assert float(np.dot(vector, mean)) < 0.99


def test_query_prefix_changes_the_vector(embedder: LocalEmbeddingModel) -> None:
    """The query instruction prefix reaches the encoder and changes the output."""
    text = "What was the registration key for Server X?"
    document = np.asarray(embedder.embed_documents([text])[0], dtype=np.float32)
    query = np.asarray(embedder.embed_query(text), dtype=np.float32)

    assert query.shape == (384,)
    assert not np.allclose(document, query, atol=1e-4)


def test_long_text_is_capped_before_encoding(embedder: LocalEmbeddingModel) -> None:
    """A text over the cap is cut to the 512 token limit before the run."""
    long_text = "word " * 2000
    raw_tokens = embedder.count_tokens(long_text)
    assert raw_tokens > embedder.max_tokens

    truncated = embedder.truncate(long_text)
    truncated_tokens = embedder.count_tokens(truncated)
    assert truncated_tokens <= embedder.max_tokens
    assert truncated_tokens < raw_tokens

    vector = embedder.embed_documents([long_text])[0]
    assert len(vector) == 384


def test_similar_texts_are_closer_than_unrelated_texts(embedder: LocalEmbeddingModel) -> None:
    """Sanity check on retrieval quality: cosine ranks the related pair first."""
    anchor, related, unrelated = embedder.embed_documents(
        [
            "The deployment failed because the database password expired.",
            "The release broke when the database password ran out.",
            "Our quarterly marketing plan targets three new regions.",
        ]
    )
    anchor = np.asarray(anchor)
    related = np.asarray(related)
    unrelated = np.asarray(unrelated)

    assert float(np.dot(anchor, related)) > float(np.dot(anchor, unrelated))
