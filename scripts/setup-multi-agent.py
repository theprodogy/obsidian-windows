#!/usr/bin/env python3
"""Install portable skill links without overwriting existing host configuration.

Usage: scripts/setup-multi-agent.py [--check|--dry-run|--apply]
       [--host codex|opencode|gemini|zcode|cursor|windsurf|all] [--workspace PATH]

Default: dry-run for Codex, OpenCode, and Gemini user-level per-skill links.
ZCode, Cursor and Windsurf are opt-in and require an explicit --host selection;
Cursor and Windsurf also require an explicit --workspace destination.
Existing files and links pointing elsewhere are never replaced.
On native Windows each link is a directory junction, or a copy when a junction
cannot be created.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claude_obsidian.paths import is_name_surrogate

SKILLS_DIR = REPO_ROOT / "skills"
HOSTS = ("codex", "opencode", "gemini", "zcode", "cursor", "windsurf")
EXPECTED_GLOBAL = ("codex", "opencode", "gemini")


def is_link(path: Path) -> bool:
    try:
        return is_name_surrogate(os.lstat(path))
    except OSError:
        return False


def points_at(destination: Path, source: Path) -> bool:
    try:
        if os.name == "nt":
            return os.path.samefile(destination, source)
        return os.readlink(destination) == str(source)
    except OSError:
        return False


def create_link(source: Path, destination: Path) -> str:
    if os.name != "nt":
        os.symlink(source, destination, target_is_directory=True)
        return "symlink"
    import _winapi

    try:
        _winapi.CreateJunction(str(source), str(destination))
        return "junction"
    except OSError:
        shutil.copytree(source, destination, symlinks=True)
        return "copy"


class Installer:
    def __init__(self, mode: str, workspace: Path | None) -> None:
        self.mode = mode
        self.workspace = workspace
        self.status = 0

    def conflict(self, host: str, message: str) -> None:
        print(f"CONFLICT {host} {message}", file=sys.stderr)
        self.status = 2

    def confined_parents(self, host: str, parent: Path, root: Path) -> bool:
        try:
            relative = parent.relative_to(root)
        except ValueError:
            self.conflict(host, f"parent escapes workspace: {parent}")
            return False
        cursor = root
        for component in relative.parts:
            cursor = cursor / component
            if is_link(cursor):
                self.conflict(host, f"parent is a symlink: {cursor}")
                return False
            if cursor.exists():
                if not cursor.is_dir():
                    self.conflict(host, f"parent is not a directory: {cursor}")
                    return False
            elif self.mode == "apply":
                try:
                    cursor.mkdir()
                except OSError:
                    self.conflict(host, f"cannot create confined parent: {cursor}")
                    return False
            else:
                continue
            if cursor.exists() and (is_link(cursor) or not cursor.is_dir()):
                self.conflict(host, f"parent changed during install: {cursor}")
                return False
        return True

    def inspect_link(self, host: str, source: Path, destination: Path, root: Path | None) -> None:
        if is_link(destination):
            if points_at(destination, source):
                print(f"READY {host} {destination}")
            else:
                self.conflict(host, f"{destination} points elsewhere")
            return
        if destination.exists():
            self.conflict(host, f"{destination} already exists")
            return
        if root is not None and not self.confined_parents(host, destination.parent, root):
            return
        if self.mode == "apply":
            if root is None:
                destination.parent.mkdir(parents=True, exist_ok=True)
            kind = create_link(source, destination)
            suffix = "" if kind == "symlink" else f" ({kind})"
            print(f"CREATED {host} {destination}{suffix}")
        else:
            print(f"PLANNED {host} {destination}")
            if self.mode == "check" and self.status == 0:
                self.status = 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", dest="mode", action="store_const", const="check")
    group.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run")
    group.add_argument("--apply", dest="mode", action="store_const", const="apply")
    parser.add_argument("--host", action="append", choices=(*HOSTS, "all"))
    parser.add_argument("--workspace")
    parser.set_defaults(mode="dry-run")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if not SKILLS_DIR.is_dir():
        print(f"ERROR: missing skills directory: {SKILLS_DIR}", file=sys.stderr)
        return 2
    expanded: list[str] = []
    for host in args.host or EXPECTED_GLOBAL:
        expanded.extend((*EXPECTED_GLOBAL, "cursor", "windsurf") if host == "all" else (host,))

    workspace = None
    if args.workspace:
        if not os.path.isdir(args.workspace):
            print("ERROR: workspace is not an existing directory", file=sys.stderr)
            return 2
        workspace = Path(args.workspace).resolve()

    home = Path(os.environ.get("HOME") or Path.home())
    installer = Installer(args.mode, workspace)
    seen: set[str] = set()
    for host in expanded:
        if host in seen:
            continue
        seen.add(host)
        root = None
        if host == "codex":
            destination_root = home / ".agents/skills"
        elif host == "opencode":
            destination_root = home / ".config/opencode/skills"
        elif host == "gemini":
            destination_root = home / ".gemini/skills"
        elif host == "zcode":
            destination_root = home / ".zcode/skills"
        else:
            if workspace is None:
                print(f"ERROR: --host {host} requires --workspace PATH", file=sys.stderr)
                installer.status = 2
                continue
            destination_root = workspace / f".{host}/skills"
            root = workspace
        for source in sorted(SKILLS_DIR.iterdir()):
            if source.is_dir() and (source / "SKILL.md").is_file():
                installer.inspect_link(host, source, destination_root / source.name, root)

    if args.mode == "dry-run":
        print("Dry run only. Repeat with --apply to create the planned links.")
    return installer.status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
