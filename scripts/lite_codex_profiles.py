#!/usr/bin/env python3
"""Build a read-only federated Codex session tree for Agent Sessions Lite.

The upstream app currently accepts one Codex sessions root. This helper lets a user
keep multiple CODEX_HOME directories (for example work/personal/geo) while presenting
a single compatible tree to Agent Sessions.

It NEVER edits the source Codex homes. The federation contains symlinks only and can be
recreated at any time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_CONFIG = Path("~/.config/agent-sessions-lite/codex-profiles.json").expanduser()
DEFAULT_ROOT = Path("~/Library/Application Support/AgentSessionsLite/CodexFederated").expanduser()
MANIFEST = ".agent-sessions-lite-manifest.json"


@dataclass(frozen=True)
class Profile:
    name: str
    home: Path


def slug(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    if not out:
        raise ValueError("profile name must contain at least one letter or number")
    return out.lower()


def load_profiles(path: Path) -> list[Profile]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("config must be a non-empty JSON array")

    profiles: list[Profile] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each profile must be an object with name and home")
        name = slug(str(item.get("name", "")))
        if name in seen:
            raise ValueError(f"duplicate profile name: {name}")
        home_raw = str(item.get("home", "")).strip()
        if not home_raw:
            raise ValueError(f"profile {name!r} is missing home")
        # Resolving SOURCE homes is intentional: symlinks should point at the real files.
        home = Path(os.path.expandvars(os.path.expanduser(home_raw))).resolve()
        profiles.append(Profile(name=name, home=home))
        seen.add(name)
    return profiles


def rollout_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return (
        p
        for p in root.rglob("*.jsonl")
        if p.is_file() and p.name.startswith("rollout-")
    )


def collision_safe_destination(base: Path, relative: Path, profile: str) -> Path:
    dest = base / relative
    if not dest.exists() and not dest.is_symlink():
        return dest

    # Session UUIDs make collisions extremely unlikely, but never overwrite a file from
    # another profile. Keep the rollout- prefix/date so upstream recent-session detection
    # continues to work.
    suffix = relative.suffix
    stem = relative.name[: -len(suffix)] if suffix else relative.name
    renamed = relative.with_name(f"{stem}--{profile}{suffix}")
    return base / renamed


def add_tree(profile: Profile, source_name: str, destination_root: Path, manifest: list[dict]) -> int:
    source_root = profile.home / source_name
    count = 0
    if not source_root.is_dir():
        print(f"warning: {profile.name}: missing {source_root}", file=sys.stderr)
        return count

    for source in rollout_files(source_root):
        relative = source.relative_to(source_root)
        destination = collision_safe_destination(destination_root, relative, profile.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
        manifest.append(
            {
                "profile": profile.name,
                "kind": source_name,
                "source": str(source),
                "destination": str(destination),
            }
        )
        count += 1
    return count


def output_path(path: Path) -> Path:
    """Return an absolute output path without following a pre-existing symlink."""
    expanded = Path(os.path.expandvars(os.path.expanduser(str(path))))
    return Path(os.path.abspath(expanded))


def remove_generated_path(path: Path) -> None:
    """Remove only the path itself; never follow a symlink to its target."""
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def validate_output_root(root: Path) -> None:
    home = Path.home().absolute()
    root = root.absolute()
    if root == Path("/") or root == home:
        raise ValueError(f"refusing unsafe federation root: {root}")
    if len(root.parts) < 3:
        raise ValueError(f"refusing overly broad federation root: {root}")


def rebuild(profiles: list[Profile], root: Path) -> None:
    root = output_path(root)
    validate_output_root(root)

    # IMPORTANT: do not call root.resolve(). If an attacker or accident replaced the
    # federation path with a symlink, resolve() would turn a harmless unlink into a delete
    # operation against the symlink target. We deliberately operate on the pathname itself.
    staging = root.with_name(root.name + ".staging")
    remove_generated_path(staging)
    staging.mkdir(parents=True)

    sessions = staging / "sessions"
    archived = staging / "archived_sessions"
    sessions.mkdir()
    archived.mkdir()

    manifest: list[dict] = []
    totals: dict[str, int] = {}
    for profile in profiles:
        active_count = add_tree(profile, "sessions", sessions, manifest)
        archived_count = add_tree(profile, "archived_sessions", archived, manifest)
        totals[profile.name] = active_count + archived_count

    (staging / MANIFEST).write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [{"name": p.name, "home": str(p.home)} for p in profiles],
                "files": manifest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    remove_generated_path(root)
    staging.rename(root)

    print(f"Federation rebuilt: {root}")
    print(f"Set Agent Sessions Codex custom sessions root to:\n  {root / 'sessions'}")
    for name, count in totals.items():
        print(f"  {name}: {count} sessions")


def init_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"config already exists: {path}")
    example = [
        {"name": "work", "home": "~/.codex-work"},
        {"name": "personal", "home": "~/.codex-personal"},
        {"name": "geo", "home": "~/.codex-geo"},
    ]
    path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    print(f"Created {path}")
    print("Edit the paths to match your actual CODEX_HOME directories, then run sync.")


def status(root: Path) -> None:
    root = output_path(root)
    manifest_path = root / MANIFEST
    if not manifest_path.exists():
        print("No federation manifest found.")
        return
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = data.get("files", [])
    by_profile: dict[str, int] = {}
    broken = 0
    for item in files:
        by_profile[item["profile"]] = by_profile.get(item["profile"], 0) + 1
        destination = Path(item["destination"])
        if not destination.is_symlink() or not destination.exists():
            broken += 1
    print(f"Federation: {root}")
    for name, count in sorted(by_profile.items()):
        print(f"  {name}: {count} sessions")
    print(f"  broken links: {broken}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create an example profile config")
    sub.add_parser("sync", help="rebuild the federated session tree")
    sub.add_parser("status", help="show federation status")
    args = parser.parse_args()

    try:
        if args.command == "init":
            init_config(args.config.expanduser())
        elif args.command == "sync":
            profiles = load_profiles(args.config.expanduser())
            rebuild(profiles, args.root)
        elif args.command == "status":
            status(args.root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
