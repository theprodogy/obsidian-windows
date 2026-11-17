"""fd-relative filesystem primitives with one API on POSIX and native Windows.

POSIX is a passthrough to ``os.*`` with ``dir_fd``.  Windows pins directories
with NT handles (``NtCreateFile`` + ``RootDirectory``) wrapped as CRT fds, so
``os.read/write/fstat/fsync/close`` work on every returned descriptor.
"""

from __future__ import annotations

import errno
import os
import sys

__all__ = [
    "O_DIRECTORY",
    "O_NOFOLLOW",
    "fchmod",
    "fd_path",
    "fsync_dir",
    "link_at",
    "listdir_fd",
    "lock_ex_nb",
    "mkdir_at",
    "open_at",
    "open_dir",
    "process_alive",
    "rename_at",
    "rmdir_at",
    "stat_at",
    "supported",
    "unlink_at",
    "unlock",
]

if os.name != "nt":
    O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
    O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)

    def supported() -> bool:
        return (
            bool(O_DIRECTORY)
            and os.open in os.supports_dir_fd
            and os.mkdir in os.supports_dir_fd
            and os.rename in os.supports_dir_fd
            and os.stat in os.supports_dir_fd
            and os.unlink in os.supports_dir_fd
            and os.rmdir in os.supports_dir_fd
        )

    def open_dir(path: str | os.PathLike[str]) -> int:
        return os.open(
            path,
            os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )

    def open_at(dir_fd: int, name: str, flags: int, mode: int = 0o600) -> int:
        return os.open(name, flags, mode, dir_fd=dir_fd)

    def mkdir_at(dir_fd: int, name: str, mode: int = 0o700) -> None:
        os.mkdir(name, mode, dir_fd=dir_fd)

    def stat_at(
        dir_fd: int, name: str, follow_symlinks: bool = False
    ) -> os.stat_result:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def rename_at(src_dir_fd: int, src: str, dst_dir_fd: int, dst: str) -> None:
        os.rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def unlink_at(dir_fd: int, name: str) -> None:
        os.unlink(name, dir_fd=dir_fd)

    def rmdir_at(dir_fd: int, name: str) -> None:
        os.rmdir(name, dir_fd=dir_fd)

    def link_at(src_dir_fd: int, src: str, dst_dir_fd: int, dst: str) -> None:
        os.link(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=False)

    def fchmod(fd: int, mode: int) -> None:
        os.fchmod(fd, mode)

    def lock_ex_nb(fd: int) -> bool:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                return False
            raise
        return True

    def unlock(fd: int) -> None:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def listdir_fd(dir_fd: int) -> list[str]:
        return os.listdir(dir_fd)

    def fsync_dir(dir_fd: int) -> None:
        os.fsync(dir_fd)

    def fd_path(fd: int) -> str:
        if sys.platform == "darwin":
            import fcntl

            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)  # type: ignore[attr-defined]
            return os.fsdecode(raw.split(b"\0", 1)[0])
        return os.readlink(f"/proc/self/fd/{fd}")

    def process_alive(pid: int) -> bool | None:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True

