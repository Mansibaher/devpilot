"""File discovery and filtering rules.

A file is kept only if it passes every gate here. The gates run cheapest-first
so that most files are rejected by a path check before anything reads their
bytes. All of this is pure: it takes a path and, for the binary check, a byte
sample, and returns a decision. No walking of a real tree happens here -- the
indexing service does that and calls ``should_index`` per file.

The design is allowlist-oriented for source types (an unknown extension is
skipped, not kept) but has an explicit filename allowlist so useful
extensionless files -- Dockerfile, Makefile, README, LICENSE, and a few
dotfiles -- are not lost to that default. Secrets are dropped entirely: not
their contents, not even their metadata, because a browsable list of a repo's
secret filenames is itself a small information leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from devpilot.indexing.languages import detect_language

# Directory names that are excluded wherever they appear in the tree. Matched
# against every path segment, so ``a/node_modules/b/c.js`` is excluded by the
# ``node_modules`` segment regardless of depth.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        ".gradle",
        ".terraform",
        "bin",
        "obj",
        ".tox",
        "coverage",
        ".cache",
    }
)

# Exact filenames treated as secrets and dropped entirely. ``.env`` is here but
# ``.env.example`` is on the allowlist below, so a template survives while the
# real thing never does.
_SECRET_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        "credentials",
        "credentials.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "secrets.yaml",
        "secrets.yml",
    }
)

# Filename suffixes treated as secret/credential material and dropped.
_SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
    ".crt",
    ".cer",
    ".der",
)

# Secret filename prefixes (private keys of various tools).
_SECRET_PREFIXES: tuple[str, ...] = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")

# Large generated lockfiles: explicitly listed, not matched by a blanket
# ``*.lock`` rule, so that genuinely useful lockfiles such as ``Cargo.lock``
# remain eligible and are filtered only by the size and binary gates.
_EXCLUDED_LOCKFILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "npm-shrinkwrap.json",
        "composer.lock",
    }
)

# Generated or minified artefacts identified by suffix.
_EXCLUDED_SUFFIXES: tuple[str, ...] = (".min.js", ".min.css", ".map", ".lock.hcl")

# Extensionless / special files worth keeping even though the source allowlist
# would otherwise skip them. Prefixes cover the ``README.*`` / ``LICENSE.*``
# family requested in the design.
_ALLOWED_FILENAMES: frozenset[str] = frozenset(
    {
        "Dockerfile",
        "Makefile",
        "makefile",
        "GNUmakefile",
        "CMakeLists.txt",
        ".gitignore",
        ".editorconfig",
        ".env.example",
        ".dockerignore",
        "Rakefile",
        "Gemfile",
    }
)
_ALLOWED_FILENAME_PREFIXES: tuple[str, ...] = (
    "README",
    "LICENSE",
    "LICENCE",
    "COPYING",
    "Dockerfile.",
)

# Number of leading bytes inspected for a NUL byte to classify a file as binary.
_BINARY_SNIFF_BYTES = 8192


class SkipReason:
    """Stable reasons a file was skipped, for logging and tests."""

    EXCLUDED_DIR = "excluded_dir"
    SECRET = "secret"
    LOCKFILE = "lockfile"
    GENERATED = "generated"
    UNSUPPORTED = "unsupported_type"
    TOO_LARGE = "too_large"
    BINARY = "binary"


@dataclass(frozen=True)
class FileDecision:
    """The outcome of filtering one file.

    Attributes:
        keep: Whether the file should be indexed.
        reason: When skipped, a ``SkipReason``; ``None`` when kept.
        language: When kept, the detected language or ``None`` if unknown.

    """

    keep: bool
    reason: str | None = None
    language: str | None = None


def _in_excluded_dir(path: PurePosixPath) -> bool:
    """Return True if any parent segment is an excluded directory."""
    return any(part in _EXCLUDED_DIRS for part in path.parts[:-1])


def _is_secret(name: str) -> bool:
    """Return True if the filename denotes secret or credential material."""
    if name in _SECRET_FILENAMES:
        return True
    if name.lower().endswith(_SECRET_SUFFIXES):
        return True
    return name.startswith(_SECRET_PREFIXES)


def _is_allowlisted_name(name: str) -> bool:
    """Return True for extensionless/special files worth keeping by name."""
    if name in _ALLOWED_FILENAMES:
        return True
    return name.startswith(_ALLOWED_FILENAME_PREFIXES)


def looks_binary(sample: bytes) -> bool:
    """Classify a byte sample as binary.

    A NUL byte in the first several kilobytes is the standard, cheap signal that
    a file is not text. It catches images, compiled objects, and fonts
    regardless of extension, which an extension check alone would miss.

    Args:
        sample: The leading bytes of the file.

    Returns:
        True if the sample appears to be binary.

    """
    return b"\x00" in sample[:_BINARY_SNIFF_BYTES]


def classify_path(relative_path: str) -> FileDecision:
    """Decide whether a file should be indexed, from its path alone.

    This runs every gate that does not require reading the file: directory
    exclusion, secrets, lockfiles, generated artefacts, and type support. The
    caller applies the size and binary gates separately, since those need the
    file's bytes. A kept decision here means "eligible, pending size and binary
    checks".

    Args:
        relative_path: Repository-relative POSIX path.

    Returns:
        A :class:`FileDecision`. When ``keep`` is True the ``language`` field is
        populated (possibly ``None`` for a recognised-but-unmapped file).

    """
    path = PurePosixPath(relative_path)
    name = path.name

    if _in_excluded_dir(path):
        return FileDecision(keep=False, reason=SkipReason.EXCLUDED_DIR)
    if _is_secret(name):
        return FileDecision(keep=False, reason=SkipReason.SECRET)
    if name in _EXCLUDED_LOCKFILES:
        return FileDecision(keep=False, reason=SkipReason.LOCKFILE)
    if name.endswith(_EXCLUDED_SUFFIXES):
        return FileDecision(keep=False, reason=SkipReason.GENERATED)

    language = detect_language(name)
    if language is None and not _is_allowlisted_name(name):
        return FileDecision(keep=False, reason=SkipReason.UNSUPPORTED)

    return FileDecision(keep=True, language=language)
