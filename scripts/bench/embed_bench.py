"""Peak resident set and throughput of the local indexer, measured, not estimated.

The script loads the real int8 ONNX artifact through the project's embedder,
builds 1000 chunks with the project's chunker from synthetic conversation turns,
encodes them, and then runs the idle unload. Resident set is read from
``/proc/self/status`` and ``getrusage``. Nothing here is estimated from a model
card.

The process imports ``src.worker`` first, so the baseline holds the same module
graph that the indexer runs inside. The import has no side effect: the worker
loop only starts from ``main()``.

Usage (from backend/):
    env -u PYTHONPATH uv run python ../scripts/bench/embed_bench.py

It writes JSON to stdout and progress to stderr.
"""
from __future__ import annotations

import argparse
import gc
import json
import resource
import sys
import time
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402

STATUS_PATH = Path("/proc/self/status")


def log(message: str) -> None:
    """Write one progress line to stderr."""
    print(f"[embed] {message}", file=sys.stderr, flush=True)


def rss_mb() -> float:
    """Return the current resident set size of this process in MiB."""
    for line in STATUS_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    raise RuntimeError("VmRSS is not present in /proc/self/status")


def peak_rss_mb() -> float:
    """Return the high water mark of the resident set in MiB."""
    for line in STATUS_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) / 1024.0
    raise RuntimeError("VmHWM is not present in /proc/self/status")


