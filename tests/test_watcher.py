import unittest

from usbcopyer.watcher import DriveWatcher
from usbcopyer.winapi import VolumeInfo


def vol(letter="E", serial="11111111", label="U", dtype="removable"):
    return VolumeInfo(letter=letter, root=f"{letter}:\\", label=label,
                      serial=serial, filesystem="NTFS", drive_type=dtype)


class FakeEnv:
    def __init__(self):
        self.drives: dict[str, VolumeInfo] = {}

    def lister(self):
        return [f"{k}:\\" for k in self.drives]

    def prober(self, root):
        return self.drives.get(root[0].upper())


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.env = FakeEnv()
        self.arrived, self.removed = [], []
        self.watcher = DriveWatcher(
            interval=0.2,
            on_arrive=self.arrived.append,
            on_remove=self.removed.append,
            log=lambda *a: None,
            lister=self.env.lister,
            prober=self.env.prober,
        )
        self.watcher._known = {}

    def test_arrival_fires_once(self):
        self.env.drives["E"] = vol()
        self.watcher._tick()
        self.watcher._tick()
        self.assertEqual(len(self.arrived), 1)
        self.assertEqual(self.arrived[0].letter, "E")

    def test_removal(self):
        self.env.drives["E"] = vol()
        self.watcher._tick()
        del self.env.drives["E"]
        self.watcher._tick()
        self.assertEqual(self.removed, ["E"])

    def test_serial_change_means_swap(self):
        self.env.drives["E"] = vol(serial="AAAAAAAA")
        self.watcher._tick()
        self.env.drives["E"] = vol(serial="BBBBBBBB")
        self.watcher._tick()
        self.assertEqual(self.removed, ["E"])
        self.assertEqual(len(self.arrived), 2)

    def test_not_ready_is_ignored(self):
        self.env.drives["E"] = None  # 盘符在，但卷信息读不到
        self.watcher._tick()
        self.assertEqual(self.arrived, [])
        self.assertIn("E", self.watcher._pending)

    def test_baseline_not_reported(self):
        # start 之前就存在的盘不应触发 on_arrive
        self.env.drives["C"] = vol(letter="C", dtype="fixed")
        known = self.watcher._snapshot()
        self.assertIn("C", known)


if __name__ == "__main__":
    unittest.main()
