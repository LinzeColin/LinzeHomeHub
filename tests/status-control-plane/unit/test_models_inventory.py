import unittest

from test_support import locate
locate()

from controlplane.inventory import InventorySnapshot, reconcile_inventories
from controlplane.models import InventoryRecord, stable_id


class InventoryTests(unittest.TestCase):
    def record(self, source, name, state="UNKNOWN"):
        return InventoryRecord(stable_id("project", name), "project", name, source, runtime_state=state)

    def test_runtime_only_is_not_healthy_coverage(self):
        result = reconcile_inventories(
            InventorySnapshot((), True, "d1"),
            InventorySnapshot((), True, "s1"),
            InventorySnapshot((self.record("runtime", "orphan", "HEALTHY"),), True, "r1"),
        )
        self.assertEqual(result.coverage_health, "DEGRADED")
        self.assertEqual(result.items[0].state, "DEPLOYED_UNREGISTERED")
        self.assertEqual(result.runtime_health, "HEALTHY")

    def test_unavailable_inventory_is_unknown(self):
        item = self.record("declared", "alpha")
        result = reconcile_inventories(
            InventorySnapshot((item,), True, "d1"),
            InventorySnapshot((), False, "UNKNOWN", "API failure"),
            InventorySnapshot((), True, "r1"),
        )
        self.assertEqual(result.coverage_health, "UNKNOWN")
        self.assertEqual(result.items[0].state, "INVENTORY_UNAVAILABLE")

    def test_declared_source_runtime_healthy(self):
        name = "alpha"
        result = reconcile_inventories(
            InventorySnapshot((self.record("declared", name),), True, "d"),
            InventorySnapshot((self.record("source", name),), True, "s"),
            InventorySnapshot((self.record("runtime", name, "HEALTHY"),), True, "r"),
        )
        self.assertEqual(result.coverage_health, "HEALTHY")
        self.assertEqual(result.runtime_health, "HEALTHY")


if __name__ == "__main__":
    unittest.main()
