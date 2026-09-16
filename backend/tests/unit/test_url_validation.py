"""Unit tests for GitHub URL validation -- the first cloning security gate."""

import pytest

from devpilot.core.errors import ValidationError
from devpilot.providers.vcs.github import parse_github_url


class TestAccepts:
    def test_canonical_url(self) -> None:
        parsed = parse_github_url("https://github.com/torvalds/linux")
        assert parsed.owner_name == "torvalds"
        assert parsed.repo_name == "linux"
        assert parsed.provider == "github"
        assert parsed.clone_url == "https://github.com/torvalds/linux.git"

    def test_strips_dot_git_suffix(self) -> None:
        parsed = parse_github_url("https://github.com/torvalds/linux.git")
        assert parsed.repo_name == "linux"

    def test_allows_safe_punctuation(self) -> None:
        parsed = parse_github_url("https://github.com/a-b/c.d_e")
        assert parsed.owner_name == "a-b"
        assert parsed.repo_name == "c.d_e"

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        parsed = parse_github_url("  https://github.com/o/r  ")
        assert parsed.repo_name == "r"


class TestRejects:
    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/o/r",  # not https
            "git://github.com/o/r",  # not https
            "ssh://git@github.com/o/r",  # not https
            "file:///etc/passwd",  # local file scheme
            "https://evil.com/o/r",  # wrong host
            "https://gitlab.com/o/r",  # wrong host
            "https://github.com/o/r/../../x",  # path traversal
            "https://user:pass@github.com/o/r",  # embedded credentials
            "https://github.com:22/o/r",  # explicit port
            "https://github.com/o",  # missing repo segment
            "https://github.com/o/r/extra",  # too many segments
            "https://github.com//r",  # empty owner
            "https://github.com/o/r\nhttps://evil.com/x/y",  # newline injection
            "",  # empty
            "not a url at all",  # garbage
        ],
    )
    def test_rejects_unsafe_url(self, url: str) -> None:
        with pytest.raises(ValidationError):
            parse_github_url(url)

    def test_rejects_control_characters(self) -> None:
        with pytest.raises(ValidationError):
            parse_github_url("https://github.com/o/\x00r")
