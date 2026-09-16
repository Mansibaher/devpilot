"""Repository URL parsing and validation.

This is the first line of the cloning security model. Every URL a user submits
is parsed and validated here before anything touches the network, and only a
narrow, canonical shape survives: an ``https://github.com/owner/repo`` URL whose
owner and repo match a strict character set. Everything else -- other schemes,
other hosts, embedded credentials, path traversal, control characters -- is
rejected with a domain error, never passed onward.

Parsing uses ``urllib.parse`` rather than a regex over the raw string, so a
crafted input cannot smuggle a second scheme or host past a permissive pattern.
The result is a ``ParsedRepository`` carrying the pieces the rest of the system
needs, plus a normalised clone URL that is safe to hand to git as a single
argument-list element.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from devpilot.core.errors import ValidationError

# Owner and repo segments: letters, digits, and a few safe punctuation marks.
# No slashes, no dots-only, no path traversal, bounded length. This is what
# keeps the parsed pieces safe to reassemble into a clone URL.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

_GITHUB_HOST = "github.com"


@dataclass(frozen=True)
class ParsedRepository:
    """A validated, normalised repository reference.

    Attributes:
        provider: The source host identifier; ``github`` in M3.
        owner_name: The repository owner segment.
        repo_name: The repository name segment, without a ``.git`` suffix.
        clone_url: The canonical HTTPS URL, safe to pass to git as one argument.

    """

    provider: str
    owner_name: str
    repo_name: str
    clone_url: str


def parse_github_url(raw_url: str) -> ParsedRepository:
    """Validate a GitHub HTTPS URL and return its normalised parts.

    Args:
        raw_url: The URL a user submitted.

    Returns:
        A :class:`ParsedRepository`.

    Raises:
        ValidationError: If the URL is not a well-formed public GitHub HTTPS URL
            of the form ``https://github.com/owner/repo``. The message is
            deliberately generic and does not echo the raw input back.

    """
    candidate = raw_url.strip()
    if not candidate or any(ord(ch) < 0x20 for ch in candidate):
        raise ValidationError("A valid GitHub repository URL is required.")

    parts = urlsplit(candidate)

    # Scheme and host are pinned. http, git, ssh, file, and any other host are
    # rejected here, which is what closes the SSRF and protocol-smuggling surface.
    if parts.scheme != "https":
        raise ValidationError("Repository URL must use https.")
    if parts.hostname != _GITHUB_HOST:
        raise ValidationError("Only github.com repositories are supported.")
    # A username or password component (https://user:pass@host) is refused
    # outright: credentials never belong in a stored clone URL.
    if parts.username or parts.password or parts.port:
        raise ValidationError("Repository URL must not contain credentials or a port.")

    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) != 2:
        raise ValidationError("Repository URL must be of the form https://github.com/owner/repo.")

    owner_name, repo_name = segments
    if repo_name.endswith(".git"):
        repo_name = repo_name[: -len(".git")]

    if not _SEGMENT.match(owner_name) or not _SEGMENT.match(repo_name):
        raise ValidationError("Repository owner and name contain invalid characters.")

    clone_url = f"https://{_GITHUB_HOST}/{owner_name}/{repo_name}.git"
    return ParsedRepository(
        provider="github",
        owner_name=owner_name,
        repo_name=repo_name,
        clone_url=clone_url,
    )
