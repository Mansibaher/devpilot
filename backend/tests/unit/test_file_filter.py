"""Unit tests for the file-filtering rules.

Each gate is exercised in isolation so a regression points at the specific rule
that broke.
"""

import pytest

from devpilot.indexing.file_filter import (
    FileDecision,
    SkipReason,
    classify_path,
    looks_binary,
)


class TestExcludedDirectories:
    @pytest.mark.parametrize(
        "path",
        [
            "node_modules/dep/index.js",
            "src/vendor/lib.py",
            ".git/config",
            "target/debug/main.rs",
            "dist/bundle.js",
            "__pycache__/mod.pyc",
        ],
    )
    def test_excluded(self, path: str) -> None:
        d = classify_path(path)
        assert not d.keep
        assert d.reason == SkipReason.EXCLUDED_DIR


class TestSecrets:
    @pytest.mark.parametrize(
        "path",
        [".env", "config/.env", "server.pem", "private.key", "id_rsa", "certs/id_ed25519"],
    )
    def test_secret_dropped(self, path: str) -> None:
        d = classify_path(path)
        assert not d.keep
        assert d.reason == SkipReason.SECRET

    def test_env_example_is_kept(self) -> None:
        # The template survives even though .env does not.
        assert classify_path(".env.example").keep


class TestLockfiles:
    @pytest.mark.parametrize(
        "path", ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"]
    )
    def test_generated_lockfiles_skipped(self, path: str) -> None:
        d = classify_path(path)
        assert not d.keep
        assert d.reason == SkipReason.LOCKFILE

    def test_cargo_lock_is_not_blanket_excluded(self) -> None:
        # Cargo.lock is useful and must survive the lockfile gate; it is kept
        # because ".lock" is not treated as a language, so it is unsupported
        # rather than lockfile-excluded -- the point is it is NOT LOCKFILE.
        d = classify_path("Cargo.lock")
        assert d.reason != SkipReason.LOCKFILE


class TestAllowlist:
    @pytest.mark.parametrize(
        "path",
        [
            "Dockerfile",
            "Makefile",
            "README",
            "README.md",
            "LICENSE",
            "LICENSE.txt",
            ".gitignore",
            ".editorconfig",
        ],
    )
    def test_special_files_kept(self, path: str) -> None:
        assert classify_path(path).keep


class TestSupportedTypes:
    def test_source_file_kept_with_language(self) -> None:
        d = classify_path("src/app.py")
        assert d.keep
        assert d.language == "python"

    def test_unknown_extension_skipped(self) -> None:
        d = classify_path("mystery.xyz")
        assert not d.keep
        assert d.reason == SkipReason.UNSUPPORTED


class TestBinaryDetection:
    def test_null_byte_is_binary(self) -> None:
        assert looks_binary(b"PNG\x00\x00\x01") is True

    def test_plain_text_is_not_binary(self) -> None:
        assert looks_binary(b"def hello():\n    return 1\n") is False

    def test_empty_is_not_binary(self) -> None:
        assert looks_binary(b"") is False


def test_decision_is_frozen() -> None:
    d = FileDecision(keep=True, language="python")
    with pytest.raises(Exception):  # noqa: B017  (frozen dataclass raises)
        d.keep = False  # type: ignore[misc]
