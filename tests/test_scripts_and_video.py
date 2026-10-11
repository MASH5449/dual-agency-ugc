"""Step-2a tests: scripting, video provider, resilience, migration, CSV ingest.

Stdlib unittest. **No network anywhere** — the HeyGen provider is exercised only
through its refusal paths (which return before any socket is touched) and every
other provider is a local fake or the dry-run one. Tests set the throttle delay
to 0 and inject recording sleep functions, so nothing waits.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_module  # noqa: E402
from agents.script_agent import ScriptAgent, build_script  # noqa: E402
from agents.util import (  # noqa: E402
    PermanentError,
    Throttle,
    TransientError,
    backoff_delay,
    is_transient,
    retry,
    throttle,
    throttle_async,
)
from agents.video_agent import VideoQueueAgent  # noqa: E402
from agents.video_provider import (  # noqa: E402
    DRYRUN_HOST,
    HEYGEN_FALLBACK_VOICE_ID,
    DryRunVideoProvider,
    HeyGenVideoProvider,
    VideoRequest,
    build_provider,
)
from db import (  # noqa: E402
    STATUS_CLASSIFIED,
    STATUS_SCRIPTED,
    STATUS_VIDEO_QUEUED,
    Database,
)
from main import Pipeline  # noqa: E402

#: Step-1 schema, verbatim: the OLD shape with no updated_at column.
OLD_SCHEMA = """
CREATE TABLE leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    website TEXT UNIQUE,
    tier TEXT,
    video_id TEXT UNIQUE,
    video_url TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL
);
"""


class NoSleep:
    """Records requested delays instead of waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.calls)


def make_config(tmp: str, **overrides) -> config_module.Config:
    env = {
        config_module.ENV_DB_PATH: os.path.join(tmp, "leads.db"),
        config_module.ENV_DATA_DIR: tmp,
        config_module.ENV_DRY_RUN: "true",
        config_module.ENV_LOOP_SECONDS: "1",
        config_module.ENV_THROTTLE_DELAY: "0",
    }
    env.update(overrides)
    return config_module.Config(env)


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="leadloop-s2-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = make_config(self.tmp)
        self.db = Database(self.cfg.db_path)
        self.db.init_schema()

    # -- helpers ----------------------------------------------------------
    def rows(self) -> list[dict]:
        with self.db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM leads ORDER BY id;").fetchall()
            ]

    def seed(self, items) -> None:
        self.db.insert_leads(items)

    def lead(self, **overrides) -> dict:
        item = {
            "email": "quick@alpha.example.com",
            "website": "https://alpha.example.com",
            "company": "Alpha Corner Shop",
            "employees": 5,
            "revenue": 120000,
            "industry": "Retail",
            "tier": "quick",
        }
        item.update(overrides)
        return item

    def classify_and_script(self, items) -> None:
        """Insert, mark classified (as the classifier would), then script."""
        self.seed(items)
        with self.db.connect() as conn:
            for lead in self.rows():
                conn.execute(
                    "UPDATE leads SET status = ?, tier = ? WHERE id = ?;",
                    (STATUS_CLASSIFIED, lead["tier"], lead["id"]),
                )
            conn.commit()
        ScriptAgent(self.cfg, self.db).run_once()


