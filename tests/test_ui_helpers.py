import unittest

from usbcopyer.app import CopyTask
from usbcopyer.ui import format_duration


class DurationTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_duration(0), "00:00")

    def test_seconds(self):
        self.assertEqual(format_duration(65), "01:05")

    def test_hours(self):
        self.assertEqual(format_duration(3725), "1:02:05")

    def test_negative_clamped(self):
        self.assertEqual(format_duration(-10), "00:00")


class PercentTests(unittest.TestCase):
    def test_by_bytes(self):
        from usbcopyer.winapi import VolumeInfo
        vol = VolumeInfo("E", "E:\\", "U", "AAAA", "NTFS", "removable")
        task = CopyTask(volume=vol, target=None)
        task.total_bytes = 200
        task.done_bytes = 50
        self.assertAlmostEqual(task.percent, 25.0)

    def test_falls_back_to_files(self):
        from usbcopyer.winapi import VolumeInfo
        vol = VolumeInfo("E", "E:\\", "U", "AAAA", "NTFS", "removable")
        task = CopyTask(volume=vol, target=None)
        task.total_files = 10
        task.done_files = 3
        self.assertAlmostEqual(task.percent, 30.0)

    def test_empty_is_zero(self):
        from usbcopyer.winapi import VolumeInfo
        vol = VolumeInfo("E", "E:\\", "U", "AAAA", "NTFS", "removable")
        self.assertEqual(CopyTask(volume=vol, target=None).percent, 0.0)


if __name__ == "__main__":
    unittest.main()