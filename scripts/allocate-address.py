#!/usr/bin/env python3
"""Read-only compatibility diagnostic for the legacy address allocator.

Address reservation now belongs exclusively to a reviewed
claude-obsidian.transaction.v1 operation with address_requests. This helper
only prints the next observed counter value and never creates locks, counters,
directories, or files.

Usage:
  scripts/allocate-address.py [--vault PATH] [--peek]

Legacy `allocate` and `--rebuild` modes fail closed with migration guidance.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian.paths import VaultSelectionError, resolve_vault_root
from claude_obsidian.transaction import TransactionError, read_vault_regular


def main(argv: list[str]) -> int:
    explicit = ""
    disabled = False
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--vault":
            if index + 1 >= len(argv):
                print("ERR: --vault requires a path", file=sys.stderr)
                return 2
            explicit = argv[index + 1]
            index += 1
        elif arg.startswith("--vault="):
            explicit = arg[len("--vault="):]
        elif arg == "--peek":
            disabled = False
        elif arg in ("allocate", "--rebuild"):
            disabled = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"ERR: unknown argument: {arg}", file=sys.stderr)
            return 2
        index += 1

    if disabled:
        print(
            "ERR LEGACY_ALLOCATOR_READ_ONLY: reserve addresses through one reviewed "
            "transaction address_requests bundle; direct allocation/rebuild is disabled",
            file=sys.stderr,
        )
        return 2

    try:
        root = resolve_vault_root(explicit or None, start=Path.cwd(), plugin_root=PLUGIN_ROOT).root
        raw = read_vault_regular(root, ".vault-meta/address-counter.txt", limit=64 * 1024)
    except (VaultSelectionError, TransactionError) as exc:
        print(f"ERR {exc.code}: {exc}", file=sys.stderr)
        return 2

    if raw is not None:
        try:
            value = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            value = ""
        if not value.isdigit() or not 1 <= int(value) <= 999999:
            print("ERR INVALID_ADDRESS_COUNTER: counter must contain 1..999999", file=sys.stderr)
            return 3
        print(int(value))
        return 0

    highest = 0
    wiki = root / "wiki"
    if wiki.is_dir() and not wiki.is_symlink():
        for page in sorted(wiki.rglob("*.md")):
            if page.is_symlink() or not page.is_file():
                continue
            try:
                page.resolve(strict=True).relative_to(wiki.resolve(strict=True))
                text = page.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            lines = text.splitlines()
            if not lines or lines[0].strip() != "---":
                continue
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                match = re.fullmatch(r"address:\s*c-([0-9]{6})\s*", line)
                if match:
                    highest = max(highest, int(match.group(1)))
    print(highest + 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
