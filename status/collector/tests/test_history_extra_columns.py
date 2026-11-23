"""附加盘、两盘合计两列要跟着时间轴一格一格前移（Owner 11-23：图上没有附加盘和合计的线）。"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collect import sync_extra_columns


class ExtraColumns(unittest.TestCase):
    def test_full_ring_keeps_every_reading(self):
        tier = {"t": list(range(5)), "disk_x": [None] * 4 + [10.0], "disk_all": [None] * 4 + [40.0]}
        for i, v in enumerate((11.0, 12.0, 13.0)):
            tier["t"] = (tier["t"] + [5 + i])[-5:]
            sync_extra_columns(tier, {"disk_x": v, "disk_all": v + 30}, True)
        self.assertEqual(tier["disk_x"], [None, 10.0, 11.0, 12.0, 13.0])
        self.assertEqual(tier["disk_all"], [None, 40.0, 41.0, 42.0, 43.0])

    def test_same_bucket_overwrites_last(self):
        tier = {"t": [0, 1], "disk_x": [5.0, 6.0]}
        sync_extra_columns(tier, {"disk_x": 7.0}, False)
        self.assertEqual(tier["disk_x"], [5.0, 7.0])

    def test_old_archive_is_left_padded(self):
        tier = {"t": [0, 1, 2]}
        sync_extra_columns(tier, {"disk_all": 50.0}, True)
        self.assertEqual(tier["disk_all"], [None, None, 50.0])


if __name__ == "__main__":
    unittest.main()
