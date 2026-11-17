#!/usr/bin/env python3
"""Detect the supported Obsidian CLI and write a normalized transport snapshot.

Official Obsidian CLI syntax is `obsidian version`. The detector never treats
binary presence alone, a legacy --version response, or exit status zero alone
as proof of capability.

Usage:
  python scripts/detect-transport.py [--vault PATH] [--force] [--quiet]
  python scripts/detect-transport.py --peek [--vault PATH]
  python scripts/detect-transport.py [PATH]          # legacy positional vault

--peek is strictly read-only with respect to the vault. It neither creates
.vault-meta nor refreshes transport.json.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian.json_utils import strict_json_loads
from claude_obsidian.paths import VaultSelectionError, resolve_vault_root
from claude_obsidian.transaction import (
    MutationLock,
    TransactionError,
    _atomic_vault_write,
    read_vault_regular,
)
from claude_obsidian.transport_probe import _stop_probe, probe_selected_vault

STALE_AFTER_DAYS = 7
MAX_OUTPUT = 8192


def fail(message: str, code: int = 2) -> None:
    print(f"ERR: {message}", file=sys.stderr)
    raise SystemExit(code)


def fail_usage(message: str) -> None:
    print(f"ERR: {message}", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    raise SystemExit(3)


def parse(argv: list[str]) -> tuple[str, bool, bool, str]:
    mode, force, quiet, vault_input, positional = "write", False, False, "", ""
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--peek":
            mode = "peek"
        elif arg == "--force":
            force = True
        elif arg == "--quiet":
            quiet = True
        elif arg == "--vault":
            if index + 1 >= len(argv):
                fail_usage("--vault requires a path")
            if vault_input:
                fail_usage("--vault may be specified only once")
            vault_input = argv[index + 1]
            index += 1
        elif arg.startswith("--vault="):
            if vault_input:
                fail_usage("--vault may be specified only once")
            vault_input = arg[len("--vault="):]
            if not vault_input:
                fail_usage("--vault requires a path")
        elif arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        elif arg == "--":
            rest = argv[index + 1:]
            if len(rest) > 1:
                fail_usage("only one positional vault path is accepted")
            if rest:
                positional = rest[0]
            break
        elif arg.startswith("-"):
            fail_usage(f"unknown option: {arg}")
        else:
            if positional:
                fail_usage("only one positional vault path is accepted")
            positional = arg
        index += 1
    if vault_input and positional:
        fail_usage("use either --vault or a positional vault path, not both")
    return mode, force, quiet, vault_input or positional or os.getcwd()


def run_version_probe(binary: str, vault: str) -> tuple[int, str]:
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                [binary, "version"],
                cwd=vault,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=dict(os.environ),
                start_new_session=True,
            )
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _stop_probe(process)
                code = 124
                output.write(b"\nERR: obsidian version probe timed out after 5 seconds\n")
        except OSError as exc:
            code = 126
            output.write(f"ERR: cannot execute obsidian CLI: {exc.__class__.__name__}\n".encode())
        output.seek(0)
        payload = output.read(MAX_OUTPUT + 1)
    if len(payload) > MAX_OUTPUT:
        payload = payload[:MAX_OUTPUT] + b"\n[output truncated]\n"
    return code, payload.decode("utf-8", errors="replace").rstrip("\n")


def classify(kind: str, binary: str, exit_code: int, raw_output: str, vault: str):
    if kind == "missing":
        return (
            "missing", False, None, "",
            "Obsidian CLI is not on PATH. Enable Command line interface in Obsidian Settings > General.",
        )
    if kind == "legacy":
        return "unsupported", False, None, "", raw_output
    lowered = raw_output.casefold()
    app_patterns = (
        "app is not running", "app not running", "obsidian is not running",
        "no running obsidian", "unable to connect", "failed to connect",
        "connection refused", "could not connect", "ipc connection",
    )
    unsupported_patterns = (
        "unknown command", "unrecognized command", "unsupported command",
        "command not found: version", "requires obsidian 1.12",
        "requires the obsidian 1.12", "command line interface is disabled",
        "cli is disabled", "enable command line interface",
    )
    if any(pattern in lowered for pattern in app_patterns):
        return (
            "app_not_running", False, False, "",
            raw_output or "Obsidian is not running or the CLI could not connect.",
        )
    if any(pattern in lowered for pattern in unsupported_patterns):
        return (
            "unsupported", False, None, "",
            raw_output or "Installed binary does not support 'obsidian version'.",
        )
    if exit_code != 0:
        return (
            "capability_error", False, None, "",
            raw_output or f"'obsidian version' exited with status {exit_code}.",
        )
    if re.search(r"(?im)^\s*(?:error|err|fatal|exception|failed)(?:\s*[:\-]|\s+)", raw_output):
        return "capability_error", False, None, "", raw_output
    if not raw_output:
        return "capability_error", False, None, "", "'obsidian version' returned no version output."
    if not re.search(r"\b\d+\.\d+(?:\.\d+)?\b", raw_output):
        return (
            "capability_error", False, None, "",
            f"'obsidian version' returned an unrecognized response: {raw_output}",
        )
    version_string = next((line.strip() for line in raw_output.splitlines() if line.strip()), "")
    verified, diagnostic = probe_selected_vault(binary, vault)
    if not verified:
        return "vault_unverified", False, True, version_string, diagnostic
    return "ready", True, True, version_string, ""


def snapshot_is_valid(path: Path, vault: str) -> bool:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("schema_version") == 1
        and data.get("vault_root") == vault
        and isinstance(data.get("preferred"), str)
        and isinstance(data.get("fallback_chain"), list)
        and all(isinstance(item, str) and item for item in data["fallback_chain"])
        and isinstance(data.get("available"), dict)
        and isinstance(data["available"].get("cli"), dict)
        and isinstance(data["available"].get("filesystem"), dict)
        and data.get("preferred") != "cli"
    )


def is_fresh(path: Path) -> bool:
    try:
        return time.time() - os.path.getmtime(path) < STALE_AFTER_DAYS * 86400
    except OSError:
        return False


def vault_identity(vault: str) -> tuple[int, int]:
    try:
        value = os.stat(vault, follow_symlinks=False)
    except OSError as exc:
        fail(f"cannot identify vault root: {exc}")
    if not stat.S_ISDIR(value.st_mode):
        fail("vault root is not a directory")
    return value.st_dev, value.st_ino


def build_report(vault: str, identity: tuple[int, int], cli: tuple[str, str, int, str]) -> dict:
    kind, binary, exit_code, raw_output = cli
    status, usable, app_running, version_string, diagnostic = classify(
        kind, binary, exit_code, raw_output.strip(), vault
    )
    preferred = "cli" if usable else "filesystem"
    chain = ["cli", "filesystem"] if usable else ["filesystem"]
    manual_override = False

    def require_selected_vault() -> None:
        try:
            value = os.stat(vault, follow_symlinks=False)
        except OSError as exc:
            fail(f"selected vault changed during transport detection: {exc}")
        if not stat.S_ISDIR(value.st_mode) or (value.st_dev, value.st_ino) != identity:
            fail("selected vault changed during transport detection")

    require_selected_vault()
    try:
        existing_raw = read_vault_regular(
            Path(vault), ".vault-meta/transport.json", limit=8 * 1024 * 1024
        )
        existing = (
            strict_json_loads(existing_raw.decode("utf-8")) if existing_raw is not None else {}
        )
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TransactionError):
        existing = {}
    require_selected_vault()
    if existing.get("manual_override") is True:
        candidate_preferred = existing.get("preferred")
        candidate_chain = existing.get("fallback_chain")
        if (
            isinstance(candidate_preferred, str)
            and candidate_preferred
            and isinstance(candidate_chain, list)
            and candidate_chain
            and all(isinstance(item, str) and item for item in candidate_chain)
        ):
            manual_override = True
            preferred = candidate_preferred if candidate_preferred != "cli" or usable else "filesystem"
            chain = candidate_chain if usable else [item for item in candidate_chain if item != "cli"]
            if not chain:
                chain = ["filesystem"]
    deferred = "MCP server detection is deferred; configure and pin it with manual_override if needed."
    return {
        "schema_version": 1,
        "detected_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "host": socket.gethostname(),
        "vault_root": vault,
        "manual_override": manual_override,
        "preferred": preferred,
        "fallback_chain": chain,
        "available": {
            "cli": {
                "present": kind != "missing",
                "usable": usable,
                "status": status,
                "binary": binary,
                "probe": "obsidian version",
                "exit_code": None if exit_code < 0 else exit_code,
                "version_string": version_string,
                "diagnostic": diagnostic,
                "obsidian_app_running": app_running,
                "selected_vault_verified": usable,
                "identity_probe": "obsidian vault info=path",
                "capability_probe": "obsidian help read",
            },
            "filesystem": {
                "present": True,
                "vault_root": vault,
                "note": "universal fallback; uses the agent host's filesystem tools directly",
            },
            "mcp_obsidian": {"present": None, "detection": "deferred", "note": deferred},
            "mcpvault": {"present": None, "detection": "deferred", "note": deferred},
        },
    }


def main(argv: list[str]) -> int:
    mode, force, quiet, raw_vault = parse(argv)
    try:
        vault = str(
            resolve_vault_root(
                raw_vault, start=Path.cwd(), plugin_root=PLUGIN_ROOT, allow_uninitialized=True
            ).root
        )
    except VaultSelectionError as exc:
        print(f"ERR {exc.code}: {exc}", file=sys.stderr)
        fail("unable to select a safe user vault")
    if not os.path.isdir(vault):
        fail(f"vault root is not a directory: {vault}")
    identity = vault_identity(vault)

    meta_dir = Path(vault, ".vault-meta")
    output_file = meta_dir / "transport.json"
    if meta_dir.is_symlink() or (meta_dir.exists() and not meta_dir.is_dir()):
        fail(f".vault-meta is not a safe directory: {meta_dir}")
    if output_file.is_symlink() or (output_file.exists() and not output_file.is_file()):
        fail(f"transport snapshot is not a safe regular file: {output_file}")

    def log(message: str) -> None:
        if not quiet:
            print(message, file=sys.stderr)

    if mode == "write" and not force and output_file.is_file() and not output_file.is_symlink():
        if is_fresh(output_file) and snapshot_is_valid(output_file, vault):
            log(f"transport.json is fresh (<{STALE_AFTER_DAYS}d). Use --force to refresh.")
            sys.stdout.write(output_file.read_text(encoding="utf-8"))
            return 0
        if is_fresh(output_file):
            log("transport.json is fresh but invalid; refreshing it.")

    kind, binary, exit_code, raw_output = "missing", "", -1, ""
    candidate = shutil.which("obsidian")
    if candidate:
        binary, kind = candidate, "official"
        exit_code, raw_output = run_version_probe(binary, vault)
    else:
        legacy = shutil.which("obsidian-cli")
        if legacy:
            binary, kind = legacy, "legacy"
            raw_output = "Legacy obsidian-cli found; enable and register the official obsidian CLI."

    text = json.dumps(
        build_report(vault, identity, (kind, binary, exit_code, raw_output)),
        indent=2, sort_keys=True, ensure_ascii=False,
    ) + "\n"
    if mode == "write":
        root_fd = meta_fd = -1
        try:
            with MutationLock(
                Path(vault),
                expected_vault_identity={"state": "existing", "device": identity[0], "inode": identity[1]},
            ) as lock:
                root_fd = lock.duplicate_root_fd()
                meta_fd = lock.duplicate_parent_fd()
                _atomic_vault_write(
                    Path(vault), ".vault-meta/transport.json", text.encode("utf-8"),
                    mode=0o644, root_fd=root_fd, meta_fd=meta_fd,
                )
        except Exception:
            fail("cannot publish a confined transport snapshot")
        finally:
            for descriptor in (meta_fd, root_fd):
                if descriptor >= 0:
                    os.close(descriptor)
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()
    if mode == "write":
        log(f"Wrote: {output_file}")
        log("Probe: obsidian version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