class MigrationTestCase(unittest.TestCase):
    """`updated_at` arrives on a database built with the step-1 schema."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="leadloop-mig-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "leads.db")
        with sqlite3.connect(self.path) as conn:
            conn.execute(OLD_SCHEMA)
            conn.executemany(
                "INSERT INTO leads (email, website, tier, status, created_at) "
                "VALUES (?, ?, ?, ?, ?);",
                [
                    ("old1@example.com", "old1.example.com", "quick", "classified",
                     "2024-01-01T00:00:00+00:00"),
                    ("old2@example.com", "old2.example.com", "scale", "new",
                     "2024-02-02T00:00:00+00:00"),
                ],
            )
            conn.commit()

    def columns(self) -> list[str]:
        with sqlite3.connect(self.path) as conn:
            return [r[1] for r in conn.execute("PRAGMA table_info(leads);")]

    def test_migration_adds_backfills_and_is_idempotent(self) -> None:
        before = self.columns()
        self.assertNotIn("updated_at", before)

        db = Database(self.path)
        added = db.migrate_schema()
        self.assertEqual(added, ["updated_at"])

        after = self.columns()
        self.assertEqual(after[: len(before)], before, "existing columns reordered")
        self.assertEqual(after[-1], "updated_at")

        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY id;")]
        self.assertEqual(len(rows), 2, "existing rows lost")
        self.assertEqual(rows[0]["email"], "old1@example.com")
        self.assertEqual(rows[0]["updated_at"], rows[0]["created_at"])
        self.assertEqual(rows[1]["updated_at"], "2024-02-02T00:00:00+00:00")

        # Second run: no error, no duplicate column, no data change.
        self.assertEqual(db.migrate_schema(), [])
        with db.connect() as conn:
            again = [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY id;")]
        self.assertEqual(again, rows)

    def test_engine_startup_migrates_and_leaves_rows_intact(self) -> None:
        cfg = make_config(self.tmp)
        pipeline = Pipeline(cfg)
        pipeline.setup()          # init_schema() runs the migration
        summary = pipeline.run_pass()
        self.assertIn("script", summary)
        with Database(cfg.db_path).connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM leads;")]
        self.assertEqual({r["email"] for r in rows}, {"old1@example.com", "old2@example.com"})
        self.assertTrue(all(r["updated_at"] for r in rows))


class UpdatedAtTestCase(BaseCase):
    def test_update_lead_refreshes_updated_at_and_keeps_row(self) -> None:
        self.seed([self.lead(created_at="2000-01-01T00:00:00+00:00")])
        row = self.rows()[0]
        self.assertEqual(row["updated_at"], "2000-01-01T00:00:00+00:00")

        ok = self.db.update_lead(row["id"], status=STATUS_SCRIPTED, tier="scale")
        self.assertTrue(ok)
        updated = self.rows()[0]
        self.assertEqual(updated["status"], STATUS_SCRIPTED)
        self.assertEqual(updated["tier"], "scale")
        self.assertNotEqual(updated["updated_at"], "2000-01-01T00:00:00+00:00")
        self.assertEqual(updated["created_at"], "2000-01-01T00:00:00+00:00")

    def test_update_lead_rejects_unknown_field(self) -> None:
        self.seed([self.lead()])
        with self.assertRaises(ValueError):
            self.db.update_lead(self.rows()[0]["id"], nope="x")


class ScriptAgentTestCase(BaseCase):
    def test_script_per_tier_persisted_and_status_moved(self) -> None:
        items = [
            self.lead(email="q@a.example.com", website="https://a.example.com",
                      company="A Shop", tier="quick", industry="Retail"),
            self.lead(email="s@b.example.com", website="https://b.example.com",
                      company="B Brand", tier="scale", industry="Consumer electronics"),
            self.lead(email="w@c.example.com", website="https://c.example.com",
                      company="C Agency", tier="whitelabel", industry="Marketing services"),
        ]
        self.classify_and_script(items)

        scripts_dir = Path(self.tmp) / "scripts"
        files = sorted(p.name for p in scripts_dir.glob("*.json"))
        self.assertEqual(files, ["1.json", "2.json", "3.json"])

        for row in self.rows():
            self.assertEqual(row["status"], STATUS_SCRIPTED)
            document = json.loads((scripts_dir / f"{row['id']}.json").read_text())
            self.assertEqual(document["lead_id"], row["id"])
            self.assertEqual(document["email"], row["email"])
            self.assertEqual(document["tier"], row["tier"])
            self.assertEqual(document["tier"], build_script(
                lead_id=row["id"], email=row["email"], tier=row["tier"],
                company="x", website=row["website"], industry="y",
            ).tier)
            self.assertIn(document["tier"], ("quick", "scale", "whitelabel"))
            self.assertTrue(document["script"].strip())
            self.assertGreaterEqual(document["target_duration_seconds"], 15)
            self.assertTrue(document["created_at"])

        # Tier shape: quick is the short one, whitelabel the long one.
        durations = {
            json.loads((scripts_dir / f"{r['id']}.json").read_text())["tier"]:
            json.loads((scripts_dir / f"{r['id']}.json").read_text())["target_duration_seconds"]
            for r in self.rows()
        }
        self.assertLess(durations["quick"], durations["scale"])
        self.assertLess(durations["scale"], durations["whitelabel"])

    def test_explicit_tier_override_is_scripted_as_that_tier(self) -> None:
        self.classify_and_script(
            [self.lead(email="o@d.example.com", website="https://d.example.com",
                       company="D Tiny", tier="whitelabel", employees=2, revenue=1000)]
        )
        document = json.loads((Path(self.tmp) / "scripts" / "1.json").read_text())
        self.assertEqual(document["tier"], "whitelabel")
        self.assertIn("white-label", document["script"])

    def test_pricing_is_omitted_unless_configured(self) -> None:
        items = [self.lead(email="p@e.example.com", website="https://e.example.com")]
        self.classify_and_script(items)
        text = json.loads((Path(self.tmp) / "scripts" / "1.json").read_text())["script"]
        for token in ("price", "Price", "pricing", "Pricing", "$", "€", "£"):
            self.assertNotIn(token, text)

    def test_pricing_appears_when_the_env_var_is_set(self) -> None:
        cfg = make_config(self.tmp, **{config_module.ENV_TIER_PRICE_QUICK: "499 EUR"})
        self.cfg = cfg
        self.classify_and_script(
            [self.lead(email="pr@f.example.com", website="https://f.example.com")]
        )
        text = json.loads((Path(self.tmp) / "scripts" / "1.json").read_text())["script"]
        self.assertIn("499 EUR", text)

    def test_script_never_invents_a_result_or_deadline(self) -> None:
        for tier in ("quick", "scale", "whitelabel"):
            script = build_script(
                lead_id=1, email="x@y.example.com", tier=tier,
                company="Y Co", website="y.example.com", industry="Retail",
            )
            lowered = script.text.lower()
            for banned in ("guarantee", "100%", "last chance", "final notice",
                           "expires", "spots left", "clients like"):
                self.assertNotIn(banned, lowered, f"{tier}: {banned}")


class FakeProvider:
    """Local provider stub: no network, deterministic ids."""

    name = "fake"
    live = False

    def __init__(self, video_id: str = "fake-1") -> None:
        self.video_id = video_id
        self.calls: list[VideoRequest] = []

    def describe(self) -> str:
        return "fake (live=false)"

    def queue_video(self, request: VideoRequest):
        from agents.video_provider import VideoResult

        self.calls.append(request)
        return VideoResult(video_id=self.video_id, provider=self.name,
                           video_url=f"https://{DRYRUN_HOST}/v/{self.video_id}")


class VideoQueueTestCase(BaseCase):
    def test_dry_run_provider_queues_unique_ids(self) -> None:
        self.classify_and_script([
            self.lead(email="v1@a.example.com", website="https://a.example.com"),
            self.lead(email="v2@b.example.com", website="https://b.example.com",
                      company="B", tier="scale", employees=80, revenue=5000000),
        ])
        agent = VideoQueueAgent(self.cfg, self.db, throttle_delay=0)
        totals = agent.run_once()
        self.assertEqual(totals["queued"], 2)

        rows = self.rows()
        ids = [r["video_id"] for r in rows]
        self.assertEqual(len(set(ids)), 2, "video ids must be unique")
        for row in rows:
            self.assertTrue(row["video_id"].startswith("dryrun-"), row["video_id"])
            self.assertEqual(row["status"], STATUS_VIDEO_QUEUED)
            self.assertIn(DRYRUN_HOST, row["video_url"])
            self.assertTrue(row["updated_at"])

    def test_colliding_video_id_leaves_the_other_row_intact(self) -> None:
        self.classify_and_script([
            self.lead(email="c1@a.example.com", website="https://a.example.com"),
            self.lead(email="c2@b.example.com", website="https://b.example.com",
                      company="B", tier="scale", employees=80, revenue=5000000),
        ])
        agent = VideoQueueAgent(self.cfg, self.db, provider=FakeProvider("dup-1"),
                                throttle_delay=0)
        totals = agent.run_once()

        rows = self.rows()
        first, second = rows[0], rows[1]
        self.assertEqual(totals["queued"], 1)
        self.assertEqual(totals["duplicate"], 1)
        # The row that owns the id is untouched...
        self.assertEqual(first["video_id"], "dup-1")
        self.assertEqual(first["status"], STATUS_VIDEO_QUEUED)
        self.assertEqual(first["video_url"], f"https://{DRYRUN_HOST}/v/dup-1")
        # ...and the second is failed, not silently rewritten.
        self.assertIsNone(second["video_id"])
        self.assertEqual(second["status"], "failed")
        self.assertEqual(self.db.count(), 2)

    def test_missing_script_fails_the_row(self) -> None:
        self.classify_and_script([self.lead()])
        (Path(self.tmp) / "scripts" / "1.json").unlink()
        totals = VideoQueueAgent(self.cfg, self.db, throttle_delay=0).run_once()
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_provider_failure_fails_the_row_without_crashing(self) -> None:
        class Boom:
            name = "boom"
            live = True

            def describe(self) -> str:
                return "boom"

            def queue_video(self, request):
                raise TransientError("socket exploded")

        self.classify_and_script([self.lead()])
        totals = VideoQueueAgent(self.cfg, self.db, provider=Boom(),
                                 throttle_delay=0).run_once()
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(self.rows()[0]["status"], "failed")


class ProviderSelectionTestCase(BaseCase):
    def test_default_is_dry_run(self) -> None:
        provider = build_provider(self.cfg)
        self.assertIsInstance(provider, DryRunVideoProvider)
        self.assertFalse(provider.live)

    def test_heygen_selected_by_config(self) -> None:
        cfg = make_config(self.tmp, **{config_module.ENV_VIDEO_PROVIDER: "heygen"})
        self.assertIsInstance(build_provider(cfg), HeyGenVideoProvider)

    def test_unknown_provider_is_not_silently_dry_run(self) -> None:
        cfg = make_config(self.tmp, **{config_module.ENV_VIDEO_PROVIDER: "veo"})
        with self.assertRaises(PermanentError):
            build_provider(cfg)

    def test_live_provider_refuses_in_dry_run(self) -> None:
        def explode(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("network call attempted")

        provider = HeyGenVideoProvider(self.cfg, api_key="k", dry_run=True,
                                       opener=explode)
        self.assertIn("DRY_RUN", provider.refusal_reason())
        with self.assertRaises(PermanentError):
            provider.queue_video(VideoRequest(1, "a@b.example.com", "quick", "hi"))

    def test_live_provider_refuses_without_key(self) -> None:
        def explode(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("network call attempted")

        provider = HeyGenVideoProvider(self.cfg, api_key="", dry_run=False,
                                       opener=explode)
        self.assertIn("HEYGEN_API_KEY", provider.refusal_reason())
        with self.assertRaises(PermanentError):
            provider.queue_video(VideoRequest(1, "a@b.example.com", "quick", "hi"))

    def test_mock_mode_refuses_even_when_dry_run_is_false(self) -> None:
        provider = HeyGenVideoProvider(self.cfg, api_key="k", dry_run=False,
                                       mock_mode=True, opener=lambda *a, **k: None)
        self.assertIn("MOCK_MODE", provider.refusal_reason())

    def test_refusal_reason_is_a_permanent_error_not_retried(self) -> None:
        provider = HeyGenVideoProvider(self.cfg, api_key="", dry_run=False)
        calls = {"n": 0}

        def run():
            calls["n"] += 1
            return provider.queue_video(VideoRequest(1, "a@b", "quick", "hi"))

        with self.assertRaises(PermanentError):
            retry(run, attempts=3, sleep=lambda s: None)
        self.assertEqual(calls["n"], 1, "a config problem must not be retried")


class _FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class HeyGenAvatarFallbackTestCase(BaseCase):
    """Avatar fallback on 404 avatar_not_found. Fake opener, no network."""

    LOOKS = {"data": [
        {"id": "look-1", "default_voice_id": "voice-1"},
        {"id": "look-2", "default_voice_id": "voice-2"},
        {"id": "look-3"},
    ]}
    NOT_FOUND = {"error": {"code": "avatar_not_found", "message": "Avatar not found"}}

    def make_provider(self, video_outcomes: list):
        """``video_outcomes``: per /v3/videos call, a dict (success) or an
        (http_status, body_dict) tuple (HTTPError)."""
        self.video_calls: list[dict] = []
        outcomes = list(video_outcomes)

        def opener(request, timeout=None):
            url = request.full_url
            if "/v3/avatars/looks" in url:
                self.looks_url = url
                return _FakeResponse(self.LOOKS)
            payload = json.loads(request.data.decode("utf-8"))
            self.video_calls.append(payload)
            outcome = outcomes.pop(0)
            if isinstance(outcome, tuple):
                status, body = outcome
                raise urllib.error.HTTPError(
                    url, status, "err", {}, io.BytesIO(json.dumps(body).encode("utf-8"))
                )
            return _FakeResponse(outcome)

        self.logs: list[str] = []
        return HeyGenVideoProvider(
            self.cfg, api_key="secret-key", dry_run=False, mock_mode=False,
            opener=opener, sleep=NoSleep(), log=self.logs.append,
            throttle_delay=0,
        )

    def request(self) -> VideoRequest:
        return VideoRequest(1, "a@b.example.com", "quick", "hello")

    def test_lookup_returns_multiple_candidates_with_fallback_voice(self) -> None:
        provider = self.make_provider([])
        candidates = provider.lookup_avatar()
        self.assertEqual(candidates, [
            ("look-1", "voice-1"),
            ("look-2", "voice-2"),
            ("look-3", HEYGEN_FALLBACK_VOICE_ID),
        ])
        self.assertIn("limit=10", self.looks_url)

    def test_first_404_avatar_not_found_then_second_succeeds(self) -> None:
        provider = self.make_provider(
            [(404, self.NOT_FOUND), {"data": {"video_id": "vid-2"}}]
        )
        result = provider.queue_video(self.request())
        self.assertEqual(result.video_id, "vid-2")
        self.assertEqual([c["avatar_id"] for c in self.video_calls], ["look-1", "look-2"])
        self.assertEqual(self.video_calls[1]["voice_id"], "voice-2")
        joined = "\n".join(self.logs)
        self.assertIn("look-1", joined)
        self.assertIn("used avatar_id look-2", joined)
        self.assertNotIn("secret-key", joined)

    def test_all_candidates_not_found_raises_permanent_with_attempts(self) -> None:
        provider = self.make_provider([(404, self.NOT_FOUND)] * 3)
        with self.assertRaises(PermanentError) as ctx:
            provider.queue_video(self.request())
        message = str(ctx.exception)
        for avatar_id in ("look-1", "look-2", "look-3"):
            self.assertIn(avatar_id, message)
        self.assertEqual(len(self.video_calls), 3)

    def test_other_4xx_error_is_not_a_fallback(self) -> None:
        provider = self.make_provider([(400, {"error": {"code": "invalid_script"}})])
        with self.assertRaises(PermanentError) as ctx:
            provider.queue_video(self.request())
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertEqual(len(self.video_calls), 1)

    def test_404_with_other_code_is_not_a_fallback(self) -> None:
        provider = self.make_provider([(404, {"error": {"code": "voice_not_found"}})])
        with self.assertRaises(PermanentError):
            provider.queue_video(self.request())
        self.assertEqual(len(self.video_calls), 1)

    def test_transient_error_is_still_retried_on_same_avatar(self) -> None:
        provider = self.make_provider(
            [(503, {"error": {"code": "unavailable"}}), {"data": {"video_id": "vid-1"}}]
        )
        result = provider.queue_video(self.request())
        self.assertEqual(result.video_id, "vid-1")
        self.assertEqual([c["avatar_id"] for c in self.video_calls], ["look-1", "look-1"])


class ResilienceTestCase(unittest.TestCase):
    def test_backoff_is_exponential_with_jitter_disabled(self) -> None:
        delays = [backoff_delay(a, jitter=0.0) for a in (1, 2, 3)]
        self.assertEqual(delays, [1.0, 2.0, 4.0])

    def test_retry_succeeds_after_transient_failures(self) -> None:
        sleep = NoSleep()
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TransientError("timeout")
            return "ok"

        result = retry(flaky, attempts=3, sleep=sleep, jitter=0.0, log=lambda m: None)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(sleep.calls, [1.0, 2.0])

    def test_retry_gives_up_after_three_attempts(self) -> None:
        sleep = NoSleep()
        logged: list[str] = []
        attempts = {"n": 0}

        def always():
            attempts["n"] += 1
            raise TransientError("503")

        with self.assertRaises(TransientError):
            retry(always, attempts=3, sleep=sleep, jitter=0.0, log=logged.append)
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(sleep.calls), 2)
        self.assertTrue(any("giving up after 3 attempts" in line for line in logged))

    def test_plain_400_is_never_retried(self) -> None:
        sleep = NoSleep()
        attempts = {"n": 0}

        def bad():
            attempts["n"] += 1
            raise urllib.error.HTTPError("https://api.example.com", 400, "Bad Request",
                                         {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            retry(bad, attempts=3, sleep=sleep, log=lambda m: None)
        self.assertEqual(attempts["n"], 1)
        self.assertEqual(sleep.calls, [])

    def test_transient_classification(self) -> None:
        self.assertTrue(is_transient(TransientError("boom")))
        self.assertTrue(is_transient(TimeoutError("slow")))
        self.assertFalse(is_transient(PermanentError("400")))
        self.assertFalse(is_transient(urllib.error.HTTPError("u", 404, "nf", {}, None)))
        self.assertTrue(is_transient(urllib.error.HTTPError("u", 429, "slow down", {}, None)))
        self.assertTrue(is_transient(urllib.error.HTTPError("u", 503, "down", {}, None)))

    # -- throttle ---------------------------------------------------------
    def test_throttle_skipped_in_dry_run_and_at_zero_delay(self) -> None:
        sleep = NoSleep()
        self.assertEqual(throttle(dry_run=True, delay=0, sleep=sleep), 0.0)
        self.assertEqual(throttle(dry_run=False, delay=0, sleep=sleep), 0.0)
        self.assertEqual(throttle(dry_run=False, sleep=sleep, min_seconds=0,
                                  max_seconds=0), 0.0)
        self.assertEqual(sleep.calls, [], "no sleeping when the delay is zero")

    def test_throttle_uses_the_configured_window_when_live(self) -> None:
        sleep = NoSleep()
        slept = throttle(dry_run=False, min_seconds=2, max_seconds=5, sleep=sleep)
        self.assertEqual(sleep.calls, [slept])
        self.assertGreaterEqual(slept, 2.0)
        self.assertLessEqual(slept, 5.0)

    def test_throttle_async_skips_in_dry_run(self) -> None:
        slept = []
        import asyncio

        async def fake_sleep(seconds):
            slept.append(seconds)

        got = asyncio.run(throttle_async(dry_run=True, delay=0, sleep=fake_sleep))
        self.assertEqual(got, 0.0)
        self.assertEqual(slept, [])

    def test_stateful_throttle_never_delays_the_first_call(self) -> None:
        sleep = NoSleep()
        gate = Throttle(min_seconds=2, max_seconds=5, dry_run=False, sleep=sleep)
        asyncio_run = None
        import asyncio

        asyncio_run = asyncio.run

        async def two_calls():
            await gate.between_calls()
            await gate.between_calls()
            return gate.seconds_slept

        slept = asyncio_run(two_calls())
        self.assertEqual(len(sleep.calls), 1, "only the second call waits")
        self.assertGreaterEqual(slept, 2.0)

    def test_dry_run_throttle_object_is_inactive(self) -> None:
        gate = Throttle(dry_run=True, sleep=NoSleep())
        self.assertFalse(gate.active)
        self.assertEqual(gate.wait(), 0.0)


class CsvIngestTestCase(BaseCase):
    def write_csv(self, name: str, text: str) -> None:
        inbox = Path(self.tmp) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / name).write_text(text, encoding="utf-8")

    def test_csv_rows_are_inserted_with_default_lawful_basis(self) -> None:
        self.write_csv(
            "owner_list.csv",
            "Email,Domain,Company,Employees,Revenue,Industry\n"
            "Owner.One@Example.com,https://one.example.com,One Ltd,6,250000,Retail\n"
            "two@example.com,two.example.com,Two Ltd,80,5000000,Consumer electronics\n"
            "bad-row,three.example.com,Three Ltd,10,1000000,Retail\n",
        )
        pipeline = Pipeline(self.cfg)
        pipeline.setup()
        summary = pipeline.run_pass()

        self.assertEqual(summary["ingest"]["inserted"], 2)
        emails = {row["email"] for row in self.rows()}
        self.assertEqual(emails, {"owner.one@example.com", "two@example.com"})

        with self.db.connect() as conn:
            bases = {
                r[0] for r in conn.execute("SELECT DISTINCT lawful_basis FROM lead_sources;")
            }
            firm = dict(
                conn.execute(
                    "SELECT company, employees, industry FROM lead_firmographics WHERE lead_id = 1;"
                ).fetchone()
            )
        self.assertEqual(bases, {"owned opted-in list"})
        self.assertEqual(firm["company"], "One Ltd")
        self.assertEqual(firm["employees"], 6.0)
        self.assertEqual(firm["industry"], "Retail")

        # Archived, so it cannot be ingested twice.
        self.assertTrue((Path(self.tmp) / "processed" / "owner_list.csv").exists())

    def test_csv_default_lawful_basis_from_env(self) -> None:
        cfg = make_config(self.tmp, **{config_module.ENV_DEFAULT_LAWFUL_BASIS: "webinar opt-in"})
        self.cfg = cfg
        self.write_csv("list.csv", "email,website\nx@y.example.com,y.example.com\n")
        pipeline = Pipeline(cfg)
        pipeline.setup()
        pipeline.run_pass()
        with self.db.connect() as conn:
            basis = conn.execute("SELECT lawful_basis FROM lead_sources;").fetchone()[0]
        self.assertEqual(basis, "webinar opt-in")

    def test_csv_duplicate_email_in_a_different_case_is_skipped(self) -> None:
        self.write_csv("first.csv", "email,website\ndup@example.com,dup.example.com\n")
        pipeline = Pipeline(self.cfg)
        pipeline.setup()
        pipeline.run_pass()
        count_after_first = self.db.count()

        self.write_csv("second.csv", "Email,Website\nDUP@Example.COM,other.example.com\n")
        summary = pipeline.run_pass()

        self.assertEqual(summary["ingest"]["inserted"], 0)
        self.assertEqual(summary["ingest"]["skipped"], 1)
        self.assertEqual(self.db.count(), count_after_first)
        self.assertEqual(self.rows()[0]["website"], "dup.example.com")

    def test_csv_without_email_column_is_skipped_entirely(self) -> None:
        self.write_csv("nope.csv", "name,website\nSomeone,nope.example.com\n")
        summary = Pipeline(self.cfg).run_pass() if False else None  # noqa: F841
        pipeline = Pipeline(self.cfg)
        pipeline.setup()
        summary = pipeline.run_pass()
        self.assertEqual(summary["ingest"]["inserted"], 0)
        self.assertEqual(self.db.count(), 0)


class FullPipelineRegressionTestCase(BaseCase):
    def sample(self) -> list[dict]:
        return [
            {"email": "one@a.example.com", "website": "https://a.example.com",
             "company": "A", "employees": 5, "revenue": 120000, "industry": "Retail",
             "source": "test", "lawful_basis": "test fixture"},
            {"email": "two@b.example.com", "website": "https://b.example.com",
             "company": "B", "employees": 80, "revenue": 5000000,
             "industry": "Consumer electronics", "source": "test",
             "lawful_basis": "test fixture"},
            {"email": "three@c.example.com", "website": "https://c.example.com",
             "company": "C Agency", "employees": 30, "revenue": 2000000,
             "industry": "Marketing services", "source": "test",
             "lawful_basis": "test fixture"},
        ]

    def test_second_pass_over_identical_input_inserts_zero_and_requeues_nothing(self) -> None:
        inbox = Path(self.tmp) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "batch.json").write_text(json.dumps(self.sample()), encoding="utf-8")

        pipeline = Pipeline(self.cfg)
        pipeline.setup()
        first = pipeline.run_pass()
        self.assertEqual(first["ingest"]["inserted"], 3)
        self.assertEqual(first["script"]["scripted"], 3)
        self.assertEqual(first["video"]["queued"], 3)
        before = self.db.count()

        shutil.copy(Path(self.tmp) / "processed" / "batch.json", inbox / "batch.json")
        second = pipeline.run_pass()

        self.assertEqual(second["ingest"]["inserted"], 0)
        self.assertEqual(second["ingest"]["skipped"], 3)
        self.assertEqual(second["script"]["scripted"], 0)
        self.assertEqual(second["video"]["queued"], 0)
        self.assertEqual(self.db.count(), before)
        self.assertTrue(all(r["status"] == STATUS_VIDEO_QUEUED for r in self.rows()))
        self.assertEqual(len({r["video_id"] for r in self.rows()}), 3)


if __name__ == "__main__":
    unittest.main()
