"""Check for newer tags of apps in custom_apps.json and update config files."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AppEntry:
    url: str
    branch: str


def parse_url(url: str) -> tuple[str, str]:
    """Extract (org, repo) from a GitHub URL.

    >>> parse_url("https://github.com/frappe/erpnext")
    ('frappe', 'erpnext')
    """
    parts = url.rstrip("/").split("/")
    return parts[-2], parts[-1]


def extract_major_version(branch: str) -> str | None:
    """Return the major version string from a branch/tag name.

    >>> extract_major_version("v15.62.0")
    '15'
    >>> extract_major_version("version-15")
    '15'
    >>> extract_major_version("v1.20.1")
    '1'
    >>> extract_major_version("develop")
    """
    m = re.match(r"v?(\d+)", branch)
    if m:
        return m.group(1)
    m = re.match(r"version-(\d+)", branch)
    if m:
        return m.group(1)
    return None


def get_latest_tag(url: str, major_version: str) -> str | None:
    """Query git ls-remote for the latest semver tag matching the major version."""
    pattern = f"v{major_version}.*"
    try:
        output = subprocess.check_output(
            (
                "git",
                "-c",
                "versionsort.suffix=-",
                "ls-remote",
                "--refs",
                "--tags",
                "--sort=v:refname",
                url,
                pattern,
            ),
            encoding="UTF-8",
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        logger.warning("Failed to query tags for %s", url)
        return None

    refs = output.split()
    # refs is [hash, ref, hash, ref, ...] — take every second element starting from index 1
    tag_refs = refs[1::2]
    if not tag_refs:
        logger.warning("No tags found for %s matching v%s.*", url, major_version)
        return None

    ref = tag_refs[-1]
    matches = re.findall(pattern, ref)
    if not matches:
        logger.warning("Cannot parse tag from ref %s", ref)
        return None
    return matches[0]


def load_custom_apps(path: Path) -> list[AppEntry]:
    """Load custom_apps.json and return a list of AppEntry."""
    with open(path) as f:
        data = json.load(f)
    return [AppEntry(url=entry["url"], branch=entry["branch"]) for entry in data]


def save_custom_apps(path: Path, entries: list[AppEntry]) -> None:
    """Write entries back to custom_apps.json with indent=2 (prettier-compatible)."""
    data = [{"url": e.url, "branch": e.branch} for e in entries]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def check_app_updates(entries: list[AppEntry], dry_run: bool = False) -> list[str]:
    """Check each app for updates. Returns list of update descriptions."""
    updates = []
    for entry in entries:
        org, repo = parse_url(entry.url)
        major = extract_major_version(entry.branch)
        if major is None:
            logger.info(
                "Skipping %s/%s — cannot determine major version from '%s'",
                org,
                repo,
                entry.branch,
            )
            continue

        # Skip branch-style refs like "version-15" — no tag to compare
        if entry.branch.startswith("version-"):
            logger.info(
                "Skipping %s/%s — using branch ref '%s'", org, repo, entry.branch
            )
            continue

        latest = get_latest_tag(entry.url, major)
        if latest is None:
            continue

        if latest != entry.branch:
            msg = f"{org}/{repo}: {entry.branch} -> {latest}"
            logger.info("Update available: %s", msg)
            updates.append(msg)
            if not dry_run:
                entry.branch = latest
        else:
            logger.info("%s/%s: %s is already latest", org, repo, entry.branch)

    return updates


def update_build_push_yml(
    path: Path,
    frappe_tag: str | None = None,
    erpnext_tag: str | None = None,
) -> list[str]:
    """Update FRAPPE_BUILD and FRAPPE_BRANCH in build_push.yml."""
    if not path.exists():
        logger.warning("build_push.yml not found at %s", path)
        return []

    text = path.read_text()
    updates = []

    if erpnext_tag:
        new_text = re.sub(
            r"FRAPPE_BUILD=v[\d.]+",
            f"FRAPPE_BUILD={erpnext_tag}",
            text,
        )
        if new_text != text:
            updates.append(f"FRAPPE_BUILD -> {erpnext_tag}")
            text = new_text

    if frappe_tag:
        new_text = re.sub(
            r"FRAPPE_BRANCH=v[\d.]+",
            f"FRAPPE_BRANCH={frappe_tag}",
            text,
        )
        if new_text != text:
            updates.append(f"FRAPPE_BRANCH -> {frappe_tag}")
            text = new_text

    if updates:
        path.write_text(text)

    return updates


def update_example_env(path: Path, erpnext_tag: str) -> bool:
    """Update ERPNEXT_VERSION in example.env."""
    if not path.exists():
        logger.warning("example.env not found at %s", path)
        return False

    text = path.read_text()
    new_text = re.sub(
        r"ERPNEXT_VERSION=v[\d.]+",
        f"ERPNEXT_VERSION={erpnext_tag}",
        text,
    )
    if new_text != text:
        path.write_text(new_text)
        return True
    return False


def update_pwd_yml(path: Path, erpnext_tag: str) -> bool:
    """Update frappe/erpnext:vX.Y.Z references in pwd.yml."""
    if not path.exists():
        logger.warning("pwd.yml not found at %s", path)
        return False

    text = path.read_text()
    new_text = re.sub(
        r"frappe/erpnext:v[\d.]+",
        f"frappe/erpnext:{erpnext_tag}",
        text,
    )
    if new_text != text:
        path.write_text(new_text)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check for updates without modifying files",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Path to the repository root (default: current directory)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    root = args.repo_root
    custom_apps_path = root / "custom_apps.json"
    build_push_path = root / ".github" / "workflows" / "build_push.yml"
    example_env_path = root / "example.env"
    pwd_yml_path = root / "pwd.yml"

    if not custom_apps_path.exists():
        logger.error("custom_apps.json not found at %s", custom_apps_path)
        return 1

    entries = load_custom_apps(custom_apps_path)
    all_updates: list[str] = []

    # Check app updates
    app_updates = check_app_updates(entries, dry_run=args.dry_run)
    all_updates.extend(app_updates)

    # Find erpnext and frappe tags for build config updates
    erpnext_tag = None
    frappe_tag = None
    for entry in entries:
        _, repo = parse_url(entry.url)
        if repo == "erpnext":
            erpnext_tag = entry.branch
        # frappe framework is not in custom_apps.json — query it directly
    frappe_major = extract_major_version(erpnext_tag) if erpnext_tag else None
    if frappe_major:
        frappe_tag = get_latest_tag("https://github.com/frappe/frappe", frappe_major)
        logger.info("Latest frappe tag for v%s: %s", frappe_major, frappe_tag)

    if not args.dry_run:
        # Save updated custom_apps.json
        if app_updates:
            save_custom_apps(custom_apps_path, entries)
            logger.info("Updated custom_apps.json")

        # Update build_push.yml
        if erpnext_tag and frappe_tag:
            yml_updates = update_build_push_yml(
                build_push_path, frappe_tag, erpnext_tag
            )
            all_updates.extend(yml_updates)

        # Update example.env
        if erpnext_tag:
            if update_example_env(example_env_path, erpnext_tag):
                all_updates.append(f"example.env ERPNEXT_VERSION -> {erpnext_tag}")

        # Update pwd.yml
        if erpnext_tag:
            if update_pwd_yml(pwd_yml_path, erpnext_tag):
                all_updates.append(f"pwd.yml erpnext image -> {erpnext_tag}")

    if all_updates:
        print("Updates found:")
        for update in all_updates:
            print(f"  - {update}")
    else:
        print("All apps are up to date.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
