"""Unit tests for language detection."""

import pytest

from devpilot.indexing.languages import detect_language


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("app.py", "python"),
        ("index.ts", "typescript"),
        ("main.go", "go"),
        ("lib.rs", "rust"),
        ("Query.java", "java"),
        ("styles.css", "css"),
        ("data.json", "json"),
        ("config.yaml", "yaml"),
        ("README.md", "markdown"),
        ("Dockerfile", "dockerfile"),
        ("Makefile", "makefile"),
        (".gitignore", "gitignore"),
        (".editorconfig", "editorconfig"),
    ],
)
def test_detects_known(filename: str, expected: str) -> None:
    assert detect_language(filename) == expected


@pytest.mark.parametrize("filename", ["mystery.xyz", "noextension", "archive.zzz", "LICENSE"])
def test_unknown_returns_none(filename: str) -> None:
    assert detect_language(filename) is None


def test_exact_filename_beats_extension() -> None:
    # A bare Dockerfile resolves by name; Dockerfile.dev falls through to
    # extension lookup and finds nothing mapped for ".dev".
    assert detect_language("Dockerfile") == "dockerfile"
    assert detect_language("Dockerfile.dev") is None


def test_case_insensitive_extension() -> None:
    assert detect_language("APP.PY") == "python"
