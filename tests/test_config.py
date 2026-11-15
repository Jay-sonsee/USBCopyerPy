import shutil
import tempfile
import unittest
from pathlib import Path

from usbcopyer.config import Config

TEST_TMP = Path(__file__).resolve().parents[1] / ".testtmp"
TEST_TMP.mkdir(exist_ok=True)


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="cfg-", dir=TEST_TMP))


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.folder_rule, "[serial]")
        self.assertIn("remote", cfg.skip_types)
        self.assertNotIn("removable", cfg.skip_types)

    def test_roundtrip(self):
        tmp = temp_dir()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "config.json"
        cfg = Config(destination="D:\\backup", conflict="rename", delay_seconds=7,
                     ext_mode="white", white_exts="jpg,png", skip_types=["remote"])
        cfg.save(path)
        loaded = Config.load(path)
        self.assertEqual(loaded.destination, "D:\\backup")
        self.assertEqual(loaded.conflict, "rename")
        self.assertEqual(loaded.delay_seconds, 7)
        self.assertEqual(loaded.skip_types, ["remote"])

    def test_unknown_keys_ignored(self):
        cfg = Config.from_dict({"destination": "X", "不存在": 1})
        self.assertEqual(cfg.destination, "X")

    def test_type_coercion(self):
        cfg = Config.from_dict({"delay_seconds": "12", "copy_no_ext": 0, "poll_interval": "2.5"})
        self.assertEqual(cfg.delay_seconds, 12)
        self.assertFalse(cfg.copy_no_ext)
        self.assertAlmostEqual(cfg.poll_interval, 2.5)

    def test_broken_json_falls_back(self):
        tmp = temp_dir()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "config.json"
        path.write_text("{ not json", encoding="utf-8")
        cfg = Config.load(path)
        self.assertEqual(cfg.folder_rule, "[serial]")

    def test_validate_missing_destination(self):
        problems = Config().validate()
        self.assertTrue(any("备份目录" in p for p in problems))

    def test_validate_bad_rule(self):
        cfg = Config(destination="D:\\x")
        cfg.folder_rule = "[nope]"
        self.assertTrue(any("分类规则" in p for p in cfg.validate()))

    def test_validate_ok(self):
        cfg = Config(destination="D:\\x", folder_rule="[serial]")
        self.assertEqual(cfg.validate(), [])

    def test_data_paths(self):
        cfg = Config(config_dir="C:\\tmp\\cfg")
        self.assertEqual(str(cfg.root_dir), "C:\\tmp\\cfg")
        self.assertTrue(str(cfg.data_root).endswith("USBCopyerData"))


if __name__ == "__main__":
    unittest.main()