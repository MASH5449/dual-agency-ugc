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

    def test_outreach_requires_approved_prospect_and_published_email(self):
        self.store.import_apify(
            [{
                "id": "shop-1",
                "name": "Example Skincare",
                "websiteUrl": "example.com",
                "contacts": [{"method": "email", "target": "hello@example.com"}],
            }]
        )

        self.assertEqual(self.store.create_outreach_draft(1, "Subject", "Body"), "not_approved")
        self.store.review(1, "approved")
        self.assertEqual(self.store.create_outreach_draft(1, "Subject", "Body"), "draft")
        [prospect] = self.store.list_outreach_prospects()
        self.assertEqual(prospect["email"], "hello@example.com")
        self.assertEqual(prospect["outreach_status"], "draft")

    def test_outreach_edit_and_reservation_are_single_use(self):
        self.store.import_apify(
            [{
                "id": "shop-1",
                "name": "Example Skincare",
                "websiteUrl": "example.com",
                "contacts": [{"method": "email", "target": "hello@example.com"}],
            }]
        )
        self.store.review(1, "approved")
        self.store.create_outreach_draft(1, "Original subject", "Original body")
        self.assertEqual(
            self.store.update_outreach_draft(1, {"subject": "Edited", "body": "Edited body"}),
            "updated",
        )

        status, draft = self.store.reserve_outreach_send(1)
        self.assertEqual(status, "sending")
        self.assertEqual(draft["subject"], "Edited")
        self.assertEqual(self.store.reserve_outreach_send(1)[0], "not_draft")
        self.assertTrue(self.store.finish_outreach_send(1, "enrolled", "instantly-1"))
        self.assertEqual(self.store.reserve_outreach_send(1)[0], "not_draft")

    def test_suppression_blocks_draft_creation_and_send_reservation(self):
        self.store.import_apify(
            [{
                "id": "shop-1",
                "name": "Example Skincare",
                "websiteUrl": "example.com",
                "contacts": [{"method": "email", "target": "hello@example.com"}],
            }]
        )
        self.store.review(1, "approved")
        self.store.suppress_outreach_email(1, "Asked not to be contacted")
        self.assertTrue(self.store.is_email_suppressed("HELLO@example.com"))
        self.assertEqual(
            self.store.create_outreach_draft(1, "Subject", "Body"),
            "suppressed",
        )


if __name__ == "__main__":
    unittest.main()