def maxrss_mb() -> float:
    """Return the peak resident set that the kernel accounted to this process."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


WORDS = (
    "account adapter agenda alert amount answer archive asset audit budget build "
    "calendar campaign capacity card channel chart check client cluster column "
    "command comment commit company config connect contact contract cost count "
    "cursor cycle dashboard data deadline delivery deploy detail device digest "
    "document domain draft driver email endpoint engine entry event export field "
    "file filter folder format gateway group handle header health history host "
    "image import index input invoice issue item job label latency layer ledger "
    "license limit line link list load local login market message metric mirror "
    "model module monitor month note object offer order output owner package page "
    "panel partner password path payment period phone pipeline plan platform "
    "policy portal price process product profile project prompt protocol query "
    "queue range rate record region release report request response result review "
    "role route rule run sample schedule scope score search secret section server "
    "service session setting share sheet signal source stage state status step "
    "storage stream summary support sync system table target task team template "
    "tenant test thread ticket time token tool topic trace track traffic trigger "
    "update upload user value vendor version view weekly window worker workflow "
    "write zone access action active address agent agreement analysis archive "
    "balance batch bonus budget cache catalog change charge choice claim client "
    "code compare confirm console content context control convert credit daily "
    "debit default define delete deploy design detail detect device dialog direct "
    "display domain download edit effort enable error estimate event exact expect "
    "explain export extend factor feature fetch figure final focus follow forum "
    "future gather generate goal grant graph guide handle health history hour "
    "impact inbox income inform input inside install intend invest invite join "
    "keep keyword label language launch layout learn lesson library limit log "
    "manage margin master matter measure meet member memory mention method metric "
    "minute model moment notice number object obtain offer office online open "
    "option order organize output overall panel parent partner pass patch pause "
    "pending people percent perform period permit person phone photo place plan "
    "point policy portal prepare present press price print prior private problem "
    "process produce product profit program project promise proper protect prove "
    "provide publish purchase purpose quality question quick quote raise random "
    "reach react ready reason receive recent record recover reduce refer refresh "
    "region register regular reject relate release remain remote remove render "
    "repeat replace reply report request require reset resolve resource respond "
    "restore result return review reward route sample save scale schedule scope "
    "score search second secret select send sense server service session setting "
    "settle share shift short show signal simple single size skill small source "
    "space speak special spend split stage start state status stay step still "
    "stock store story stream strong study submit success suggest summary supply "
    "support survey switch system table target task teach team technical tell "
    "template tenant term test thank theme theory thing think thread ticket time "
    "today token topic total touch trace track trade traffic train transfer "
    "travel treat trend trial trigger trouble trust truth turn type update upload "
    "usage useful user valid value vendor verify version video view visit voice "
    "volume vote wait watch water weekly whole window worker workflow world write"
).split()


def make_turn(rng: np.random.Generator, role: str, words: int) -> str:
    """Build one conversational turn of a given word count."""
    picked = rng.choice(WORDS, size=words, replace=True)
    body = " ".join(str(word) for word in picked)
    suffix = "." if role == "assistant" else "?"
    return f"{body}{suffix}"


def build_chunks(chunker, count: int, rng: np.random.Generator):
    """Segment synthetic turns until the chunker yields the requested windows."""
    from src.modules.memory.chunker import MemoryTurn

    turns = []
    index = 0
    while True:
        role = "user" if index % 2 == 0 else "assistant"
        length = int(rng.integers(20, 46)) if role == "user" else int(rng.integers(60, 141))
        turns.append(
            MemoryTurn(
                message_id=uuid.uuid4(),
                role=role,
                content=make_turn(rng, role, length),
            )
        )
        index += 1
        if index % 400 == 0:
            windows = chunker.segment(turns)
            if len(windows) >= count:
                return windows[:count]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261101)
    args = parser.parse_args()

    from src.config.settings import Settings
    from src.integrations.embedding.local import LocalEmbeddingModel
    from src.modules.memory.chunker import Chunker

    baseline_after_core = rss_mb()
    import src.worker  # noqa: F401  the module graph the indexer runs inside

    after_worker_import = rss_mb()

    settings = Settings()
    embedder = LocalEmbeddingModel(settings)
    chunker = Chunker(embedder, max_tokens=settings.EMBEDDING_MAX_TOKENS)
    rng = np.random.default_rng(args.seed)
    windows = build_chunks(chunker, args.chunks, rng)
    texts = [window.text for window in windows]
    token_counts = [embedder.count_tokens(text) for text in texts]
    after_chunks = rss_mb()
    log(f"built {len(texts)} chunks, mean {sum(token_counts) / len(token_counts):.0f} tokens")

    started = time.perf_counter()
    embedder.load()
    load_seconds = time.perf_counter() - started
    after_load = rss_mb()
    log(f"model loaded in {load_seconds:.2f}s, sha256={embedder._verified_sha256}")

    # One warmup run, so the first run does not pay the one time setup of the
    # ONNX session allocator. It is not part of the timed batch.
    embedder.embed_documents(texts[:2])

    started = time.perf_counter()
    vectors = embedder.embed_documents(texts)
    encode_seconds = time.perf_counter() - started
    peak = peak_rss_mb()
    peak_getrusage = maxrss_mb()
    log(f"encoded {len(vectors)} chunks in {encode_seconds:.2f}s")

    if len(vectors) != len(texts):
        raise SystemExit("the embedder returned a different number of vectors")
    if any(len(vector) != settings.EMBEDDING_DIMENSIONS for vector in vectors):
        raise SystemExit("a vector does not have the configured dimension")

    idle_seconds = settings.EMBEDDING_IDLE_UNLOAD_SECONDS
    unloaded = embedder.unload_if_idle(now=time.monotonic() + idle_seconds + 1.0)
    after_unload = rss_mb()
    session_after_unload = embedder.session
    gc.collect()
    time.sleep(0.5)
    after_gc = rss_mb()
    embedder.close()

    artifact = embedder.artifact_path
    import hashlib

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    report = {
        "seed": args.seed,
        "chunks": len(texts),
        "chunk_tokens_mean": sum(token_counts) / len(token_counts),
        "chunk_tokens_min": min(token_counts),
        "chunk_tokens_max": max(token_counts),
        "chunk_tokens_p50": sorted(token_counts)[len(token_counts) // 2],
        "rss_mb": {
            "baseline_after_core_imports": baseline_after_core,
            "after_worker_import": after_worker_import,
            "after_chunk_build": after_chunks,
            "after_model_load": after_load,
            "peak_encoding": peak,
            "peak_encoding_getrusage": peak_getrusage,
            "after_idle_unload": after_unload,
            "after_gc_collect": after_gc,
        },
        "model_load_seconds": load_seconds,
        "encode_seconds": encode_seconds,
        "chunks_per_second": len(texts) / encode_seconds,
        "unload_returned": unloaded,
        "session_is_none_after_unload": session_after_unload is None,
        "model": {
            "id": settings.EMBEDDING_MODEL,
            "dimensions": settings.EMBEDDING_DIMENSIONS,
            "artifact": str(artifact),
            "artifact_bytes": artifact.stat().st_size,
            "sha256_observed": digest,
            "sha256_pinned": settings.EMBEDDING_ARTIFACT_SHA256,
            "checksum_matches": digest == settings.EMBEDDING_ARTIFACT_SHA256,
            "revision": settings.EMBEDDING_ARTIFACT_REVISION,
        },
        "host_python": sys.version.split()[0],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