else:
    import ctypes
    import msvcrt
    import stat as _stat
    from ctypes import wintypes

    # Distinct high bits: the Windows CRT has no such flags and uses up to 0x40000.
    O_DIRECTORY = getattr(os, "O_DIRECTORY", 0x100000)
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0x200000)

    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_READ_DATA = 0x1
    _FILE_WRITE_DATA = 0x2
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_WRITE_ATTRIBUTES = 0x100
    _FILE_TRAVERSE = 0x20
    _GENERIC_READ = 0x00120089
    _GENERIC_WRITE = 0x00120116 | 0x80
    _SHARE_RW = 0x3
    _SHARE_ALL = 0x7
    _OPEN, _CREATE, _OPEN_IF, _OVERWRITE, _OVERWRITE_IF = 1, 2, 3, 4, 5
    _DIRECTORY_FILE = 0x1
    _NON_DIRECTORY_FILE = 0x40
    _SYNC_IO = 0x20
    _OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x40
    _ATTR_NORMAL = 0x80
    _ATTR_READONLY = 0x1
    _ATTR_DIRECTORY = 0x10
    _ATTR_REPARSE = 0x400
    _SURROGATE_TAGS = frozenset({0xA000000C, 0xA0000003})
    _STILL_ACTIVE = 259

    _STATUS_ERRNO = {
        0xC0000034: errno.ENOENT,
        0xC000003A: errno.ENOENT,
        0xC000000F: errno.ENOENT,
        0xC0000035: errno.EEXIST,
        0xC0000103: errno.ENOTDIR,
        0xC00000BA: errno.EISDIR,
        0xC0000022: errno.EACCES,
        0xC0000043: errno.EACCES,
        0xC0000056: errno.EACCES,
        0xC0000121: errno.EACCES,
        0xC0000101: errno.ENOTEMPTY,
        0xC0000033: errno.EINVAL,
        0xC000003B: errno.EINVAL,
        0xC000000D: errno.EINVAL,
        0xC00000D4: errno.EXDEV,
        0xC000007F: errno.ENOSPC,
        0xC0000003: errno.EINVAL,
        0xC00000BB: errno.ENOTSUP,
    }
    _WINERROR_ERRNO = {
        2: errno.ENOENT,
        3: errno.ENOENT,
        5: errno.EACCES,
        32: errno.EACCES,
        33: errno.EACCES,
        80: errno.EEXIST,
        87: errno.EINVAL,
        112: errno.ENOSPC,
        145: errno.ENOTEMPTY,
        183: errno.EEXIST,
        267: errno.ENOTDIR,
        1920: errno.ELOOP,
    }
    _FALLBACK_STATUSES = frozenset({0xC0000003, 0xC00000BB, 0xC000000D})

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_void_p),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.c_void_p),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQos", ctypes.c_void_p),
        ]

    class _IoStatus(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_size_t), ("Information", ctypes.c_size_t)]

    class _TagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    class _RenameHeader(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("RootDirectory", ctypes.c_void_p),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", ctypes.c_wchar * 1),
        ]

    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _NtCreateFile = _ntdll.NtCreateFile
    _NtCreateFile.restype = ctypes.c_long
    _NtCreateFile.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _NtSetInformationFile = _ntdll.NtSetInformationFile
    _NtSetInformationFile.restype = ctypes.c_long
    _NtSetInformationFile.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _NtQueryDirectoryFile = _ntdll.NtQueryDirectoryFile
    _NtQueryDirectoryFile.restype = ctypes.c_long
    _NtQueryDirectoryFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_ubyte,
        ctypes.c_void_p,
        ctypes.c_ubyte,
    ]
    _RtlNtStatusToDosError = _ntdll.RtlNtStatusToDosError
    _RtlNtStatusToDosError.restype = wintypes.ULONG
    _RtlNtStatusToDosError.argtypes = [ctypes.c_long]

    _CloseHandle = _k32.CloseHandle
    _CloseHandle.argtypes = [ctypes.c_void_p]
    _GetInfoEx = _k32.GetFileInformationByHandleEx
    _GetInfoEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _FlushFileBuffers = _k32.FlushFileBuffers
    _FlushFileBuffers.argtypes = [ctypes.c_void_p]
    _GetFinalPath = _k32.GetFinalPathNameByHandleW
    _GetFinalPath.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD]
    _GetFinalPath.restype = wintypes.DWORD
    _OpenProcess = _k32.OpenProcess
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _OpenProcess.restype = ctypes.c_void_p
    _GetExitCodeProcess = _k32.GetExitCodeProcess
    _GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]

    def supported() -> bool:
        return True

    def _status_error(status: int, name: str | None = None) -> OSError:
        code = status & 0xFFFFFFFF
        err = _STATUS_ERRNO.get(code)
        if err is None:
            err = _WINERROR_ERRNO.get(_RtlNtStatusToDosError(status), errno.EIO)
        return OSError(err, f"NTSTATUS 0x{code:08X}", name)

    def _check_name(name: str) -> None:
        if (
            not isinstance(name, str)
            or name in ("", ".", "..")
            or any(c in name for c in "/\\:\0")
        ):
            raise OSError(errno.EINVAL, "not a single path component", name)

    def _handle(fd: int) -> int:
        return msvcrt.get_osfhandle(fd)

    def _nt_open(
        root: int | None,
        name: str,
        access: int,
        share: int,
        disposition: int,
        options: int,
        attrs: int = _ATTR_NORMAL,
    ) -> int:
        size = len(name) * 2
        if size > 0xFFFE:
            raise OSError(errno.ENAMETOOLONG, "path too long", name)
        buf = ctypes.create_unicode_buffer(name)
        us = _UnicodeString(size, size + 2, ctypes.addressof(buf))
        oa = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            root,
            ctypes.addressof(us),
            _OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        out = ctypes.c_void_p()
        iosb = _IoStatus()
        status = _NtCreateFile(
            ctypes.byref(out),
            access,
            ctypes.byref(oa),
            ctypes.byref(iosb),
            None,
            attrs,
            share,
            disposition,
            options | _SYNC_IO,
            None,
            0,
        )
        if status < 0:
            raise _status_error(status, name)
        return int(out.value or 0)

    def _tag_info(handle: int) -> tuple[int, int]:
        info = _TagInfo()
        if not _GetInfoEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return 0, 0
        return info.FileAttributes, info.ReparseTag

    def _is_surrogate(handle: int) -> bool:
        attrs, tag = _tag_info(handle)
        return bool(attrs & _ATTR_REPARSE) and tag in _SURROGATE_TAGS

    def _wrap(handle: int, flags: int = 0) -> int:
        try:
            return msvcrt.open_osfhandle(handle, os.O_BINARY | flags)
        except OSError:
            _CloseHandle(handle)
            raise

    def _open_checked(
        root: int | None,
        name: str,
        access: int,
        share: int,
        disposition: int,
        options: int,
        nofollow: bool,
        attrs: int = _ATTR_NORMAL,
    ) -> int:
        # shortcut: cloud placeholders opened no-follow are read as the placeholder, upgrade if OneDrive hydration matters
        if nofollow:
            options |= _OPEN_REPARSE_POINT
        handle = _nt_open(root, name, access, share, disposition, options, attrs)
        if nofollow and _is_surrogate(handle):
            _CloseHandle(handle)
            raise OSError(errno.ELOOP, "path is a symlink or junction", name)
        return handle

    def _dir_open(root: int | None, name: str, nofollow: bool) -> int:
        access = _FILE_READ_DATA | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        # no FILE_SHARE_DELETE: a pinned directory cannot be renamed or swapped while open
        return _wrap(
            _open_checked(root, name, access, _SHARE_RW, _OPEN, _DIRECTORY_FILE, nofollow)
        )

    def open_dir(path: str | os.PathLike[str]) -> int:
        full = os.path.abspath(os.fspath(path))
        if full.startswith("\\\\?\\"):
            nt = "\\??\\" + full[4:]
        elif full.startswith("\\\\"):
            nt = "\\??\\UNC\\" + full[2:]
        else:
            nt = "\\??\\" + full
        return _dir_open(None, nt, True)

    def open_at(dir_fd: int, name: str, flags: int, mode: int = 0o600) -> int:
        _check_name(name)
        root = _handle(dir_fd)
        nofollow = bool(flags & O_NOFOLLOW)
        if flags & O_DIRECTORY:
            return _dir_open(root, name, nofollow)
        acc = flags & 3
        access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        if acc == os.O_RDONLY:
            access = _GENERIC_READ
        elif acc == os.O_WRONLY:
            access = _GENERIC_WRITE
        else:
            access = _GENERIC_READ | _GENERIC_WRITE
        if flags & os.O_APPEND:
            access &= ~_FILE_WRITE_DATA
        creat = bool(flags & os.O_CREAT)
        trunc = bool(flags & os.O_TRUNC)
        if creat and flags & os.O_EXCL:
            disposition = _CREATE
        elif creat:
            disposition = _OVERWRITE_IF if trunc else _OPEN_IF
        else:
            disposition = _OVERWRITE if trunc else _OPEN
        attrs = _ATTR_READONLY if creat and not mode & 0o200 else _ATTR_NORMAL
        handle = _open_checked(
            root, name, access, _SHARE_ALL, disposition, _NON_DIRECTORY_FILE, nofollow, attrs
        )
        return _wrap(handle, os.O_APPEND if flags & os.O_APPEND else 0)

    def mkdir_at(dir_fd: int, name: str, mode: int = 0o700) -> None:
        _check_name(name)
        # shortcut: mode is ignored (ACLs inherited), upgrade if per-dir ACLs are needed
        handle = _nt_open(
            _handle(dir_fd),
            name,
            _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _SHARE_ALL,
            _CREATE,
            _DIRECTORY_FILE,
        )
        _CloseHandle(handle)

    def stat_at(
        dir_fd: int, name: str, follow_symlinks: bool = False
    ) -> os.stat_result:
        _check_name(name)
        handle = _nt_open(
            _handle(dir_fd),
            name,
            _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _SHARE_ALL,
            _OPEN,
            0 if follow_symlinks else _OPEN_REPARSE_POINT,
        )
        attrs, tag = _tag_info(handle)
        fd = _wrap(handle)
        try:
            value = os.fstat(fd)
        finally:
            os.close(fd)
        # os.fstat leaves st_reparse_tag at 0 and never reports S_IFLNK, so rebuild both as os.lstat does
        visible, extra = value.__reduce__()[1]
        mode = visible[0]
        if tag == 0xA000000C:
            mode = (mode & ~0o170000) | _stat.S_IFLNK
        return os.stat_result(
            (mode, *visible[1:]),
            {**extra, "st_reparse_tag": tag, "st_file_attributes": attrs},
        )

    def _set_info(handle: int, info: ctypes.Array[ctypes.c_char] | ctypes.Structure, size: int, cls: int) -> int:
        iosb = _IoStatus()
        return _NtSetInformationFile(handle, ctypes.byref(iosb), ctypes.byref(info), size, cls)

    def _rename_info(flags: int, root: int, name: str) -> tuple[ctypes.Array[ctypes.c_char], int]:
        header = _RenameHeader.FileName.offset
        raw = name.encode("utf-16-le")
        buf = ctypes.create_string_buffer(max(ctypes.sizeof(_RenameHeader), header + len(raw)))
        head = _RenameHeader.from_buffer(buf)
        head.Flags = flags
        head.RootDirectory = root
        head.FileNameLength = len(raw)
        buf[header : header + len(raw)] = raw
        return buf, len(buf)

    def rename_at(src_dir_fd: int, src: str, dst_dir_fd: int, dst: str) -> None:
        _check_name(src)
        _check_name(dst)
        handle = _nt_open(
            _handle(src_dir_fd),
            src,
            _DELETE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _SHARE_ALL,
            _OPEN,
            _OPEN_REPARSE_POINT,
        )
        try:
            root = _handle(dst_dir_fd)
            # REPLACE_IF_EXISTS | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE
            buf, size = _rename_info(0x1 | 0x2 | 0x40, root, dst)
            status = _set_info(handle, buf, size, 65)
            if (status & 0xFFFFFFFF) in _FALLBACK_STATUSES:
                buf, size = _rename_info(0x1, root, dst)
                status = _set_info(handle, buf, size, 10)
            if status < 0:
                raise _status_error(status, dst)
        finally:
            _CloseHandle(handle)

    def _delete(handle: int, name: str) -> None:
        # DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE
        flags = ctypes.c_ulong(0x1 | 0x2 | 0x10)
        status = _set_info(handle, flags, 4, 64)
        if (status & 0xFFFFFFFF) in _FALLBACK_STATUSES:
            status = _set_info(handle, ctypes.c_ubyte(1), 1, 13)
        if status < 0:
            raise _status_error(status, name)

    def _open_for_delete(dir_fd: int, name: str, options: int) -> int:
        _check_name(name)
        return _nt_open(
            _handle(dir_fd),
            name,
            _DELETE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _SHARE_ALL,
            _OPEN,
            options | _OPEN_REPARSE_POINT,
        )

    def unlink_at(dir_fd: int, name: str) -> None:
        handle = _open_for_delete(dir_fd, name, 0)
        try:
            attrs, tag = _tag_info(handle)
            if attrs & _ATTR_DIRECTORY and tag not in _SURROGATE_TAGS:
                raise OSError(errno.EISDIR, "is a directory", name)
            _delete(handle, name)
        finally:
            _CloseHandle(handle)

    def rmdir_at(dir_fd: int, name: str) -> None:
        handle = _open_for_delete(dir_fd, name, _DIRECTORY_FILE)
        try:
            if _is_surrogate(handle):
                raise OSError(errno.ENOTDIR, "not a directory", name)
            _delete(handle, name)
        finally:
            _CloseHandle(handle)

    def listdir_fd(dir_fd: int) -> list[str]:
        handle = _handle(dir_fd)
        buf = ctypes.create_string_buffer(65536)
        names: list[str] = []
        restart = 1
        while True:
            iosb = _IoStatus()
            status = _NtQueryDirectoryFile(
                handle, None, None, None, ctypes.byref(iosb), buf, len(buf), 12, 0, None, restart
            )
            restart = 0
            code = status & 0xFFFFFFFF
            if code in (0x80000006, 0xC000000F):
                return names
            if status < 0:
                raise _status_error(status)
            offset = 0
            while True:
                nxt, _index, length = (
                    int.from_bytes(buf.raw[offset + i : offset + i + 4], "little")
                    for i in (0, 4, 8)
                )
                entry = buf.raw[offset + 12 : offset + 12 + length].decode("utf-16-le")
                if entry not in (".", ".."):
                    names.append(entry)
                if not nxt:
                    break
                offset += nxt

    def link_at(src_dir_fd: int, src: str, dst_dir_fd: int, dst: str) -> None:
        _check_name(src)
        _check_name(dst)
        handle = _nt_open(
            _handle(src_dir_fd),
            src,
            _FILE_WRITE_ATTRIBUTES | _SYNCHRONIZE,
            _SHARE_ALL,
            _OPEN,
            _OPEN_REPARSE_POINT,
        )
        try:
            buf, size = _rename_info(0, _handle(dst_dir_fd), dst)
            status = _set_info(handle, buf, size, 11)
            if status < 0:
                raise _status_error(status, dst)
        finally:
            _CloseHandle(handle)

    def fchmod(fd: int, mode: int) -> None:
        # shortcut: Python < 3.13 has no os.fchmod on Windows, so this is a no-op there; upgrade if read-only bits must be set on 3.11/3.12
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)

    # LockFileEx fails with ERROR_INVALID_PARAMETER on directory handles, so the lock is a named semaphore bound to (volume serial, file id)
    _LOCKS: dict[int, int] = {}
    _CreateSemaphore = _k32.CreateSemaphoreW
    _CreateSemaphore.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_wchar_p]
    _CreateSemaphore.restype = ctypes.c_void_p
    _WaitForSingleObject = _k32.WaitForSingleObject
    _WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD
    _ReleaseSemaphore = _k32.ReleaseSemaphore
    _ReleaseSemaphore.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]

    def lock_ex_nb(fd: int) -> bool:
        if fd in _LOCKS:
            return True
        value = os.fstat(fd)
        name = f"Global\\claude-obsidian-{value.st_dev:x}-{value.st_ino:x}"
        handle = _CreateSemaphore(None, 1, 1, name)
        if not handle:
            raise OSError(
                _WINERROR_ERRNO.get(ctypes.get_last_error(), errno.EIO),
                "CreateSemaphoreW failed",
            )
        if _WaitForSingleObject(handle, 0) == 0:
            _LOCKS[fd] = handle
            return True
        _CloseHandle(handle)
        return False

    def unlock(fd: int) -> None:
        handle = _LOCKS.pop(fd, None)
        if handle is not None:
            _ReleaseSemaphore(handle, 1, None)
            _CloseHandle(handle)

    def fsync_dir(dir_fd: int) -> None:
        # shortcut: pinned dirs open read-only so the flush is usually denied and ignored, upgrade if directory durability is verified needed
        if not _FlushFileBuffers(_handle(dir_fd)):
            err = ctypes.get_last_error()
            if err != 5:
                raise OSError(_WINERROR_ERRNO.get(err, errno.EIO), "FlushFileBuffers failed")

    def fd_path(fd: int) -> str:
        handle = _handle(fd)
        size = 260
        while True:
            buf = ctypes.create_unicode_buffer(size)
            n = _GetFinalPath(handle, buf, size, 0)
            if n == 0:
                raise OSError(
                    _WINERROR_ERRNO.get(ctypes.get_last_error(), errno.EIO),
                    "GetFinalPathNameByHandleW failed",
                )
            if n < size:
                break
            size = n + 1
        path = buf.value
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
        return path

    def process_alive(pid: int) -> bool | None:
        if pid <= 0:
            return False
        handle = _OpenProcess(0x1000, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            if err == 87:
                return False
            return True if err == 5 else None
        try:
            code = wintypes.DWORD()
            if not _GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == _STILL_ACTIVE
        finally:
            _CloseHandle(handle)

