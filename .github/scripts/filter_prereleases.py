"""Revert pre-commit autoupdate bumps that introduce pre-release versions.

``pre-commit autoupdate`` picks the latest git tag for each repo, which can
be an alpha/beta/rc/dev release (e.g. ``isort 9.0.0a3``). Run this after
autoupdate to restore the previous rev whenever the new rev looks like a
pre-release.

Only reverts when the *change* introduces a pre-release marker — revs that
were already pinned to a pre-release (e.g. ``prettier v4.0.0-alpha.8``) and
weren't touched by autoupdate are left alone.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PRERELEASE_RE = re.compile(
    r"(?:(?<=\d)(?:a|b|rc)\d+)"  # PEP 440: 9.0.0a3, 1.2b1, 2.0rc1
    r"|(?:\.dev\d+)"  # PEP 440 dev: 1.0.dev1
    r"|(?:[-.](?:alpha|beta|rc|pre|dev)\b)",  # SemVer-ish: v4.0.0-alpha.8
    re.IGNORECASE,
)

REPO_RE = re.compile(r"^(\s*-\s*repo:\s*)(\S+)\s*$")
REV_RE = re.compile(r"^(\s*rev:\s*)(\S+)\s*$")


def is_prerelease(rev: str) -> bool:
    return bool(PRERELEASE_RE.search(rev))


def parse_revs(text: str) -> dict[str, str]:
    """Return {repo_url: rev} for each repo entry in a pre-commit config."""
    revs: dict[str, str] = {}
    current_repo: str | None = None
    for line in text.splitlines():
        m = REPO_RE.match(line)
        if m:
            current_repo = m.group(2)
            continue
        m = REV_RE.match(line)
        if m and current_repo is not None:
            revs[current_repo] = m.group(2)
            current_repo = None
    return revs


def restore_revs(text: str, restore: dict[str, str]) -> str:
    """Rewrite ``rev:`` lines for repos listed in ``restore``."""
    lines = text.splitlines(keepends=True)
    current_repo: str | None = None
    for i, line in enumerate(lines):
        m = REPO_RE.match(line)
        if m:
            current_repo = m.group(2)
            continue
        m = REV_RE.match(line)
        if m and current_repo in restore:
            prefix = m.group(1)
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f"{prefix}{restore[current_repo]}{newline}"
            current_repo = None
    return "".join(lines)


def get_previous_config(path: Path) -> str:
    """Read the pre-update version of the config from git HEAD."""
    return subprocess.check_output(
        ("git", "show", f"HEAD:{path}"),
        encoding="UTF-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".pre-commit-config.yaml"),
        help="Path to .pre-commit-config.yaml",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.config.exists():
        logger.error("Config not found: %s", args.config)
        return 1

    new_text = args.config.read_text()
    new_revs = parse_revs(new_text)
    old_revs = parse_revs(get_previous_config(args.config))

    restore: dict[str, str] = {}
    for repo, new_rev in new_revs.items():
        old_rev = old_revs.get(repo)
        if old_rev is None or new_rev == old_rev:
            continue
        if is_prerelease(new_rev) and not is_prerelease(old_rev):
            logger.info("Reverting %s: %s -> %s (pre-release)", repo, new_rev, old_rev)
            restore[repo] = old_rev

    if not restore:
        logger.info("No pre-release bumps to revert.")
        return 0

    args.config.write_text(restore_revs(new_text, restore))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
