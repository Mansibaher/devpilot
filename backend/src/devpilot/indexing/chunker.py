"""Line-window chunking.

Splits a file's text into overlapping fixed-size windows of lines. Pure: it
takes text and two integers and returns chunks, with no database, model, or I/O.
That keeps the interesting logic -- window math, line-number accuracy,
determinism -- testable in milliseconds, and lets the worker and any future
chunker share one interface.

Line numbers are 1-based and inclusive, because the whole product is
source-grounded citations: a chunk must map to a line range a user can open in
the file. Token windows would straddle line boundaries and make citations lie,
so the unit here is the line.

Each chunk carries a deterministic content checksum derived from the path and
line span as well as the text, so that re-chunking an unchanged file yields
identical checksums and the pipeline can recognise unchanged content.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One line-window chunk of a file.

    Attributes:
        chunk_index: Zero-based ordinal of this chunk within the file.
        start_line: First line of the chunk, 1-based inclusive.
        end_line: Last line of the chunk, 1-based inclusive.
        content: The chunk's exact source text.
        content_sha256: Deterministic checksum over path, span, and content.

    """

    chunk_index: int
    start_line: int
    end_line: int
    content: str
    content_sha256: str


def _checksum(path: str, start_line: int, end_line: int, content: str) -> str:
    """Return a deterministic hex checksum identifying a chunk's content.

    Includes the path and line span, not just the text, so two identical code
    fragments at different locations get different checksums -- the checksum is
    a content-and-position identity, distinct from the row's UUID identity.
    """
    digest = hashlib.sha256()
    digest.update(f"{path}\n{start_line}\n{end_line}\n".encode())
    digest.update(content.encode("utf-8"))
    return digest.hexdigest()


def chunk_file(path: str, text: str, *, window_lines: int, overlap_lines: int) -> list[Chunk]:
    """Split file text into overlapping line-window chunks.

    A file shorter than one window becomes a single chunk spanning the whole
    file. An empty file yields no chunks. Successive windows advance by
    ``window_lines - overlap_lines`` lines, so adjacent chunks share
    ``overlap_lines`` lines and a definition near a window boundary appears in
    at least one chunk alongside its context.

    Args:
        path: Repository-relative path, used only for the checksum.
        text: The full file contents.
        window_lines: Lines per window. Must be > 0.
        overlap_lines: Lines shared between adjacent windows. Must satisfy
            ``0 <= overlap_lines < window_lines``.

    Returns:
        The file's chunks in order.

    Raises:
        ValueError: If the window/overlap relationship is invalid. This mirrors
            the settings validator so the invariant holds even if the chunker
            is called directly.

    """
    if window_lines <= 0:
        raise ValueError("window_lines must be positive")
    if overlap_lines < 0 or overlap_lines >= window_lines:
        raise ValueError("overlap_lines must satisfy 0 <= overlap_lines < window_lines")

    if not text:
        return []

    lines = text.splitlines()
    if not lines:
        return []

    step = window_lines - overlap_lines
    chunks: list[Chunk] = []
    index = 0
    start = 0  # 0-based index into lines
    total = len(lines)

    while start < total:
        end = min(start + window_lines, total)  # exclusive
        content = "\n".join(lines[start:end])
        start_line = start + 1  # to 1-based inclusive
        end_line = end
        chunks.append(
            Chunk(
                chunk_index=index,
                start_line=start_line,
                end_line=end_line,
                content=content,
                content_sha256=_checksum(path, start_line, end_line, content),
            )
        )
        index += 1
        if end == total:
            break  # last window reached the end; do not emit a trailing overlap-only chunk
        start += step

    return chunks
