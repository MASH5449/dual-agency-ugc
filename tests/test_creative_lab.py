import tempfile
import unittest
from pathlib import Path

from creative_lab import CreativeStore
from prospects import ProspectStore


class CreativeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "agency.sqlite3")
        ProspectStore(self.db_path).initialize()
        self.store = CreativeStore(self.db_path)
        self.store.initialize()
        self.campaign_id = self.store.create_campaign(
            {
                "brand_name": "Example Skincare",
                "product_name": "Daily Cleanser",
                "product_url": "https://example.com/products/cleanser",
                "goal": "Test product page visits.",
                "target_audience": "Adults shopping for a gentle daily cleanser.",
                "offer": "",
                "hypothesis": "A texture demonstration may attract more qualified visits.",
                "primary_metric": "cost per product-page visit",
            }
        )
        self.creative_id = self.store.add_creative(
            self.campaign_id,
            {
                "concept": "Texture demo",
                "hook": "Want to see how it lathers?",
                "script": "Show the product texture and explain the use directions.",
                "channel": "Instagram Reels",
                "duration_seconds": 20,
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_campaign_brief_and_creative_are_persisted(self):
        [campaign] = self.store.list_campaigns()
        self.assertEqual(campaign["brand_name"], "Example Skincare")
        self.assertEqual(campaign["status"], "planning")
        [creative] = campaign["creatives"]
        self.assertEqual(creative["status"], "draft")
        self.assertEqual(creative["hook"], "Want to see how it lathers?")
        self.assertEqual(creative["results"], [])

    def test_draft_can_be_edited_before_approval(self):
        result = self.store.update_creative_draft(
            self.creative_id,
            {
                "concept": "Powder-to-foam demo",
                "hook": "Ever tried a cleanser in powder form?",
                "script": "Show the product and follow its package directions.",
                "channel": "TikTok",
                "duration_seconds": 25,
            },
        )
        self.assertEqual(result, "updated")
        [campaign] = self.store.list_campaigns()
        [creative] = campaign["creatives"]
        self.assertEqual(creative["status"], "draft")
        self.assertEqual(creative["concept"], "Powder-to-foam demo")
        self.assertEqual(creative["hook"], "Ever tried a cleanser in powder form?")
        self.assertEqual(creative["channel"], "TikTok")
        self.assertEqual(creative["duration_seconds"], 25)

    def test_only_drafts_can_be_edited_and_invalid_edits_do_not_apply(self):
        invalid_data = {
            "concept": "Changed concept",
            "hook": "Changed hook",
            "script": "Changed script",
            "channel": "TikTok",
            "duration_seconds": 61,
        }
        with self.assertRaisesRegex(ValueError, "Duration"):
            self.store.update_creative_draft(self.creative_id, invalid_data)

        self.store.update_creative_status(self.creative_id, "approved")
        with self.assertRaisesRegex(ValueError, "Provide concept"):
            self.store.update_creative_draft(self.creative_id, {})
        self.assertEqual(
            self.store.update_creative_draft(
                self.creative_id,
                {
                    "concept": "Changed concept",
                    "hook": "Changed hook",
                    "script": "Changed script",
                    "channel": "TikTok",
                    "duration_seconds": 20,
                },
            ),
            "not_draft",
        )
        [campaign] = self.store.list_campaigns()
        [creative] = campaign["creatives"]
        self.assertEqual(creative["status"], "approved")
        self.assertEqual(creative["concept"], "Texture demo")

    def test_production_handoff_is_persisted_only_for_approved_creative(self):
        handoff = {
            "shots": [
                {
                    "seconds": 10,
                    "visual": "Open on the presenter.",
                    "voiceover": "Want to see how it lathers?",
                    "on_screen_text": "",
                }
            ],
            "checklist": ["Check directions."],
        }
        self.assertEqual(
            self.store.save_production_handoff(self.creative_id, handoff),
            "not_approved",
        )
        self.store.update_creative_status(self.creative_id, "approved")
        self.assertEqual(self.store.get_creative_status(self.creative_id), "approved")
        self.assertFalse(self.store.has_production_handoff(self.creative_id))
        self.assertEqual(
            self.store.save_production_handoff(self.creative_id, handoff),
            "saved",
        )
        self.assertTrue(self.store.has_production_handoff(self.creative_id))
        self.assertEqual(
            self.store.save_production_handoff(self.creative_id, handoff),
            "exists",
        )
        [campaign] = self.store.list_campaigns()
        [creative] = campaign["creatives"]
        self.assertEqual(creative["production_handoff"]["shots"], handoff["shots"])
        self.assertEqual(
            self.store.update_creative_status(
                self.creative_id,
                "produced",
                "https://videos.example.com/texture-demo.mp4",
            ),
            "produced",
        )
        [campaign] = self.store.list_campaigns()
        [creative] = campaign["creatives"]
        self.assertEqual(creative["production_handoff"]["checklist"], ["Check directions."])

    def test_video_render_is_single_active_job_and_requires_human_review(self):
        self.assertEqual(
            self.store.reserve_video_render(self.creative_id),
            (None, "not_approved"),
        )
        self.store.update_creative_status(self.creative_id, "approved")
        job_id, status = self.store.reserve_video_render(self.creative_id)
        self.assertEqual(status, "reserved")
        self.assertEqual(
            self.store.reserve_video_render(self.creative_id),
            (None, "already_active"),
        )
        self.assertTrue(
            self.store.set_video_render_submission(
                job_id,
                {"status": "queued", "video_id": "heygen-job-1", "detail": ""},
            )
        )
        self.assertTrue(
            self.store.finish_video_render(
                "heygen-job-1", "https://cdn.heygen.ai/video-1.mp4"
            )
        )
        self.assertFalse(
            self.store.finish_video_render(
                "heygen-job-1", "https://cdn.heygen.ai/video-1.mp4"
            )
        )
        self.assertEqual(self.store.get_creative_status(self.creative_id), "approved")
        self.assertEqual(
            self.store.review_video_render(job_id, "rejected"),
            "rejected",
        )

        retry_job_id, status = self.store.reserve_video_render(self.creative_id)
        self.assertEqual(status, "reserved")
        self.store.set_video_render_submission(
            retry_job_id,
            {"status": "queued", "video_id": "heygen-job-2", "detail": ""},
        )
        self.store.finish_video_render(
            "heygen-job-2", "https://cdn.heygen.ai/video-2.mp4"
        )
        self.assertEqual(
            self.store.review_video_render(retry_job_id, "approved"),
            "approved",
        )
        self.assertEqual(self.store.get_creative_status(self.creative_id), "produced")
        self.assertEqual(
            self.store.reserve_video_render(self.creative_id),
            (None, "not_approved"),
        )
        [campaign] = self.store.list_campaigns()
        [creative] = campaign["creatives"]
        self.assertEqual(creative["video_url"], "https://cdn.heygen.ai/video-2.mp4")
        self.assertEqual(
            [job["status"] for job in creative["video_jobs"]],
            ["rejected", "approved"],
        )

    def test_uncertain_video_submission_cannot_be_retried_automatically(self):
        self.store.update_creative_status(self.creative_id, "approved")
        job_id, _ = self.store.reserve_video_render(self.creative_id)
        self.store.set_video_render_submission(
            job_id,
            {
                "status": "submission_unknown",
                "detail": "Check HeyGen before retrying.",
            },
        )
        self.assertEqual(
            self.store.reserve_video_render(self.creative_id),
            (None, "already_active"),
        )
        [campaign] = self.store.list_campaigns()
        [creative] = campaign["creatives"]
        self.assertEqual(creative["video_jobs"][0]["status"], "submission_unknown")

    def test_explicit_approval_production_testing_and_manual_results(self):
        with self.assertRaisesRegex(ValueError, "Cannot move"):
            self.store.update_creative_status(self.creative_id, "testing")
        self.assertEqual(
            self.store.update_creative_status(self.creative_id, "approved"),
            "approved",
        )
        with self.assertRaisesRegex(ValueError, "video URL is required"):
            self.store.update_creative_status(self.creative_id, "produced")
        self.store.update_creative_status(
            self.creative_id,
            "produced",
            "https://videos.example.com/texture-demo.mp4",
        )
        self.store.update_creative_status(self.creative_id, "testing")
        result_id = self.store.add_result(
            self.creative_id,
            {
                "start_date": "2026-10-01",
                "end_date": "2026-10-07",
                "spend": 25.50,
                "impressions": 1200,
                "clicks": 60,
                "conversions": 4,
                "revenue": 120,
                "notes": "Values entered from platform report.",
            },
        )
        self.assertGreater(result_id, 0)
        [campaign] = self.store.list_campaigns()
        self.assertEqual(campaign["status"], "testing")
        [creative] = campaign["creatives"]
        self.assertEqual(creative["results"][0]["clicks"], 60)
        self.assertEqual(creative["results"][0]["revenue"], 120.0)

    def test_results_are_not_allowed_before_testing_and_duplicate_periods_fail(self):
        result = {
            "start_date": "2026-10-01",
            "end_date": "2026-10-07",
            "spend": 10,
            "impressions": 100,
            "clicks": 5,
            "conversions": 1,
            "revenue": 20,
        }
        with self.assertRaisesRegex(ValueError, "marked testing"):
            self.store.add_result(self.creative_id, result)
        self.store.update_creative_status(self.creative_id, "approved")
        self.store.update_creative_status(
            self.creative_id, "produced", "https://example.com/video.mp4"
        )
        self.store.update_creative_status(self.creative_id, "testing")
        self.store.add_result(self.creative_id, result)
        with self.assertRaisesRegex(ValueError, "already exist"):
            self.store.add_result(self.creative_id, result)

    def test_validates_campaign_urls_durations_and_nonfinite_money(self):
        invalid_campaign = {
            "brand_name": "Brand",
            "product_name": "Product",
            "product_url": "javascript:alert(1)",
            "goal": "Goal",
            "target_audience": "Audience",
            "hypothesis": "Hypothesis",
            "primary_metric": "CTR",
        }
        with self.assertRaisesRegex(ValueError, "http or https"):
            self.store.create_campaign(invalid_campaign)
        with self.assertRaisesRegex(ValueError, "Duration"):
            self.store.add_creative(
                self.campaign_id,
                {
                    "concept": "Concept",
                    "hook": "Hook",
                    "script": "Script",
                    "channel": "TikTok",
                    "duration_seconds": 61,
                },
            )
        with self.assertRaisesRegex(ValueError, "non-negative number"):
            self.store.add_result(
                self.creative_id,
                {
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-01",
                    "spend": float("nan"),
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0,
                    "revenue": 0,
                },
            )

    def test_campaign_can_only_link_to_an_approved_imported_prospect(self):
        prospects = ProspectStore(self.db_path)
        prospects.import_apify(
            [
                {
                    "id": "brand-1",
                    "name": "Example Skincare",
                    "websiteUrl": "https://example.com",
                    "sampleProducts": [
                        {
                            "title": "Daily Cleanser",
                            "url": "https://example.com/products/cleanser",
                        }
                    ],
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "approved prospect"):
            self.store.create_campaign(
                {
                    "prospect_id": 1,
                    "brand_name": "Example Skincare",
                    "product_name": "Daily Cleanser",
                    "product_url": "https://example.com/products/cleanser",
                    "goal": "Test creative.",
                    "target_audience": "Skincare shoppers.",
                    "hypothesis": "A product demo may work.",
                    "primary_metric": "CTR",
                }
            )
        prospects.review(1, "approved")
        campaign_id = self.store.create_campaign(
            {
                "prospect_id": 1,
                "brand_name": "Example Skincare",
                "product_name": "Daily Cleanser",
                "product_url": "https://example.com/products/cleanser",
                "goal": "Test creative.",
                "target_audience": "Skincare shoppers.",
                "hypothesis": "A product demo may work.",
                "primary_metric": "CTR",
            }
        )
        campaign = next(
            item for item in self.store.list_campaigns()
            if item["id"] == campaign_id
        )
        self.assertEqual(campaign["id"], campaign_id)
        self.assertEqual(campaign["prospect_id"], 1)


if __name__ == "__main__":
    unittest.main()
