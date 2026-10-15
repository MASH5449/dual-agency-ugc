import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apify_intake import (
    APIFY_API_BASE,
    DEFAULT_ACTOR_ID,
    DEFAULT_ACTOR_INPUT,
    ApifyRunner,
)
from prospects import ProspectStore


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class ApifyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProspectStore(str(Path(self.temp_dir.name) / "prospects.sqlite3"))
        self.store.initialize()
        self.calls = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_runner(self, post, get, **overrides):
        settings = {
            "token": "test-token",
            "actor_id": "team~shopify-leads",
            "actor_input": {"search": "skincare"},
            "max_items": 25,
            "max_charge_usd": 2.5,
            "interval_seconds": 3600,
            "run_timeout_seconds": 60,
            "poll_interval_seconds": 1,
            "post": post,
            "get": get,
            "sleep": lambda _seconds: None,
            "monotonic": lambda: 0,
        }
        settings.update(overrides)
        return ApifyRunner(self.store, **settings)

    @staticmethod
    def run_runner(runner):
        async def execute():
            started = await runner.start()
            await runner._task
            return started

        return asyncio.run(execute())

    def test_runs_actor_with_limits_and_imports_results_for_review(self):
        def post(url, **kwargs):
            self.calls.append(("post", url, kwargs))
            return FakeResponse(201, {"data": {"id": "run-1"}})

        run_statuses = iter([
            {"data": {"status": "RUNNING"}},
            {"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}},
        ])

        def get(url, **kwargs):
            self.calls.append(("get", url, kwargs))
            if "/actor-runs/" in url:
                return FakeResponse(200, next(run_statuses))
            return FakeResponse(
                200,
                [{
                    "id": "store-1",
                    "name": "Example Skincare",
                    "websiteUrl": "https://example.com",
                }],
            )

        runner = self.make_runner(post, get)
        started = self.run_runner(runner)
        self.assertTrue(started)

        status = runner.status()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["results"], {
            "inserted": 1, "duplicates": 0, "invalid": 0
        })
        self.assertEqual(self.store.count("needs_review"), 1)
        [prospect] = self.store.list_prospects()
        self.assertEqual(prospect["status"], "needs_review")
        self.assertEqual(self.calls[0][1], f"{APIFY_API_BASE}/acts/team~shopify-leads/runs")
        self.assertEqual(self.calls[0][2]["params"], {
            "maxTotalChargeUsd": 2.5,
            "maxItems": 25,
            "timeout": 60,
        })
        self.assertEqual(self.calls[0][2]["headers"]["Authorization"], "Bearer test-token")
        dataset_call = self.calls[-1]
        self.assertEqual(dataset_call[2]["params"]["limit"], 25)

    def test_repeated_dataset_results_are_deduplicated_by_prospect_store(self):
        records = [{
            "id": "store-1",
            "name": "Example Skincare",
            "websiteUrl": "https://example.com",
        }]
        calls = {"dataset": 0}

        def post(_url, **_kwargs):
            return FakeResponse(201, {"data": {"id": "run-1"}})

        def get(url, **_kwargs):
            if "/actor-runs/" in url:
                return FakeResponse(
                    200,
                    {"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}},
                )
            calls["dataset"] += 1
            return FakeResponse(200, records)

        runner = self.make_runner(post, get)
        self.run_runner(runner)
        self.run_runner(runner)

        self.assertEqual(calls["dataset"], 2)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(runner.status()["results"]["duplicates"], 1)

    def test_failed_actor_run_is_reported_and_never_imported(self):
        def post(_url, **_kwargs):
            return FakeResponse(201, {"data": {"id": "run-1"}})

        def get(_url, **_kwargs):
            return FakeResponse(200, {"data": {"status": "FAILED"}})

        runner = self.make_runner(post, get)
        self.run_runner(runner)

        self.assertEqual(runner.status()["state"], "failed")
        self.assertIn("FAILED", runner.status()["message"])
        self.assertEqual(self.store.count(), 0)

    def test_rejects_dataset_shape_and_http_errors(self):
        def post(_url, **_kwargs):
            return FakeResponse(201, {"data": {"id": "run-1"}})

        def successful_run(url, **_kwargs):
            if "/actor-runs/" in url:
                return FakeResponse(
                    200,
                    {"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}},
                )
            return FakeResponse(200, {"items": []})

        runner = self.make_runner(post, successful_run)
        self.run_runner(runner)
        self.assertEqual(runner.status()["state"], "failed")
        self.assertIn("JSON list", runner.status()["message"])

        failed_runner = self.make_runner(
            lambda _url, **_kwargs: FakeResponse(401, {}),
            successful_run,
        )
        self.run_runner(failed_runner)
        self.assertEqual(failed_runner.status()["state"], "failed")
        self.assertIn("HTTP 401", failed_runner.status()["message"])

    def test_missing_actor_configuration_blocks_run_without_provider_call(self):
        runner = self.make_runner(
            lambda *_args, **_kwargs: self.fail("Actor must not start"),
            lambda *_args, **_kwargs: self.fail("Actor must not be polled"),
            token="",
            actor_id="",
            actor_input=None,
        )
        self.assertFalse(runner.status()["configured"])
        with self.assertRaisesRegex(ValueError, "APIFY_API_TOKEN"):
            asyncio.run(runner.start())

    def test_nonfinite_spend_limit_is_rejected(self):
        runner = self.make_runner(
            lambda *_args, **_kwargs: self.fail("Actor must not start"),
            lambda *_args, **_kwargs: self.fail("Actor must not be polled"),
            max_charge_usd=float("nan"),
        )
        self.assertIn("APIFY_MAX_CHARGE_USD", runner.configuration_error)

    def test_environment_defaults_to_conservative_us_skincare_pilot(self):
        with patch.dict(os.environ, {
            "APIFY_API_TOKEN": "token",
            "APIFY_AUTO_RUN": "false",
        }, clear=True):
            runner = ApifyRunner.from_environment(
                self.store,
                post=lambda *_args, **_kwargs: None,
                get=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(runner.actor_id, DEFAULT_ACTOR_ID)
        self.assertEqual(runner.actor_input, DEFAULT_ACTOR_INPUT)
        self.assertEqual(runner.max_items, 25)
        self.assertFalse(runner.auto_run)
        self.assertFalse(runner.configuration_error)

    def test_interrupted_run_is_persisted_until_owner_confirms_it_was_checked(self):
        def post(_url, **_kwargs):
            return FakeResponse(201, {"data": {"id": "run-1"}})

        def get(_url, **_kwargs):
            raise TimeoutError("status request timed out")

        runner = self.make_runner(post, get)
        self.run_runner(runner)
        self.assertEqual(runner.status()["state"], "interrupted")

        reopened_store = ProspectStore(self.store.db_path)
        reopened_store.initialize()
        recovered = self.make_runner(post, get)
        recovered.prospect_store = reopened_store
        recovered.restore_state()
        self.assertEqual(recovered.status()["state"], "interrupted")
        self.assertFalse(self.run_start(recovered))
        self.assertTrue(recovered.clear_block())
        self.assertEqual(recovered.status()["state"], "idle")

    @staticmethod
    def run_start(runner):
        return asyncio.run(runner.start())


if __name__ == "__main__":
    unittest.main()
