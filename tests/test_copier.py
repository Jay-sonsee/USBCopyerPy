import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from usbcopyer.config import Config
from usbcopyer.copier import assert_target_outside_source, copy_volume
from usbcopyer.winapi import VolumeInfo

TEST_TMP = Path(__file__).resolve().parents[1] / ".testtmp"
TEST_TMP.mkdir(exist_ok=True)


def make_volume(root: str) -> VolumeInfo:
    return VolumeInfo(letter=root[0].upper(), root=root, label="TEST", serial="DEADBEEF",
                      filesystem="NTFS", drive_type="removable")


class CopierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="copy-", dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.src = self.tmp / "src"
        self.dst = self.tmp / "dst"
        self.src.mkdir()
        (self.src / "a.txt").write_text("hello", encoding="utf-8")
        (self.src / "sub").mkdir()
        (self.src / "sub" / "b.bin").write_bytes(b"\x00" * 2048)
        (self.src / "empty").mkdir()
        self.cfg = Config(destination=str(self.dst))
        self.volume = make_volume(str(self.src) + "\\")

    def _copy(self, cancel=None, dry_run=False):
        cancel = cancel or threading.Event()
        return copy_volume(self.volume, self.dst, self.cfg, cancel, lambda *a: None, dry_run=dry_run)

    def test_basic_copy(self):
        stats = self._copy()
        self.assertEqual(stats.files_copied, 2)
        self.assertEqual(stats.files_failed, 0)
        self.assertEqual((self.dst / "a.txt").read_text(encoding="utf-8"), "hello")
        self.assertTrue((self.dst / "sub" / "b.bin").exists())

    def test_empty_dir_skipped(self):
        self._copy()
        self.assertFalse((self.dst / "empty").exists())

    def test_mtime_preserved(self):
        old = time.time() - 86400
        os.utime(self.src / "a.txt", (old, old))
        self._copy()
        self.assertAlmostEqual((self.dst / "a.txt").stat().st_mtime, old, delta=2)

    def test_skip_if_newer(self):
        self._copy()
        future = time.time() + 3600
        os.utime(self.dst / "a.txt", (future, future))
        stats = self._copy()
        self.assertEqual(stats.files_copied, 0)
        self.assertGreaterEqual(stats.files_skipped, 1)

    def test_overwrite(self):
        self.cfg.conflict = "overwrite"
        self._copy()
        (self.src / "a.txt").write_text("changed", encoding="utf-8")
        future = time.time() + 3600
        os.utime(self.dst / "a.txt", (future, future))
        stats = self._copy()
        self.assertEqual(stats.files_copied, 2)
        self.assertEqual((self.dst / "a.txt").read_text(encoding="utf-8"), "changed")

    def test_rename_conflict_creates_valid_path(self):
        self.cfg.conflict = "rename"
        self._copy()
        stats = self._copy()
        self.assertEqual(stats.files_failed, 0)
        names = [p.name for p in self.dst.iterdir() if p.is_file()]
        self.assertTrue(any(n.endswith("-a.txt") and len(n) == len("a.txt") + 9 for n in names), names)

    def test_ext_blacklist(self):
        self.cfg.ext_mode = "black"
        self.cfg.black_exts = "bin"
        stats = self._copy()
        self.assertEqual(stats.files_copied, 1)
        self.assertFalse((self.dst / "sub" / "b.bin").exists())

    def test_ext_whitelist(self):
        self.cfg.ext_mode = "white"
        self.cfg.white_exts = "bin"
        stats = self._copy()
        self.assertEqual(stats.files_copied, 1)
        self.assertFalse((self.dst / "a.txt").exists())

    def test_size_max(self):
        self.cfg.size_mode = "max"
        self.cfg.size_limit_mb = 0
        stats = self._copy()
        self.assertEqual(stats.files_copied, 0)

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        stats = self._copy(cancel=cancel)
        self.assertTrue(stats.cancelled)
        self.assertEqual(stats.files_copied, 0)
        self.assertFalse((self.dst / "a.txt").exists())

    def test_destination_inside_source_rejected(self):
        bad = self.src / "backup"
        with self.assertRaises(ValueError):
            copy_volume(self.volume, bad, self.cfg, threading.Event(), lambda *a: None)

    def test_dry_run_writes_nothing(self):
        stats = self._copy(dry_run=True)
        self.assertEqual(stats.files_copied, 2)
        self.assertFalse(self.dst.exists())

    def test_clear_target_first(self):
        self._copy()
        (self.dst / "stale.txt").write_text("old", encoding="utf-8")
        self.cfg.clear_target_first = True
        self._copy()
        self.assertFalse((self.dst / "stale.txt").exists())


class TargetGuardTests(unittest.TestCase):
    def test_cross_drive_allowed(self):
        # U: 复制到 C: 是正常场景，不能误报
        assert_target_outside_source("U:\\", "C:\\backup")

    def test_inside_source_rejected(self):
        with self.assertRaises(ValueError):
            assert_target_outside_source("C:\\data", "C:\\data\\backup")

    def test_sibling_allowed(self):
        assert_target_outside_source("C:\\data", "C:\\backup")

    def test_same_path_rejected(self):
        with self.assertRaises(ValueError):
            assert_target_outside_source("C:\\data", "C:\\data")

if __name__ == "__main__":
    unittest.main()