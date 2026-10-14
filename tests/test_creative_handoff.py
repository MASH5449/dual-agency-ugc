import json
import unittest
from types import SimpleNamespace

from creative_handoff import generate_production_handoff


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


class FakeOpenAIClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(completions=FakeCompletions(content))
        self.options = None

    def with_options(self, **options):
        self.options = options
        return self


class ProductionHandoffTests(unittest.TestCase):
    def setUp(self):
        creative = {
            "brand_name": "Example Skincare",
            "product_name": "Daily Cleanser",
            "product_url": "https://example.com/cleanser",
            "concept": "Product overview",
            "hook": "Want to see this cleanser?",
            "script": "Want to see this cleanser? Meet our cleanser.",
            "channel": "TikTok",
            "duration_seconds": 20,
        }
        self.creative = creative
        self.client = FakeOpenAIClient(
            json.dumps(
                {
                    "shots": [
                        {
                            "seconds": 10,
                            "visual": "Presenter opens to camera.",
                            "voiceover": "Want to see this cleanser?",
                            "on_screen_text": "Want to see this cleanser?",
                        },
                        {
                            "seconds": 10,
                            "visual": "Show the real product if available.",
                            "voiceover": "Meet our cleanser.",
                            "on_screen_text": "",
                        },
                    ],
                    "checklist": ["Verify the real product directions."],
                }
            )
        )
        self.completion = self.client.chat.completions

    def test_generates_shoot_ready_handoff_without_changing_approved_script(self):
        handoff = generate_production_handoff(self.client, self.creative)

        self.assertEqual(sum(shot["seconds"] for shot in handoff["shots"]), 20)
        self.assertEqual(
            " ".join(shot["voiceover"] for shot in handoff["shots"]),
            self.creative["script"],
        )
        self.assertEqual(
            handoff["shots"][0]["on_screen_text"],
            "Want to see this cleanser?",
        )
        self.assertEqual(len(handoff["checklist"]), 1)
        self.assertEqual(self.completion.request["model"], "gpt-4o-mini")
        self.assertEqual(self.completion.request["max_tokens"], 1500)
        self.assertEqual(
            self.client.options, {"max_retries": 0, "timeout": 45.0}
        )
        self.assertEqual(
            self.completion.request["response_format"], {"type": "json_object"}
        )
        system_prompt = self.completion.request["messages"][0]["content"]
        self.assertIn("Never invent product properties", system_prompt)
        self.assertIn("verbati", system_prompt)

    def test_rejects_provider_copy_not_in_approved_script(self):
        self.completion.content = json.dumps(
            {
                "shots": [
                    {
                        "seconds": 10,
                        "visual": "Presenter opens to camera.",
                        "voiceover": "Want to see this cleanser?",
                        "on_screen_text": "",
                    },
                    {
                        "seconds": 10,
                        "visual": "Show the product.",
                        "voiceover": "This cures acne.",
                        "on_screen_text": "",
                    },
                ],
                "checklist": ["Check claims."],
            }
        )
        with self.assertRaisesRegex(ValueError, "quote the approved script"):
            generate_production_handoff(self.client, self.creative)

    def test_rejects_invalid_response_and_runtime(self):
        self.completion.content = json.dumps({"shots": [], "checklist": []})
        with self.assertRaisesRegex(ValueError, "invalid shot list"):
            generate_production_handoff(self.client, self.creative)
        self.creative["duration_seconds"] = True
        with self.assertRaisesRegex(ValueError, "valid runtime"):
            generate_production_handoff(self.client, self.creative)


if __name__ == "__main__":
    unittest.main()
