"""A minimal, deliberately hardened wrapper around the git CLI.

Every property here is a security control, not a convenience:

- ``shell=False`` always, with git invoked as an argument list. The clone URL,
  already validated by the URL parser, is still passed as a list element and
  never interpolated into a string, so there is no shell for an injection to
  target even in principle.
- The child environment is scrubbed to a small allowlist. No ambient token,
  proxy, or credential variable is inherited, so nothing can leak into the
  subprocess or out through it.
- ``protocol.allowed=https`` forbids git from following a redirect into
  ``file://`` or ``ext::``, closing a local-file and command-execution vector.
- ``GIT_TERMINAL_PROMPT=0`` means a private or mistyped repository fails fast
  instead of blocking on a credential prompt.
- Every invocation has a timeout, so a decompression bomb or a hostile server
  cannot hang a worker indefinitely.

The clone is shallow and blobless (``--depth 1 --filter=blob:none``): history is
bounded, and blobs are fetched lazily only for the files that survive filtering.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a git command fails, times out, or git is unavailable."""


# The only environment variables the git subprocess is allowed to see. Anything
# carrying credentials or proxy configuration is intentionally excluded.
_GIT_SAFE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",  # never prompt for credentials
    "GIT_CONFIG_NOSYSTEM": "1",  # ignore /etc/gitconfig
    "HOME": "/tmp",  # ignore any user ~/.gitconfig
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
}

# Prepended to every git call. Disables global/system config that could alter
# behaviour, and restricts the transport to https so no redirect can escape it.
_GIT_HARDENING_FLAGS = [
    "-c",
    "protocol.allowed=https",
    "-c",
    "credential.helper=",
]


class GitClient:
    """Runs the handful of git commands indexing needs, safely."""

    def __init__(self, timeout_seconds: int) -> None:
        """Construct the client.

        Args:
            timeout_seconds: Wall-clock ceiling applied to every git command.

        """
        self._timeout = timeout_seconds

    def _run(self, args: list[str], *, cwd: Path | None = None) -> str:
        """Run one git command and return its stdout.

        Args:
            args: git arguments, excluding the ``git`` executable itself.
            cwd: Working directory, when the command operates on a clone.

        Returns:
            Captured stdout, stripped.

        Raises:
            GitError: On non-zero exit, timeout, or a missing git binary. The
                message never includes the process environment.

        """
        command = ["git", *_GIT_HARDENING_FLAGS, *args]
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=_GIT_SAFE_ENV,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git timed out after {self._timeout}s") from exc
        except FileNotFoundError as exc:
            raise GitError("git executable not found") from exc

        if result.returncode != 0:
            # stderr is truncated and carries no environment; safe to surface.
            detail = (result.stderr or "").strip()[:500]
            raise GitError(f"git {args[0]} failed: {detail}")
        return result.stdout.strip()

    def shallow_clone(self, clone_url: str, destination: Path) -> None:
        """Clone a single commit of the default branch into an empty directory.

        Args:
            clone_url: A validated HTTPS clone URL.
            destination: An existing, empty, isolated directory.

        Raises:
            GitError: If the clone fails or times out.

        """
        self._run(
            [
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--filter=blob:none",
                "--no-tags",
                clone_url,
                str(destination),
            ]
        )

    def head_sha(self, repo_dir: Path) -> str:
        """Return the HEAD commit SHA of a clone.

        Args:
            repo_dir: The cloned repository directory.

        Returns:
            The full 40-character commit SHA.

        """
        return self._run(["rev-parse", "HEAD"], cwd=repo_dir)

    def current_branch(self, repo_dir: Path) -> str | None:
        """Return the checked-out branch name, or None if detached.

        Args:
            repo_dir: The cloned repository directory.

        Returns:
            The branch name, or ``None`` when HEAD is detached.

        """
        name = self._run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
        return None if name == "HEAD" else name
