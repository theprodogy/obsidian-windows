"""fd-relative primitives behave identically on POSIX and native Windows."""
import errno
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_obsidian import fsat
from claude_obsidian.paths import is_name_surrogate

BIN = getattr(os, "O_BINARY", 0)
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | BIN
DIRFLAGS = os.O_RDONLY | fsat.O_DIRECTORY | fsat.O_NOFOLLOW
WINDOWS = os.name == "nt"


def write_file(fd, name, data=b"hello"):
    handle = fsat.open_at(fd, name, CREATE)
    try:
        os.write(handle, data)
        os.fsync(handle)
    finally:
        os.close(handle)


def read_file(fd, name):
    handle = fsat.open_at(fd, name, os.O_RDONLY | BIN)
    try:
        return os.read(handle, 1 << 16)
    finally:
        os.close(handle)


class FsatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = os.path.realpath(self.temporary.name)
        self.fds = []
        self.addCleanup(self.close_all)

    def close_all(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass

    def pin(self, path=None):
        fd = fsat.open_dir(path or self.base)
        self.fds.append(fd)
        return fd

    def test_supported(self):
        self.assertTrue(fsat.supported())

    def test_create_read_write_and_exclusive(self):
        fd = self.pin()
        write_file(fd, "a.txt", b"one\r\ntwo")
        self.assertEqual(read_file(fd, "a.txt"), b"one\r\ntwo")
        with self.assertRaises(OSError) as ctx:
            fsat.open_at(fd, "a.txt", CREATE)
        self.assertEqual(ctx.exception.errno, errno.EEXIST)
        with self.assertRaises(OSError) as ctx:
            fsat.open_at(fd, "missing", os.O_RDONLY | BIN)
        self.assertEqual(ctx.exception.errno, errno.ENOENT)
        handle = fsat.open_at(fd, "a.txt", os.O_WRONLY | os.O_TRUNC | BIN)
        os.close(handle)
        self.assertEqual(read_file(fd, "a.txt"), b"")
        handle = fsat.open_at(fd, "a.txt", os.O_WRONLY | os.O_APPEND | BIN)
        os.write(handle, b"x")
        os.write(handle, b"y")
        os.close(handle)
        self.assertEqual(read_file(fd, "a.txt"), b"xy")

    def test_open_at_directory_errors(self):
        fd = self.pin()
        write_file(fd, "f")
        fsat.mkdir_at(fd, "d")
        with self.assertRaises(OSError) as ctx:
            fsat.open_at(fd, "f", DIRFLAGS)
        self.assertEqual(ctx.exception.errno, errno.ENOTDIR)
        with self.assertRaises(OSError) as ctx:
            fsat.open_at(fd, "d", os.O_WRONLY | BIN)
        self.assertIn(ctx.exception.errno, {errno.EISDIR, errno.EACCES})
        with self.assertRaises(FileExistsError):
            fsat.mkdir_at(fd, "d")

    def test_mkdir_listdir_unlink_rmdir(self):
        fd = self.pin()
        fsat.mkdir_at(fd, "sub")
        write_file(fd, "f")
        self.assertEqual(sorted(fsat.listdir_fd(fd)), ["f", "sub"])
        with self.assertRaises(OSError) as ctx:
            fsat.unlink_at(fd, "sub")
        self.assertIn(ctx.exception.errno, {errno.EISDIR, errno.EPERM})
        with self.assertRaises(OSError) as ctx:
            fsat.rmdir_at(fd, "f")
        self.assertEqual(ctx.exception.errno, errno.ENOTDIR)
        sub = self.pin(os.path.join(self.base, "sub"))
        write_file(sub, "inner")
        with self.assertRaises(OSError) as ctx:
            fsat.rmdir_at(fd, "sub")
        self.assertIn(ctx.exception.errno, {errno.ENOTEMPTY, errno.EEXIST, errno.EACCES})
        fsat.unlink_at(sub, "inner")
        os.close(sub)
        self.fds.remove(sub)
        fsat.rmdir_at(fd, "sub")
        fsat.unlink_at(fd, "f")
        self.assertEqual(fsat.listdir_fd(fd), [])
        with self.assertRaises(FileNotFoundError):
            fsat.unlink_at(fd, "f")

    def test_listdir_many_entries(self):
        fd = self.pin()
        names = {f"file-{index:04d}-{'x' * 40}" for index in range(2000)}
        for name in names:
            write_file(fd, name, b"")
        self.assertEqual(set(fsat.listdir_fd(fd)), names)

    def test_rename_replaces_across_directories(self):
        left = self.pin()
        fsat.mkdir_at(left, "other")
        right = self.pin(os.path.join(self.base, "other"))
        write_file(left, "src", b"new")
        write_file(right, "dst", b"old")
        fsat.rename_at(left, "src", right, "dst")
        self.assertEqual(read_file(right, "dst"), b"new")
        with self.assertRaises(FileNotFoundError):
            fsat.stat_at(left, "src")
        write_file(right, "ro", b"x")
        os.chmod(os.path.join(self.base, "other", "ro"), stat.S_IREAD)
        write_file(left, "over", b"z")
        fsat.rename_at(left, "over", right, "ro")
        self.assertEqual(read_file(right, "ro"), b"z")

    def test_stat_at_matches_lstat(self):
        fd = self.pin()
        write_file(fd, "f", b"abc")
        fsat.mkdir_at(fd, "d")
        for name in ("f", "d"):
            via_fd = fsat.stat_at(fd, name)
            direct = os.lstat(os.path.join(self.base, name))
            self.assertNotEqual(via_fd.st_ino, 0)
            self.assertEqual((via_fd.st_dev, via_fd.st_ino), (direct.st_dev, direct.st_ino))
            self.assertEqual(stat.S_ISDIR(via_fd.st_mode), name == "d")
        self.assertEqual(fsat.stat_at(fd, "f").st_size, 3)
        dir_stat = os.fstat(fd)
        self.assertEqual(dir_stat.st_ino, os.lstat(self.base).st_ino)

    def test_link_at_is_create_only(self):
        fd = self.pin()
        write_file(fd, "a", b"data")
        fsat.link_at(fd, "a", fd, "b")
        self.assertEqual(read_file(fd, "b"), b"data")
        self.assertEqual(fsat.stat_at(fd, "a").st_ino, fsat.stat_at(fd, "b").st_ino)
        with self.assertRaises(OSError) as ctx:
            fsat.link_at(fd, "a", fd, "b")
        self.assertEqual(ctx.exception.errno, errno.EEXIST)

    def test_fchmod(self):
        fd = self.pin()
        handle = fsat.open_at(fd, "m", CREATE)
        try:
            fsat.fchmod(handle, 0o600)
        finally:
            os.close(handle)

    def test_fd_path_round_trip(self):
        fd = self.pin()
        self.assertEqual(os.path.normcase(fsat.fd_path(fd)), os.path.normcase(self.base))
        fsat.mkdir_at(fd, "child")
        child = fsat.open_at(fd, "child", DIRFLAGS)
        self.fds.append(child)
        self.assertEqual(
            os.path.normcase(fsat.fd_path(child)),
            os.path.normcase(os.path.join(self.base, "child")),
        )

    def test_fsync_dir(self):
        fsat.fsync_dir(self.pin())

    def test_process_alive(self):
        self.assertIs(fsat.process_alive(os.getpid()), True)
        self.assertIs(fsat.process_alive(0), False)
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertIn(fsat.process_alive(child.pid), (False, None))

    def test_lock_ex_nb_conflicts_and_releases(self):
        first = self.pin()
        second = self.pin()
        self.assertTrue(fsat.lock_ex_nb(first))
        self.assertFalse(fsat.lock_ex_nb(second))
        fsat.unlock(first)
        self.assertTrue(fsat.lock_ex_nb(second))
        fsat.unlock(second)

    def test_lock_conflicts_across_processes(self):
        fd = self.pin()
        self.assertTrue(fsat.lock_ex_nb(fd))
        self.addCleanup(fsat.unlock, fd)
        code = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from claude_obsidian import fsat\n"
            "fd = fsat.open_dir(sys.argv[2])\n"
            "sys.exit(0 if fsat.lock_ex_nb(fd) else 7)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(ROOT), self.base])
        self.assertEqual(result.returncode, 7)

    def test_open_dir_rejects_file_and_missing(self):
        fd = self.pin()
        write_file(fd, "f")
        with self.assertRaises(OSError) as ctx:
            fsat.open_dir(os.path.join(self.base, "f"))
        self.assertEqual(ctx.exception.errno, errno.ENOTDIR)
        with self.assertRaises(FileNotFoundError):
            fsat.open_dir(os.path.join(self.base, "nope"))


@unittest.skipUnless(WINDOWS, "Windows handle semantics")
class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = os.path.realpath(self.temporary.name)

    def test_junction_is_refused_but_statable(self):
        target = os.path.join(self.base, "target")
        os.mkdir(target)
        link = os.path.join(self.base, "junction")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target], check=True, capture_output=True)
        fd = fsat.open_dir(self.base)
        self.addCleanup(os.close, fd)
        with self.assertRaises(OSError) as ctx:
            fsat.open_at(fd, "junction", DIRFLAGS)
        self.assertEqual(ctx.exception.errno, errno.ELOOP)
        with self.assertRaises(OSError) as ctx:
            fsat.open_dir(link)
        self.assertEqual(ctx.exception.errno, errno.ELOOP)
        self.assertTrue(is_name_surrogate(fsat.stat_at(fd, "junction")))
        self.assertFalse(is_name_surrogate(fsat.stat_at(fd, "target")))
        handle = fsat.open_at(fd, "junction", os.O_RDONLY | fsat.O_DIRECTORY)
        os.close(handle)
        with self.assertRaises(OSError) as ctx:
            fsat.rmdir_at(fd, "junction")
        self.assertEqual(ctx.exception.errno, errno.ENOTDIR)
        fsat.unlink_at(fd, "junction")
        self.assertTrue(os.path.isdir(target))

    def test_file_symlink_is_refused(self):
        target = os.path.join(self.base, "real")
        Path(target).write_bytes(b"x")
        link = os.path.join(self.base, "link")
        try:
            os.symlink(target, link)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        fd = fsat.open_dir(self.base)
        self.addCleanup(os.close, fd)
        with self.assertRaises(OSError) as ctx:
            fsat.open_at(fd, "link", os.O_RDONLY | fsat.O_NOFOLLOW | BIN)
        self.assertEqual(ctx.exception.errno, errno.ELOOP)
        followed = fsat.open_at(fd, "link", os.O_RDONLY | BIN)
        self.assertEqual(os.read(followed, 10), b"x")
        os.close(followed)
        self.assertTrue(is_name_surrogate(fsat.stat_at(fd, "link")))

    def test_pinned_directory_cannot_be_renamed(self):
        sub = os.path.join(self.base, "pinned")
        os.mkdir(sub)
        fd = fsat.open_dir(sub)
        with self.assertRaises(OSError):
            os.rename(sub, sub + "-moved")
        os.close(fd)
        os.rename(sub, sub + "-moved")

    def test_invalid_names(self):
        fd = fsat.open_dir(self.base)
        self.addCleanup(os.close, fd)
        for name in ("a\\b", "a/b", "..", ".", "", "x:stream", "c:"):
            with self.assertRaises(OSError, msg=name) as ctx:
                fsat.open_at(fd, name, CREATE)
            self.assertEqual(ctx.exception.errno, errno.EINVAL)
            with self.assertRaises(OSError):
                fsat.mkdir_at(fd, name)
            with self.assertRaises(OSError):
                fsat.stat_at(fd, name)
            with self.assertRaises(OSError):
                fsat.unlink_at(fd, name)

    def test_case_insensitive_lookup(self):
        fd = fsat.open_dir(self.base)
        self.addCleanup(os.close, fd)
        write_file(fd, "Case.txt")
        self.assertEqual(read_file(fd, "case.TXT"), b"hello")

    def test_fd_path_has_no_extended_prefix(self):
        fd = fsat.open_dir(self.base)
        self.addCleanup(os.close, fd)
        self.assertFalse(fsat.fd_path(fd).startswith("\\\\?\\"))

    def test_stat_reparse_tag_populated(self):
        target = os.path.join(self.base, "t")
        os.mkdir(target)
        link = os.path.join(self.base, "j")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target], check=True, capture_output=True)
        fd = fsat.open_dir(self.base)
        self.addCleanup(os.close, fd)
        self.assertEqual(fsat.stat_at(fd, "j").st_reparse_tag, 0xA0000003)


@unittest.skipIf(WINDOWS, "POSIX fd semantics")
class PosixTests(unittest.TestCase):
    def test_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.realpath(tmp)
            os.mkdir(os.path.join(base, "t"))
            os.symlink("t", os.path.join(base, "l"))
            fd = fsat.open_dir(base)
            self.addCleanup(os.close, fd)
            with self.assertRaises(OSError) as ctx:
                fsat.open_at(fd, "l", DIRFLAGS)
            self.assertIn(ctx.exception.errno, {errno.ELOOP, errno.ENOTDIR})


if __name__ == "__main__":
    unittest.main()
