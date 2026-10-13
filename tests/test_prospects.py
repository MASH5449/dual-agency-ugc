import tempfile
import unittest
from pathlib import Path

from prospects import ProspectStore


class ProspectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "prospects.sqlite3")
        self.store = ProspectStore(self.db_path)
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_import_deduplicates_by_source_id_and_domain(self):
        records = [
            {
                "id": "shop-1",
                "name": "Example Skincare",
                "websiteUrl": "https://www.example.com/?utm_source=shop_app",
                "contacts": [{"method": "email", "target": "hello@example.com"}],
                "sampleProducts": [{"title": "Daily Cleanser", "url": "https://example.com/products/cleanser"}],
            },
            {
                "id": "shop-2",
                "name": "Duplicate Domain",
                "websiteUrl": "https://example.com",
            },
            {"id": "invalid", "name": "Missing site", "websiteUrl": ""},
        ]

        result = self.store.import_apify({"items": records})

        self.assertEqual(result, {"inserted": 1, "duplicates": 1, "invalid": 1})
        [prospect] = self.store.list_prospects()
        self.assertEqual(prospect["domain"], "example.com")
        self.assertEqual(prospect["status"], "needs_review")
        self.assertEqual(prospect["contacts"][0]["target"], "hello@example.com")
        self.assertEqual(prospect["sampleProducts"][0]["title"], "Daily Cleanser")

    def test_records_persist_and_review_does_not_dispatch(self):
        self.store.import_apify(
            [
                {
                    "id": "shop-1",
                    "name": "Example Skincare",
                    "websiteUrl": "example.com",
                    "sampleProducts": [],
                }
            ]
        )

        self.assertTrue(self.store.review(1, "approved", "Relevant product; verify contact."))
        reopened = ProspectStore(self.db_path)
        [prospect] = reopened.list_prospects("approved")

        self.assertEqual(prospect["status"], "approved")
        self.assertEqual(prospect["review_note"], "Relevant product; verify contact.")

    def test_rejects_wrong_payload_shape_and_large_batch(self):
        with self.assertRaises(ValueError):
            self.store.import_apify({"records": []})
        with self.assertRaises(ValueError):
            self.store.import_apify([{}], max_items=0)

    def test_review_rejects_unsupported_status(self):
        with self.assertRaises(ValueError):
            self.store.review(1, "sent")


if __name__ == "__main__":
    unittest.main()
