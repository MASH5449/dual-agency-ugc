import unittest

from creative_video import HEYGEN_VIDEO_URL, submit_heygen_video


class FakeResponse:
    def __init__(self, status_code, payload=None, invalid_json=False):
        self.status_code = status_code
        self.payload = payload
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid response")
        return self.payload


class CreativeVideoTests(unittest.TestCase):
    def test_submits_exactly_one_approved_script_render(self):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse(200, {"data": {"video_id": "render-123"}})

        result = submit_heygen_video(
            "Approved script.",
            "look-123",
            "voice-123",
            "secret-test-key",
            post,
        )
        self.assertEqual(result, {"status": "queued", "video_id": "render-123", "detail": ""})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], HEYGEN_VIDEO_URL)
        self.assertEqual(
            calls[0][1]["json"],
            {
                "type": "avatar",
                "avatar_id": "look-123",
                "script": "Approved script.",
                "voice_id": "voice-123",
                "aspect_ratio": "9:16",
            },
        )
        self.assertEqual(calls[0][1]["timeout"], 30)
        self.assertEqual(calls[0][1]["headers"]["X-Api-Key"], "secret-test-key")

    def test_provider_rejection_is_retryable_but_ambiguous_response_is_not(self):
        rejected = submit_heygen_video(
            "Script", "look", "voice", "key",
            lambda *args, **kwargs: FakeResponse(400),
        )
        self.assertEqual(rejected["status"], "failed")

        uncertain = submit_heygen_video(
            "Script", "look", "voice", "key",
            lambda *args, **kwargs: FakeResponse(500),
        )
        self.assertEqual(uncertain["status"], "submission_unknown")

        malformed = submit_heygen_video(
            "Script", "look", "voice", "key",
            lambda *args, **kwargs: FakeResponse(200, invalid_json=True),
        )
        self.assertEqual(malformed["status"], "submission_unknown")


if __name__ == "__main__":
    unittest.main()
