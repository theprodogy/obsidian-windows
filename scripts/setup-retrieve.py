#!/usr/bin/env python3
"""Safe, vault-scoped bootstrap for the optional retrieval cache.

Usage: scripts/setup-retrieve.py [--vault PATH] [--check] [--apply]
       [--no-llm|--allow-egress] [--rebuild] [--allow-remote-ollama]

Default is a read-only synthetic-prefix preview. --apply builds vault-scoped
chunks and BM25 state. Sending page text to a model requires the explicit
--allow-egress flag. Remote Ollama use requires --allow-remote-ollama.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian.paths import VaultSelectionError, resolve_vault_root


def helper(name: str, *arguments: str, quiet: bool = False) -> int:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / name), *arguments],
        stderr=subprocess.DEVNULL if quiet else None,
        check=False,
    ).returncode


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default="")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--allow-egress", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--allow-remote-ollama", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.no_llm and args.allow_egress:
        print("ERROR: --no-llm and --allow-egress are mutually exclusive", file=sys.stderr)
        return 2
    for name in ("contextual-prefix.py", "bm25-index.py", "rerank.py", "retrieve.py"):
        if not (SCRIPTS / name).is_file():
            print(f"ERROR: missing product helper: scripts/{name}", file=sys.stderr)
            return 3
    try:
        vault = str(
            resolve_vault_root(args.vault or None, start=Path.cwd(), plugin_root=PLUGIN_ROOT).root
        )
    except VaultSelectionError as exc:
        print(f"ERR {exc.code}: {exc}", file=sys.stderr)
        return 2

    print(f"Vault: {vault}")
    print(f"Product helpers: {SCRIPTS}")

    if args.check:
        if helper("bm25-index.py", "--vault", vault, "stats", quiet=True) != 0:
            print("READY: retrieval helpers are installed; vault index is not built yet.")
        return 0

    prefix = ["--vault", vault, "--all"]
    if args.no_llm:
        prefix.append("--no-llm")
    if args.allow_egress:
        prefix.append("--allow-egress")
    if args.rebuild:
        prefix.append("--rebuild")

    if not args.apply:
        code = helper("contextual-prefix.py", *prefix, "--peek")
        if code:
            return code
        print("Dry run only. Repeat with --apply to write derived retrieval state.")
        return 0

    for name, arguments in (
        ("contextual-prefix.py", prefix),
        ("bm25-index.py", ["--vault", vault, "build"]),
    ):
        code = helper(name, *arguments)
        if code:
            return code
    retrieve = ["--vault", vault, "wiki", "--top", "1", "--no-rerank"]
    if args.allow_remote_ollama:
        retrieve.append("--allow-remote-ollama")
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "retrieve.py"), *retrieve],
        stdout=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        return result.returncode
    print("Retrieval state built successfully.")
    return 0


if __name__ == "__main__":
    from claude_obsidian.hook_adapter import force_utf8_stdio

    force_utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
