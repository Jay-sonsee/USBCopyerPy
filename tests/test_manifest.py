import shutil
import tempfile
import unittest
from pathlib import Path

from usbcopyer.manifest import MANIFEST_NAME, Manifest

TEST_TMP = Path(__file__).resolve().parents[1] / ".testtmp"
TEST_TMP.mkdir(exist_ok=True)


class ManifestUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mfunit-", dir=TEST_TMP))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_save_and_load(self):
        target = self.tmp / "dst"
        target.mkdir()
        m = Manifest.for_target(target, "E:\\").load()
        m.record("a.txt", 10, 123456)
        self.assertTrue(m.save())
        self.assertTrue((target / MANIFEST_NAME).exists())

        again = Manifest.for_target(target).load()
        self.assertEqual(again.count, 1)
        self.assertEqual(again.source, "E:\\")

    def test_save_skips_when_clean(self):
        target = self.tmp / "dst"
        target.mkdir()
        m = Manifest.for_target(target).load()
        self.assertFalse(m.save())

    def test_broken_json_is_ignored(self):
        target = self.tmp / "dst"
        target.mkdir()
        (target / MANIFEST_NAME).write_text("{oops", encoding="utf-8")
        self.assertEqual(Manifest.for_target(target).load().count, 0)

    def test_version_mismatch_is_ignored(self):
        target = self.tmp / "dst"
        target.mkdir()
        (target / MANIFEST_NAME).write_text('{"version": 99, "files": {"a": {}}}', encoding="utf-8")
        self.assertEqual(Manifest.for_target(target).load().count, 0)

    def test_is_unchanged_requires_dest(self):
        target = self.tmp / "dst"
        target.mkdir()
        dest = target / "a.txt"
        dest.write_text("x", encoding="utf-8")
        m = Manifest.for_target(target).load()
        m.record("a.txt", 1, 111)
        self.assertTrue(m.is_unchanged("a.txt", 1, 111, dest))
        dest.unlink()
        self.assertFalse(m.is_unchanged("a.txt", 1, 111, dest))
        self.assertFalse(m.is_unchanged("a.txt", 2, 111, dest))
        self.assertFalse(m.is_unchanged("a.txt", 1, 222, dest))


if __name__ == "__main__":
    unittest.main()