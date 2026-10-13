import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from creative_generation import (
    generate_creative_concepts,
    validate_generation_options,
)
from creative_lab import CreativeStore
from prospects import ProspectStore


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content)
                )
            ]
        )


class CreativeGenerationTests(unittest.TestCase):
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
                "target_audience": "Adults shopping for a daily cleanser.",
                "offer": "",
                "hypothesis": "A routine demonstration may attract qualified visits.",
                "primary_metric": "cost per product-page visit",
            }
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_options_require_bounded_count_channel_and_duration(self):
        self.assertEqual(
            validate_generation_options(
                {"count": 3, "channel": " TikTok ", "duration_seconds": 20}
            ),
            (3, "TikTok", 20),
        )
        for options in (
            {"count": True, "channel": "TikTok", "duration_seconds": 20},
            {"count": 11, "channel": "TikTok", "duration_seconds": 20},
            {"count": 3, "channel": "", "duration_seconds": 20},
            {"count": 3, "channel": "TikTok", "duration_seconds": 61},
            {"count": 3, "channel": "TikTok", "duration_seconds": 20, "extra": 1},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_generation_options(options)

    def test_generates_and_saves_reviewable_draft_concepts(self):
        completion = FakeCompletions(
            json.dumps(
                {
                    "concepts": [
                        {
                            "concept": "A simple routine",
                            "hook": "What does a simple cleanse look like?",
                            "script": "Here is a closer look at this daily cleanser.",
                        },
                        {
                            "concept": "Product page walkthrough",
                            "hook": "Curious what is in this cleanser?",
                            "script": "Let's look at the product details together.",
                        },
                    ]
                }
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completion)
        )

        drafts = generate_creative_concepts(
            client,
            self.store.get_campaign_brief(self.campaign_id),
            2,
            "TikTok",
            20,
        )
        ids = self.store.add_creatives(self.campaign_id, drafts)

        self.assertEqual(len(ids), 2)
        self.assertEqual(completion.request["response_format"], {"type": "json_object"})
        self.assertIn("Never invent ingredients", completion.request["messages"][0]["content"])
        [campaign] = self.store.list_campaigns()
        self.assertEqual([item["status"] for item in campaign["creatives"]], ["draft", "draft"])
        self.assertEqual(campaign["creatives"][1]["channel"], "TikTok")
        self.assertEqual(campaign["creatives"][1]["duration_seconds"], 20)

    def test_generation_rejects_invalid_response_without_returning_partial_drafts(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=FakeCompletions(json.dumps({"concepts": []}))
            )
        )
        with self.assertRaisesRegex(ValueError, "unexpected concept count"):
            generate_creative_concepts(
                client,
                self.store.get_campaign_brief(self.campaign_id),
                2,
                "TikTok",
                20,
            )

        [campaign] = self.store.list_campaigns()
        self.assertEqual(campaign["creatives"], [])

    def test_generation_rejects_provider_response_without_choices(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(choices=[])
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "returned no choices"):
            generate_creative_concepts(
                client,
                self.store.get_campaign_brief(self.campaign_id),
                1,
                "TikTok",
                20,
            )

    def test_batch_validation_prevents_partial_saves(self):
        drafts = [
            {
                "concept": "First concept",
                "hook": "A valid hook",
                "script": "A valid script",
                "channel": "TikTok",
                "duration_seconds": 20,
            },
            {
                "concept": "Second concept",
                "hook": "A valid hook",
                "script": "A valid script",
                "channel": "TikTok",
                "duration_seconds": 61,
            },
        ]
        with self.assertRaisesRegex(ValueError, "Duration"):
            self.store.add_creatives(self.campaign_id, drafts)

        [campaign] = self.store.list_campaigns()
        self.assertEqual(campaign["creatives"], [])


if __name__ == "__main__":
    unittest.main()
