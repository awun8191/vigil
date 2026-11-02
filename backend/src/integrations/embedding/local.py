"""Local int8 ONNX encoder for bge-small-en-v1.5.

Four properties matter for a 2 GB worker:

* The artifact is pinned by revision and checksum. A mismatch stops the load.
* The ONNX session loads on first use, not at import time.
* The session unloads after the idle window, because it holds roughly 90 MB.
* The encode runs in a short lived child process by default, so the pages of
  the run go back to the operating system when the batch finishes. The worker
  process keeps only the tokenizer. A caller that injects a session keeps the
  in-process path.

Pooling is CLS from the first token. Mean pooling is wrong for this model and
loses quality with no error, so the test suite pins the expected vector.

The artifact is dynamically quantized: the activation scale of a run comes from
the largest activation in that run. A vector therefore depends on the other rows
of its run, so a run holds rows of one token length. The unit tests measure the
size of the effect.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import subprocess
import sys
import tempfile
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
_CHILD_TIMEOUT_SECONDS = 3600

# The child is self contained on purpose. It imports only numpy and
# onnxruntime, so no project path has to be configured for it. The parent
# sends the runs that it already planned, and the child pools CLS the same way.
_CHILD_SOURCE = '''\
import pickle
import sys

import numpy as np
import onnxruntime as ort

payload_path, output_path = sys.argv[1], sys.argv[2]
with open(payload_path, "rb") as handle:
    payload = pickle.load(handle)

options = ort.SessionOptions()
options.intra_op_num_threads = payload["threads"]
options.inter_op_num_threads = 1
options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
options.enable_cpu_mem_arena = False
session = ort.InferenceSession(
    payload["model"], sess_options=options, providers=["CPUExecutionProvider"]
)

rows = payload["rows"]
vectors = np.zeros((len(rows), payload["dimensions"]), dtype=np.float32)
for width, indices in payload["runs"]:
    input_ids = np.zeros((len(indices), width), dtype=np.int64)
    attention_mask = np.zeros((len(indices), width), dtype=np.int64)
    for offset, index in enumerate(indices):
        ids, mask = rows[index]
        input_ids[offset, : len(ids)] = ids
        attention_mask[offset, : len(mask)] = mask
    hidden = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": np.zeros_like(input_ids),
        },
    )[0]
    for offset, index in enumerate(indices):
        vector = np.asarray(hidden[offset, 0], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector = vector / norm
        vectors[index] = vector
    del input_ids, attention_mask, hidden

np.save(output_path, vectors)
'''


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_HASH_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _children_peak_mb() -> float:
    """Return the peak resident set of the finished child processes, in MiB."""
    try:
        import resource
    except ImportError:  # pragma: no cover - this project runs on Linux
        return 0.0
    return float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) / 1024.0


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
        self._in_flight = 0
        self._verified_sha256: str | None = None
        self._loaded = False
        # An injected factory means the caller wants the session in this
        # process, so the subprocess option does not apply to it.
        self._subprocess = bool(settings.EMBEDDING_ENCODE_SUBPROCESS) and session_factory is None
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
        """Verify the artifact and prepare the encoder. A bad digest stops here.

        In subprocess mode the session belongs to the child, so this process
        holds only the tokenizer and the verified digest.
        """
        with self._lock:
            if self._loaded:
                self._last_used = self._clock()
                return
            path = self.ensure_artifacts()
            self._ensure_tokenizer()
            if not self._subprocess:
                self._session = self._session_factory(path)
            self._loaded = True
            self._last_used = self._clock()
            self._start_sweeper()
            logger.info(
                "Embedding artifact verified and loaded: path=%s sha256=%s dimensions=%d mode=%s",
                path,
                self._verified_sha256,
                self.dimensions,
                "subprocess" if self._subprocess else "in-process",
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
            self._loaded = False

    def unload_if_idle(self, now: float | None = None) -> bool:
        """Release the session when it sat unused for the idle window.

        A run that is in flight is never unloaded. The idle clock only moves
        when an encode returns, so an encode that outlives the window would
        otherwise lose its session in the middle of the batch.
        """
        with self._lock:
            if self._session is None or self._in_flight > 0:
                return False
            instant = self._clock() if now is None else now
            if instant - self._last_used < self._idle_seconds:
                return False
            self._session = None
            self._loaded = False
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

    def _plan_runs(self, encodings: Sequence[Any]) -> list[tuple[int, list[int]]]:
        """Group rows by exact token length, then split them into bounded runs.

        The artifact is dynamically quantized, so the scale of a run comes from
        its largest activation and a mixed run changes every vector. A run
        therefore holds rows of one token length. The batch cap and the token
        budget bound the peak of a run.
        """
        groups: dict[int, list[int]] = {}
        for index, encoding in enumerate(encodings):
            groups.setdefault(len(encoding.ids), []).append(index)

        runs: list[tuple[int, list[int]]] = []
        for length in sorted(groups):
            entries = groups[length]
            per_run = max(1, min(self.max_batch, self.max_batch_tokens // length))
            for start in range(0, len(entries), per_run):
                runs.append((length, entries[start : start + per_run]))
        return runs

    def _encode_in_process(
        self, encodings: Sequence[Any], runs: Sequence[tuple[int, list[int]]]
    ) -> list[list[float]]:
        """Run the planned batches in this process's ONNX session."""
        if self._session is None:
            raise EmbeddingError("Embedding session is not loaded")

        vectors: list[list[float] | None] = [None] * len(encodings)
        for width, indices in runs:
            input_ids = np.zeros((len(indices), width), dtype=np.int64)
            attention_mask = np.zeros((len(indices), width), dtype=np.int64)
            for offset, index in enumerate(indices):
                ids = encodings[index].ids
                input_ids[offset, : len(ids)] = ids
                attention_mask[offset, : len(ids)] = encodings[index].attention_mask
            feeds = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": np.zeros_like(input_ids),
            }
            outputs = self._session.run(None, feeds)
            hidden = np.asarray(outputs[0])
            for offset, index in enumerate(indices):
                vectors[index] = self._pool(hidden[offset])
            del input_ids, attention_mask, feeds, outputs, hidden

        if any(vector is None for vector in vectors):
            raise EmbeddingError("The encoder returned fewer vectors than texts")
        return [vector for vector in vectors if vector is not None]

    def _encode_in_subprocess(
        self, encodings: Sequence[Any], runs: Sequence[tuple[int, list[int]]]
    ) -> list[list[float]]:
        """Run the planned batches in a child process that exits after them.

        The child holds the session, so the pages of the run go back to the
        operating system when it exits. This process keeps the tokenizer only.
        """
        rows = [
            (list(encoding.ids), list(encoding.attention_mask))
            for encoding in encodings
        ]
        payload = {
            "model": str(self.artifact_path),
            "dimensions": self.dimensions,
            "threads": int(self.settings.EMBEDDING_INTRA_OP_THREADS),
            "rows": rows,
            "runs": list(runs),
        }
        with tempfile.TemporaryDirectory(prefix="virgil-embed-") as work:
            work_path = Path(work)
            payload_path = work_path / "payload.pickle"
            output_path = work_path / "vectors.npy"
            with payload_path.open("wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)

            environment = {
                key: value for key, value in os.environ.items() if key != "PYTHONPATH"
            }
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        _CHILD_SOURCE,
                        str(payload_path),
                        str(output_path),
                    ],
                    cwd=str(work_path),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=_CHILD_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise EmbeddingError(
                    f"The embedding subprocess did not finish in {_CHILD_TIMEOUT_SECONDS} seconds"
                ) from exc
            except OSError as exc:
                raise EmbeddingError(
                    f"Cannot start the embedding subprocess: {exc}"
                ) from exc
            if completed.returncode != 0:
                tail = " | ".join((completed.stderr or "").strip().splitlines()[-5:])
                raise EmbeddingError(f"The embedding subprocess failed: {tail}")
            if not output_path.is_file():
                raise EmbeddingError("The embedding subprocess wrote no vectors")
            matrix = np.load(output_path)
            seconds = time.perf_counter() - started

        logger.info(
            "Embedding subprocess finished: rows=%d seconds=%.1f child_peak_rss_mb=%.1f",
            len(encodings),
            seconds,
            _children_peak_mb(),
        )
        return [[float(value) for value in row] for row in matrix]

    def _encode_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode the texts and return one vector for each, in input order.

        ONNX Runtime keeps the attention buffers of one run, so the batch cap
        keeps the peak resident set bounded. The tokenizer pads a batch to its
        longest row, and this int8 model is not invariant to that padding. A
        run therefore holds only texts of one token length, so every vector
        stays the vector of its own text.
        """
        tokenizer = self._ensure_tokenizer()
        prepared = [self.truncate(text, self.max_tokens) for text in texts]
        encodings = [tokenizer.encode(text) for text in prepared]
        runs = self._plan_runs(encodings)
        if self._subprocess and self._session is None:
            return self._encode_in_subprocess(encodings, runs)
        return self._encode_in_process(encodings, runs)

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self.load()
        with self._lock:
            self._in_flight += 1
        try:
            return self._encode_batch(texts)
        finally:
            with self._lock:
                self._in_flight -= 1
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
