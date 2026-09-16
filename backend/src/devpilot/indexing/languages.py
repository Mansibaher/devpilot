"""Language detection by file name.

Deterministic and dependency-free: a static extension-to-language map plus a
small set of well-known filenames. No content sniffing, no ``linguist``, no
subprocess. This is deliberately boring -- a correct lookup table beats a clever
heuristic for populating a metadata column, and it cannot fail at runtime.

Ambiguous extensions resolve to a single documented default (``.h`` to C, not
C++), because the language column is a hint for later retrieval, not a
compiler's decision.
"""

# Extension (lower-case, including the dot) to language name.
_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",  # documented default; could be C or C++ but we pick one
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".m": "objective-c",
    ".mm": "objective-c",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".xml": "xml",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".r": "r",
    ".jl": "julia",
    ".lua": "lua",
    ".pl": "perl",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure",
    ".hs": "haskell",
    ".dart": "dart",
    ".proto": "protobuf",
    ".tf": "terraform",
    ".gradle": "gradle",
}

# Exact filenames (case-sensitive where it matters) that carry no extension but
# have a well-known language. Kept separate from the allowlist in file_filter:
# this map answers "what language", that set answers "keep it at all".
_FILENAME_LANGUAGES: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "Makefile": "makefile",
    "makefile": "makefile",
    "GNUmakefile": "makefile",
    "Rakefile": "ruby",
    "Gemfile": "ruby",
    "CMakeLists.txt": "cmake",
    ".gitignore": "gitignore",
    ".editorconfig": "editorconfig",
}


def detect_language(filename: str) -> str | None:
    """Return the language for a file name, or None when unknown.

    Exact-filename matches win over extension matches, so ``Dockerfile.dev``
    still resolves by extension while a bare ``Dockerfile`` resolves by name.

    Args:
        filename: The base name of the file (no directory component required).

    Returns:
        A language string, or ``None`` if the name is not recognised.

    """
    if filename in _FILENAME_LANGUAGES:
        return _FILENAME_LANGUAGES[filename]
    dot = filename.rfind(".")
    if dot <= 0:  # no extension, or a dotfile with no further extension
        return None
    return _EXTENSION_LANGUAGES.get(filename[dot:].lower())
