"""Unit tests for the line-window chunker."""

import pytest

from devpilot.indexing.chunker import chunk_file


def _text(n: int) -> str:
    """Return text with n numbered lines: 'line1\\nline2\\n...'."""
    return "\n".join(f"line{i}" for i in range(1, n + 1))


class TestWindowing:
    def test_short_file_is_one_chunk_spanning_all_lines(self) -> None:
        chunks = chunk_file("f.py", _text(5), window_lines=40, overlap_lines=10)
        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 5
        assert chunks[0].chunk_index == 0

    def test_exact_window_is_one_chunk(self) -> None:
        chunks = chunk_file("f.py", _text(40), window_lines=40, overlap_lines=10)
        assert len(chunks) == 1
        assert chunks[0].end_line == 40

    def test_overlap_produces_expected_spans(self) -> None:
        # 100 lines, window 40, overlap 10 -> step 30: [1-40],[31-70],[61-100]
        chunks = chunk_file("f.py", _text(100), window_lines=40, overlap_lines=10)
        spans = [(c.start_line, c.end_line) for c in chunks]
        assert spans == [(1, 40), (31, 70), (61, 100)]

    def test_indices_are_sequential(self) -> None:
        chunks = chunk_file("f.py", _text(100), window_lines=40, overlap_lines=10)
        assert [c.chunk_index for c in chunks] == [0, 1, 2]

    def test_no_overlap(self) -> None:
        chunks = chunk_file("f.py", _text(60), window_lines=30, overlap_lines=0)
        spans = [(c.start_line, c.end_line) for c in chunks]
        assert spans == [(1, 30), (31, 60)]

    def test_content_matches_line_span(self) -> None:
        chunks = chunk_file("f.py", _text(50), window_lines=40, overlap_lines=10)
        first = chunks[0]
        assert first.content.splitlines()[0] == "line1"
        assert first.content.splitlines()[-1] == "line40"


class TestEdgeCases:
    def test_empty_file_yields_no_chunks(self) -> None:
        assert chunk_file("f.py", "", window_lines=40, overlap_lines=10) == []

    def test_whitespace_only_single_line(self) -> None:
        chunks = chunk_file("f.py", "   ", window_lines=40, overlap_lines=10)
        assert len(chunks) == 1
        assert chunks[0].start_line == 1

    def test_trailing_newline_does_not_create_empty_chunk(self) -> None:
        chunks = chunk_file("f.py", "a\nb\nc\n", window_lines=40, overlap_lines=10)
        assert len(chunks) == 1
        assert chunks[0].end_line == 3

    def test_last_window_reaching_end_is_not_duplicated(self) -> None:
        # 70 lines, window 40, overlap 10, step 30: [1-40],[31-70]; the second
        # window ends exactly at the end and must be the final chunk.
        chunks = chunk_file("f.py", _text(70), window_lines=40, overlap_lines=10)
        assert [(c.start_line, c.end_line) for c in chunks] == [(1, 40), (31, 70)]


class TestDeterminism:
    def test_same_input_same_checksum(self) -> None:
        a = chunk_file("f.py", _text(50), window_lines=40, overlap_lines=10)
        b = chunk_file("f.py", _text(50), window_lines=40, overlap_lines=10)
        assert [c.content_sha256 for c in a] == [c.content_sha256 for c in b]

    def test_different_path_changes_checksum(self) -> None:
        a = chunk_file("a.py", _text(10), window_lines=40, overlap_lines=10)
        b = chunk_file("b.py", _text(10), window_lines=40, overlap_lines=10)
        assert a[0].content_sha256 != b[0].content_sha256

    def test_different_content_changes_checksum(self) -> None:
        a = chunk_file("f.py", _text(10), window_lines=40, overlap_lines=10)
        b = chunk_file("f.py", _text(11), window_lines=40, overlap_lines=10)
        assert a[0].content_sha256 != b[0].content_sha256


class TestValidation:
    def test_zero_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="window_lines must be positive"):
            chunk_file("f.py", _text(10), window_lines=0, overlap_lines=0)

    def test_overlap_equal_to_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="overlap_lines"):
            chunk_file("f.py", _text(10), window_lines=40, overlap_lines=40)

    def test_overlap_greater_than_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="overlap_lines"):
            chunk_file("f.py", _text(10), window_lines=40, overlap_lines=50)
