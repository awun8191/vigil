"""Local int8 ONNX encoder for bge-small-en-v1.5.

Three properties matter for a 2 GB worker:

* The artifact is pinned by revision and checksum. A mismatch stops the load.
* The ONNX session loads on first use, not at import time.
* The session unloads after the idle window, because it holds roughly 90 MB.

Pooling is CLS from the first token. Mean pooling is wrong for this model and
loses quality with no error, so the test suite pins the expected vector.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from src.config.settings import Settings
from src.integrations.embedding.contract import (
    EmbeddingArtifactError,
    EmbeddingError,
)

logger = logging.getLogger("virgil.embedding")

_ARTIFACT_URL = "https://huggingface.co/{repo}/resolve/{revision}/{file}"
_HASH_BLOCK_BYTES = 1 << 20
_DOWNLOAD_TIMEOUT_SECONDS = 120


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_HASH_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    """Download a file to the destination path with an atomic rename."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            with partial.open("wb") as handle:
                for block in iter(lambda: response.read(_HASH_BLOCK_BYTES), b""):
                    handle.write(block)
        partial.replace(destination)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        raise EmbeddingArtifactError(f"Cannot download {url}: {exc}") from exc


class LocalEmbeddingModel:
    """bge-small-en-v1.5 int8 ONNX encoder with checksum, lazy load, and idle unload."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        session_factory: Callable[[Path], Any] | None = None,
        downloader: Callable[[str, Path], None] | None = None,
    ) -> None:
        self.settings = settings
        self.model_id = settings.EMBEDDING_MODEL
        self.dimensions = settings.EMBEDDING_DIMENSIONS
        self.max_tokens = settings.EMBEDDING_MAX_TOKENS
        self.document_prefix = settings.EMBEDDING_DOCUMENT_PREFIX
        self.query_prefix = settings.EMBEDDING_QUERY_PREFIX

        self._clock = clock
        self._session_factory = session_factory or self._create_session
        self._downloader = downloader or _download
        self._lock = threading.RLock()
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._last_used = 0.0
        self._verified_sha256: str | None = None
        self.max_batch = max(1, int(settings.EMBEDDING_MAX_BATCH))
        self.max_batch_tokens = max(1, int(settings.EMBEDDING_MAX_BATCH_TOKENS))
        self._idle_seconds = float(settings.EMBEDDING_IDLE_UNLOAD_SECONDS)
        self._sweep_interval = max(0.05, min(self._idle_seconds / 10.0, 30.0))
        self._stop_sweeper = threading.Event()
        self._sweeper: threading.Thread | None = None

    # ------------------------------------------------------------------ paths

    @property
    def model_dir(self) -> Path:
        """Directory that holds the downloaded artifact and tokenizer."""
        return Path(self.settings.EMBEDDING_MODEL_DIR).expanduser()

    @property
    def artifact_path(self) -> Path:
        """Absolute path of the pinned ONNX file."""
        return self.model_dir / Path(self.settings.EMBEDDING_ARTIFACT_FILE).name

    @property
    def tokenizer_path(self) -> Path:
        """Absolute path of the tokenizer file that matches the artifact."""
        return self.model_dir / self.settings.EMBEDDING_TOKENIZER_FILE

    @property
    def session(self) -> Any | None:
        """Loaded ONNX session, or None when unloaded."""
        return self._session

    def _remote_url(self, file_name: str) -> str:
        return _ARTIFACT_URL.format(
            repo=self.settings.EMBEDDING_ARTIFACT_REPO,
            revision=self.settings.EMBEDDING_ARTIFACT_REVISION,
            file=file_name,
        )

    # ------------------------------------------------------------------- load

    def ensure_artifacts(self) -> Path:
        """Download the model when absent, verify its digest, then ensure the tokenizer."""
        path = self.artifact_path
        if not path.is_file():
            self._downloader(self._remote_url(self.settings.EMBEDDING_ARTIFACT_FILE), path)
        self._verified_sha256 = self._verify_artifact(path)
        if not self.tokenizer_path.is_file():
            self._downloader(
                self._remote_url(self.settings.EMBEDDING_TOKENIZER_FILE),
                self.tokenizer_path,
            )
        return path

    def _verify_artifact(self, path: Path) -> str:
        """Return the SHA-256 of the artifact, or raise when it does not match."""
        expected = self.settings.EMBEDDING_ARTIFACT_SHA256.lower()
        if not path.is_file():
            raise EmbeddingArtifactError(f"Embedding artifact not found at {path}")
        digest = _sha256(path)
        if digest != expected:
            raise EmbeddingArtifactError(
                f"Checksum mismatch for {path}: expected {expected}, got {digest}"
            )
        return digest

    def _create_session(self, path: Path) -> Any:
        """Build a CPU-only ONNX session with the pinned thread budget."""
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - dependency is installed
            raise EmbeddingError("onnxruntime is not installed") from exc

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.settings.EMBEDDING_INTRA_OP_THREADS
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Release the buffers of a finished run. The arena holds the peak of
        # the largest run, which the index job cannot afford.
        options.enable_cpu_mem_arena = False
        return ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def load(self) -> None:
        """Verify the artifact and load the session. A bad digest stops here."""
        with self._lock:
            if self._session is not None:
                self._last_used = self._clock()
                return
            path = self.ensure_artifacts()
            self._ensure_tokenizer()
            self._session = self._session_factory(path)
            self._last_used = self._clock()
            self._start_sweeper()
            logger.info(
                "Embedding artifact verified and loaded: path=%s sha256=%s dimensions=%d",
                path,
                self._verified_sha256,
                self.dimensions,
            )

    def _ensure_tokenizer(self) -> Any:
        if self._tokenizer is not None:
            return self._tokenizer
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - dependency is installed
            raise EmbeddingError("tokenizers is not installed") from exc

        if not self.tokenizer_path.is_file():
            self._downloader(
                self._remote_url(self.settings.EMBEDDING_TOKENIZER_FILE),
                self.tokenizer_path,
            )
        tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self._tokenizer = tokenizer
        return tokenizer

    # ------------------------------------------------------------------ unload

    def unload(self) -> None:
        """Release the ONNX session. Safe to call more than once."""
        with self._lock:
            self._session = None

    def unload_if_idle(self, now: float | None = None) -> bool:
        """Release the session when it sat unused for the idle window."""
        with self._lock:
            if self._session is None:
                return False
            instant = self._clock() if now is None else now
            if instant - self._last_used < self._idle_seconds:
                return False
            self._session = None
            return True

    def _start_sweeper(self) -> None:
        if self._sweeper is not None and self._sweeper.is_alive():
            return
        self._sweeper = threading.Thread(
            target=self._sweep_loop,
            name="embedding-idle-sweeper",
            daemon=True,
        )
        self._sweeper.start()

    def _sweep_loop(self) -> None:
        while not self._stop_sweeper.wait(self._sweep_interval):
            self.unload_if_idle()

    def close(self) -> None:
        """Stop the idle sweeper and release the session."""
        self._stop_sweeper.set()
        sweeper = self._sweeper
        if sweeper is not None and sweeper.is_alive() and sweeper is not threading.current_thread():
            sweeper.join(timeout=2.0)
        self.unload()

    # ------------------------------------------------------------------ tokens

    def count_tokens(self, text: str) -> int:
        """Return the token count for this tokenizer, including special tokens."""
        return len(self._ensure_tokenizer().encode(text).ids)

    def truncate(self, text: str, max_tokens: int | None = None) -> str:
        """Cut a text to the token cap and return the decoded head.

        The cap counts the special tokens that the encoder adds back, so the
        result re-encodes to no more than max_tokens ids.
        """
        limit = self.max_tokens if max_tokens is None else max_tokens
        tokenizer = self._ensure_tokenizer()
        ids = tokenizer.encode(text).ids
        if len(ids) <= limit:
            return text
        budget = max(1, limit - tokenizer.num_special_tokens_to_add(False))
        keep = ids[1 : 1 + budget]
        candidate = tokenizer.decode(keep, skip_special_tokens=True)
        while keep and len(tokenizer.encode(candidate).ids) > limit:
            keep = keep[:-1]
            candidate = tokenizer.decode(keep, skip_special_tokens=True)
        return candidate

    # ------------------------------------------------------------------ encode

    def _pool(self, hidden_state: np.ndarray) -> list[float]:
        """Take the CLS token and normalize. Mean pooling is wrong for bge models."""
        vector = np.asarray(hidden_state[0], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector = vector / norm
        return [float(value) for value in vector]

    def _encode_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode the texts in bounded batches and return one vector for each.

        ONNX Runtime keeps the attention buffers of one run, so the batch cap
        keeps the peak resident set bounded when a session makes many windows.
        The tokenizer pads a batch to its longest row, and this int8 model is
        not invariant to that padding. A run therefore holds only texts of one
        token length, so every vector stays the vector of its own text.
        """
        if self._session is None:
            raise EmbeddingError("Embedding session is not loaded")
        tokenizer = self._ensure_tokenizer()
        prepared = [self.truncate(text, self.max_tokens) for text in texts]

        groups: dict[int, list[tuple[int, list[int], list[int]]]] = {}
        for index, text in enumerate(prepared):
            encoding = tokenizer.encode(text)
            groups.setdefault(len(encoding.ids), []).append(
                (index, encoding.ids, encoding.attention_mask)
            )

        vectors: list[list[float] | None] = [None] * len(prepared)
        for length in sorted(groups):
            entries = groups[length]
            # A run holds at most the window cap and at most the token budget.
            per_run = max(1, min(self.max_batch, self.max_batch_tokens // length))
            for start in range(0, len(entries), per_run):
                window = entries[start : start + per_run]
                input_ids = np.array([entry[1] for entry in window], dtype=np.int64)
                attention_mask = np.array(
                    [entry[2] for entry in window], dtype=np.int64
                )
                feeds = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": np.zeros_like(input_ids),
                }
                outputs = self._session.run(None, feeds)
                hidden = np.asarray(outputs[0])
                for offset, entry in enumerate(window):
                    vectors[entry[0]] = self._pool(hidden[offset])
                del input_ids, attention_mask, feeds, outputs, hidden

        if any(vector is None for vector in vectors):
            raise EmbeddingError("The encoder returned fewer vectors than texts")
        return [vector for vector in vectors if vector is not None]

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self.load()
        try:
            return self._encode_batch(texts)
        finally:
            with self._lock:
                self._last_used = self._clock()

    # ---------------------------------------------------------------- prefixes

    def prepare_document(self, text: str) -> str:
        """Apply the document prefix. It is empty for this model."""
        return f"{self.document_prefix}{text}"

    def prepare_query(self, text: str) -> str:
        """Apply the query instruction prefix, which is required for retrieval."""
        return f"{self.query_prefix}{text}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode stored text with no prefix."""
        return self._encode([self.prepare_document(text) for text in texts])

    def embed_query(self, text: str) -> list[float]:
        """Encode a retrieval query with the instruction prefix."""
        return self._encode([self.prepare_query(text)])[0]


_SHARED_MODELS: dict[tuple[str, str, str, int, int], LocalEmbeddingModel] = {}
_SHARED_LOCK = threading.Lock()


def get_local_embedder(settings: Settings) -> LocalEmbeddingModel:
    """Return the process-wide embedder so one session serves every job."""
    key = (
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_MODEL_DIR,
        settings.EMBEDDING_ARTIFACT_REVISION,
        settings.EMBEDDING_DIMENSIONS,
        settings.EMBEDDING_IDLE_UNLOAD_SECONDS,
    )
    with _SHARED_LOCK:
        model = _SHARED_MODELS.get(key)
        if model is None:
            model = LocalEmbeddingModel(settings)
            _SHARED_MODELS[key] = model
        return model
