import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from usbcopyer.verify import (
    VERIFY_FULL,
    VERIFY_NONE,
    VERIFY_SAMPLE,
    VERIFY_SIZE,
    verify_file,
)

TEST_TMP = Path(__file__).resolve().parents[1] / ".testtmp"
TEST_TMP.mkdir(exist_ok=True)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-", dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src.bin"
        self.dst = self.tmp / "dst.bin"

    def _make(self, size: int, fill: bytes = b"a"):
        data = (fill * (size // len(fill) + 1))[:size]
        self.src.write_bytes(data)
        self.dst.write_bytes(data)
        return data

    def _corrupt(self, offset: int):
        data = bytearray(self.dst.read_bytes())
        data[offset] ^= 0xFF
        self.dst.write_bytes(bytes(data))

    def test_none_always_passes(self):
        self._make(100)
        self._corrupt(50)
        self.assertEqual(verify_file(str(self.src), str(self.dst), VERIFY_NONE, 100), (True, ""))

    def test_size_detects_truncation(self):
        self._make(1000)
        self.dst.write_bytes(b"x" * 500)
        ok, reason = verify_file(str(self.src), str(self.dst), VERIFY_SIZE, 1000)
        self.assertFalse(ok)
        self.assertIn("大小不一致", reason)

    def test_size_passes_same_size(self):
        self._make(1000)
        self._corrupt(500)   # 大小相同但内容不同
        ok, _ = verify_file(str(self.src), str(self.dst), VERIFY_SIZE, 1000)
        self.assertTrue(ok)  # size 模式本来就抓不到内容损坏

    def test_sample_detects_middle_corruption(self):
        self._make(1024 * 1024)
        self._corrupt(512 * 1024)
        ok, reason = verify_file(str(self.src), str(self.dst), VERIFY_SAMPLE, 1024 * 1024)
        self.assertFalse(ok)
        self.assertIn("哈希", reason)

    def test_sample_detects_tail_corruption(self):
        self._make(1024 * 1024)
        self._corrupt(1024 * 1024 - 100)
        ok, _ = verify_file(str(self.src), str(self.dst), VERIFY_SAMPLE, 1024 * 1024)
        self.assertFalse(ok)

    def test_sample_passes_identical(self):
        self._make(1024 * 1024)
        ok, _ = verify_file(str(self.src), str(self.dst), VERIFY_SAMPLE, 1024 * 1024)
        self.assertTrue(ok)

    def test_sample_skips_hash_for_tiny_files(self):
        # 小文件（<64KB）只比大小：内容损坏但大小相同时抓不到——这是速度换来的取舍
        self._make(1000)
        self._corrupt(500)
        ok, _ = verify_file(str(self.src), str(self.dst), VERIFY_SAMPLE, 1000)
        self.assertTrue(ok)

    def test_full_detects_tiny_corruption(self):
        self._make(1000)
        self._corrupt(500)
        ok, _ = verify_file(str(self.src), str(self.dst), VERIFY_FULL, 1000)
        self.assertFalse(ok)

    def test_full_detects_head_corruption(self):
        self._make(1024 * 1024)
        self._corrupt(10)
        ok, _ = verify_file(str(self.src), str(self.dst), VERIFY_FULL, 1024 * 1024)
        self.assertFalse(ok)

    def test_missing_destination(self):
        self._make(100)
        self.dst.unlink()
        ok, reason = verify_file(str(self.src), str(self.dst), VERIFY_SIZE, 100)
        self.assertFalse(ok)
        self.assertIn("不可读", reason)


class CopierVerifyIntegrationTests(unittest.TestCase):
    def test_corrupted_copy_is_reported_as_failure(self):
        """把校验强制成 fail：复制后应删掉目标并计入失败。"""
        from usbcopyer import copier as copier_mod
        from usbcopyer.config import Config
        from usbcopyer.winapi import VolumeInfo
        import threading

        tmp = Path(tempfile.mkdtemp(prefix="vfint-", dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, tmp, True)
        src = tmp / "src"; dst = tmp / "dst"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        vol = VolumeInfo("X", str(src) + "\\", "T", "AAAAAAAA", "NTFS", "removable")
        cfg = Config(destination=str(dst), verify_mode=VERIFY_SAMPLE, use_manifest=False)

        original = copier_mod.verify_file
        copier_mod.verify_file = lambda s, d, m, size: (False, "模拟校验失败")
        try:
            stats = copier_mod.copy_volume(vol, dst, cfg, threading.Event(), lambda *a: None)
        finally:
            copier_mod.verify_file = original

        self.assertEqual(stats.files_copied, 0)
        self.assertEqual(stats.files_failed, 1)
        self.assertFalse((dst / "a.txt").exists())   # 失败的文件必须删掉


if __name__ == "__main__":
    unittest.main()