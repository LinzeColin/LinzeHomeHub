import math
import unittest

from test_support import locate
locate()

from controlplane.prices import validate_prices


class PriceValidationTests(unittest.TestCase):
    def test_valid_payload_is_canonical(self):
        out = validate_prices({"items": [{"name": "OVH", "amount": "12.50", "currency": "aud", "cadence": "monthly", "auto_renew": False, "purchase": "2026-07-27", "track_renew": True}]})
        self.assertEqual(out["items"][0]["amount"], 12.5)
        self.assertEqual(out["items"][0]["currency"], "AUD")

    def test_nonfinite_and_boolean_amounts_rejected(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_prices({"items": [{"name": "x", "amount": value}]})

    def test_invalid_date_duplicate_name_and_string_boolean_rejected(self):
        bad = [
            {"items": [{"name": "x", "amount": 1, "purchase": "2026-02-31"}]},
            {"items": [{"name": "X", "amount": 1}, {"name": "x", "amount": 2}]},
            {"items": [{"name": "x", "amount": 1, "auto_renew": "false"}]},
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_prices(payload)

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            validate_prices({"items": [{"name": "x", "amount": 1, "script": "alert(1)"}]})


if __name__ == "__main__":
    unittest.main()
