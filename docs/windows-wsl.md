# Windows and WSL guide

claude-obsidian runs natively on Windows with the same capabilities as Linux
and macOS. WSL is optional. This guide covers what works where, how native
Windows confines vault writes, and how to unstick WSL if you choose it.

## Platform support

| Capability | Linux / macOS / WSL | Native Windows (Git Bash for hooks and `.sh` wrappers) |
|---|---|---|
| Inspection, dry-run previews, retrieval | Yes | Yes |
| Vault writes (`transaction apply`, `init`, `adopt`, `migrate`, `capture apply`, `mode set`) | Yes | Yes |
| Capture queue commands | Yes | Yes |
| Git checkpoints (`checkpoint`) | Yes | Yes |
| Setup and helper scripts | `bash scripts/<name>.sh` | `python scripts/<name>.py` (`detect-transport`, `allocate-address`, `setup-retrieve`, `setup-multi-agent`); the `.sh` files are thin wrappers that need Git Bash |
| Claude Code hooks (`SessionStart`, `Stop`) | Yes (works out of the box) | Yes, with Git Bash and Python on `PATH`; see [below](#claude-code-hooks-and-python3-on-windows) |

Native Windows needs Python 3.11 or newer. On Python older than 3.13 the
file-mode call used for private files is a no-op on Windows; NTFS ACLs apply
instead.

## How Windows confines writes

Linux and macOS pin directories with POSIX directory descriptors. Native
Windows gets the same guarantees from `claude_obsidian/fsat.py`, which opens
every path component relative to an already-open directory handle through the
NT API, so a concurrently swapped folder cannot redirect a write. Pinned
directories are held open without `FILE_SHARE_DELETE`, which makes the OS
refuse to rename or delete them while an operation runs. Symlinks, junctions,
and other name-surrogate reparse points are never followed. The vault mutation
lock is an advisory lock built on a named semaphore, shared by every process
touching the same vault.

Vaults must live on a filesystem with stable file identity: NTFS is fine, but
FAT/exFAT volumes (typical USB sticks) and some network shares are refused with
`UNSAFE_VAULT_IDENTITY`: move the vault to NTFS (or work inside WSL).

## Claude Code hooks and python3 on Windows

`hooks/hooks.json` uses the shell form of a Claude Code command hook: a short
`sh` snippet picks `python3` if it runs, else `python`, and then launches
`scripts/claude-obsidian.py` from `${CLAUDE_PLUGIN_ROOT}`. On Windows, Claude
Code runs hook commands through Git Bash when it is installed. If it falls
back to PowerShell, the `sh` syntax fails to parse and the hook never runs, so
install [Git for Windows](https://git-scm.com/download/win).

Python resolution:

- python.org installer: provides `python.exe` (and the `py` launcher), not
  `python3.exe`. The snippet falls back to `python`, so no shim is needed.
- Microsoft Store Python: a `python3` app execution alias can be a stub that
  opens the Store. The snippet only accepts `python3` if `python3 -c ''`
  succeeds, otherwise it uses `python`; disable the stub alias in Windows
  Settings if neither runs.

When no usable Python or no Git Bash is found, the hook process fails before
claude-obsidian's own code runs, so no diagnostic can be emitted. SessionStart
context and Stop recovery warnings are silently absent; skills and the CLI are
unaffected.

## WSL troubleshooting

WSL is optional on current releases. WSL being "installed" does not always mean WSL is working. Symptoms and checks,
roughly in the order worth trying:

| Symptom | Check |
|---|---|
| `wsl --install` completed but `wsl --status` or `wsl -l -v` hangs indefinitely | This field-reported hang has no confirmed cause. Follow Microsoft's WSL hang diagnosis and reporting flow below. |
| `wsl` reports a kernel or version error | Run `wsl --update`, then `wsl --shutdown`, then retry. |
| WSL worked before and stopped after an update or software change | Do not assume a cause. Update Windows and WSL, then follow Microsoft's WSL troubleshooting flow. |
| Approval hash from a native dry-run fails inside WSL with `PLAN_CHANGED` | By design: the approval hash binds the reviewing environment's filesystem identity. Run the dry-run review inside WSL when the apply will happen there; a natively produced `approved_plan_sha256` cannot be replayed from WSL. |
| Writes fail with `UNSAFE_VAULT_IDENTITY` mentioning stable file identity | The vault sits on FAT/exFAT or an unsupported network share. Move it to NTFS, or keep it inside the WSL filesystem. |

WSL troubleshooting checklist:

1. Start with Microsoft's official
   [WSL troubleshooting guide](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting).
2. Confirm virtualization is enabled in BIOS/UEFI and that both "Virtual
   Machine Platform" and "Windows Subsystem for Linux" are enabled. Reboot
   after enabling either feature.
3. Run `wsl --update` from an elevated prompt, then `wsl --shutdown`, and retry
   `wsl --status`.
4. Confirm the hypervisor launch setting is enabled. If a third-party
   hypervisor is installed, use a current version that supports Hyper-V or
   temporarily turn it off while diagnosing the conflict.
5. If WSL still hangs, follow Microsoft's
   [WSL hang data-collection steps](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting-guide#wsl-hangs)
   and file the resulting report with the WSL project. Do not attribute the
   hang to a specific cause without supporting diagnostics.

## Working across the boundary

If you use both native Windows and WSL on the same vault, do the reviewed
dry-run in the environment that will run the apply: approval hashes bind to
the environment that produced them. Keeping the vault inside the WSL
filesystem (rather than on a mounted Windows drive) avoids the identity
caveats above and cross-boundary performance overhead.

Known limit: when `setup-multi-agent` cannot create symlinks on Windows and
falls back to copies, a later `--check` reports `CONFLICT` for those copies.
Enable Developer Mode (or run elevated) to allow symlinks.
