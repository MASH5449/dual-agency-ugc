"""End-to-end and unit tests for the LeadLoop engine (stdlib unittest).

Run from the repository root:

    python3 -m unittest discover -s tests -v

Every test uses a temporary DB and a temporary data directory; nothing touches
the real leads.db or data/inbox.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_module  # noqa: E402
from agents.classifier_agent import classify_lead  # noqa: E402
from db import (  # noqa: E402
    Database,
    normalise_email,
    normalise_website,
)
from main import Pipeline  # noqa: E402


def make_config(tmp: str):
    """Config pointed entirely at a temporary directory."""
    return config_module.Config(
        {
            config_module.ENV_DB_PATH: os.path.join(tmp, "leads.db"),
            config_module.ENV_DATA_DIR: tmp,
            config_module.ENV_DRY_RUN: "true",
            config_module.ENV_LOOP_SECONDS: "1",
        }
    )


class PipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="leadloop-test-")
        self.cfg = make_config(self.tmp)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def write_batch(self, items, name: str = "batch.json") -> None:
        inbox = Path(self.tmp) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(items)
        if name.endswith(".jsonl"):
            payload = "\n".join(json.dumps(i) for i in items)
        (inbox / name).write_text(payload, encoding="utf-8")

    def restore_batch(self, name: str = "batch.json") -> None:
        """Move an archived batch back into the inbox (simulates a re-run)."""
        inbox = Path(self.tmp) / "inbox"
        processed = Path(self.tmp) / "processed" / name
        if processed.exists():
            shutil.copy(processed, inbox / name)

    def run_pass(self) -> dict:
        pipeline = Pipeline(self.cfg)
        pipeline.setup()
        return pipeline.run_pass()

    def db(self) -> Database:
        return Database(self.cfg.db_path)

    def rows(self) -> list[dict]:
        db = self.db()
        with db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM leads ORDER BY id;").fetchall()
            ]

    def sample_items(self) -> list[dict]:
        return [
            {
                "email": "quick@alpha.example.com",
                "website": "https://alpha.example.com",
                "company": "Alpha Corner Shop",
                "employees": 5,
                "revenue": 120000,
                "industry": "Retail",
                "source": "test",
                "lawful_basis": "test fixture",
            },
            {
                "email": "scale@beta.example.com",
                "website": "https://beta.example.com",
                "company": "Beta Brand",
                "employees": 60,
                "revenue": 5000000,
                "industry": "Consumer electronics",
                "source": "test",
                "lawful_basis": "test fixture",
            },
            {
                "email": "wl@gamma.example.com",
                "website": "https://gamma.example.com",
                "company": "Gamma Agency",
                "employees": 30,
                "revenue": 2000000,
                "industry": "Marketing services",
                "source": "test",
                "lawful_basis": "test fixture",
            },
        ]

    # -- pipeline ---------------------------------------------------------
    def test_first_pass_inserts_n(self) -> None:
        items = self.sample_items()
        self.write_batch(items)
        summary = self.run_pass()

        self.assertEqual(summary["ingest"]["inserted"], 3)
        self.assertEqual(summary["ingest"]["skipped"], 0)
        self.assertEqual(summary["classify"]["classified"], 3)
        self.assertEqual(len(self.rows()), 3)

        statuses = {row["status"] for row in self.rows()}
        tiers = {row["tier"] for row in self.rows()}
        # Step 2a: a pass now runs ingest -> classify -> script -> video queue,
        # so every lead finishes the pass in video_queued (dry-run provider).
        self.assertEqual(statuses, {"video_queued"})
        self.assertEqual(tiers, {"quick", "scale", "whitelabel"})

    def test_second_pass_over_identical_input_inserts_zero(self) -> None:
        items = self.sample_items()
        self.write_batch(items)
        self.run_pass()
        before = len(self.rows())

        # Put the same batch back in the inbox and run again.
        self.restore_batch()
        summary = self.run_pass()
        after = len(self.rows())

        self.assertEqual(summary["ingest"]["inserted"], 0)
        self.assertEqual(summary["ingest"]["skipped"], 3)
        self.assertEqual(before, after)
        self.assertEqual(before, 3)

    def test_suppressed_email_is_never_inserted_or_processed(self) -> None:
        db = self.db()
        db.init_schema()
        db.add_suppression("quick@alpha.example.com", reason="opt-out")

        self.write_batch(self.sample_items())
        summary = self.run_pass()

        emails = {row["email"] for row in self.rows()}
        self.assertNotIn("quick@alpha.example.com", emails)
        self.assertEqual(summary["ingest"]["suppressed"], 1)
        self.assertEqual(summary["ingest"]["inserted"], 2)
        # Never classified, never in the 'new' queue.
        self.assertEqual(summary["classify"]["classified"], 2)

    def test_invalid_email_and_website_are_skipped(self) -> None:
        self.write_batch(
            [
                {
                    "email": "not-an-email",
                    "website": "https://bad-email.example.com",
                },
                {
                    "email": "good@valid.example.com",
                    "website": "not a real url",
                },
                {
                    "email": "keep@valid.example.com",
                    "website": "https://keep.example.com",
                },
            ]
        )
        summary = self.run_pass()
        emails = {row["email"] for row in self.rows()}

        self.assertEqual(summary["ingest"]["inserted"], 1)
        self.assertEqual(emails, {"keep@valid.example.com"})

    def test_jsonl_queue_is_ingested(self) -> None:
        (Path(self.tmp) / "queue.jsonl").write_text(
            json.dumps(
                {
                    "email": "queue@delta.example.com",
                    "website": "https://delta.example.com",
                    "company": "Delta Shop",
                    "employees": 4,
                    "revenue": 90000,
                    "industry": "Retail",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        summary = self.run_pass()
        self.assertEqual(summary["ingest"]["inserted"], 1)
        self.assertEqual(self.rows()[0]["tier"], "quick")

    def test_lead_sources_recorded_per_inserted_lead(self) -> None:
        self.write_batch(self.sample_items())
        self.run_pass()
        db = self.db()
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM lead_sources;").fetchone()[0]
            basis = conn.execute(
                "SELECT DISTINCT lawful_basis FROM lead_sources;"
            ).fetchall()
        self.assertEqual(count, 3)
        self.assertEqual([b[0] for b in basis], ["test fixture"])


class NormalisationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # A file-backed temp DB: each connect() is a separate connection, so
        # ":memory:" would give every call its own empty database.
        tmp = tempfile.mkdtemp(prefix="leadloop-norm-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.db = Database(os.path.join(tmp, "leads.db"))
        self.db.init_schema()

    def test_email_normalisation(self) -> None:
        self.assertEqual(normalise_email("  Foo@Bar.COM "), "foo@bar.com")
        self.assertIsNone(normalise_email(""))
        self.assertIsNone(normalise_email(None))

    def test_website_normalisation(self) -> None:
        canonical = "example.com"
        for variant in (
            "HTTPS://WWW.Example.COM/",
            "http://example.com",
            "https://www.example.com/",
            "example.com/",
            "  Example.COM  ",
            "https://www.example.com",
        ):
            self.assertEqual(normalise_website(variant), canonical, variant)

    def test_email_case_dedupe(self) -> None:
        db = self.db
        with db.connect() as conn:
            db.insert_leads(
                [{"email": "dup@example.com", "website": "one.example.com"}], conn
            )
            result = db.insert_leads(
                [{"email": "DUP@Example.COM", "website": "two.example.com"}], conn
            )
            self.assertEqual(result.inserted, 0)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(db.count(conn), 1)

    def test_website_variant_dedupe(self) -> None:
        db = self.db
        with db.connect() as conn:
            db.insert_leads(
                [{"email": "one@example.com", "website": "https://www.dup.example.com/"}],
                conn,
            )
            result = db.insert_leads(
                [{"email": "two@example.com", "website": "http://dup.example.com"}],
                conn,
            )
            self.assertEqual(result.inserted, 0)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(db.count(conn), 1)


class ClassificationTestCase(unittest.TestCase):
    def test_quick_tier(self) -> None:
        result = classify_lead(
            {
                "company": "Northwind Boutique",
                "employees": 6,
                "revenue": 250000,
                "industry": "Retail",
            }
        )
        self.assertEqual(result.tier, "quick")

    def test_scale_tier(self) -> None:
        result = classify_lead(
            {
                "company": "ScaleBrand",
                "employees": 85,
                "revenue": 6500000,
                "industry": "Consumer electronics",
            }
        )
        self.assertEqual(result.tier, "scale")

    def test_whitelabel_tier_by_size(self) -> None:
        result = classify_lead(
            {
                "company": "MegaCorp Holdings",
                "employees": 4000,
                "revenue": 90000000,
                "industry": "Consumer goods",
            }
        )
        self.assertEqual(result.tier, "whitelabel")

    def test_whitelabel_tier_by_agency_keyword(self) -> None:
        result = classify_lead(
            {
                "company": "Lumen Agency",
                "employees": 10,
                "revenue": 300000,
                "industry": "Marketing",
            }
        )
        self.assertEqual(result.tier, "whitelabel")

    def test_explicit_tier_override_wins(self) -> None:
        result = classify_lead(
            {
                "company": "Tiny Co",
                "employees": 2,
                "revenue": 1000,
                "industry": "Retail",
                "tier": "whitelabel",
            }
        )
        self.assertEqual(result.tier, "whitelabel")
        self.assertIn("explicit", result.reason)

    def test_unknown_explicit_tier_is_rejected(self) -> None:
        result = classify_lead({"company": "X", "tier": "enterprise"})
        self.assertEqual(result.tier, "")

    def test_known_tier_values_only(self) -> None:
        from db import TIERS

        for item in (
            {"company": "A", "employees": 1, "revenue": 1, "industry": "Retail"},
            {"company": "B", "employees": 100, "revenue": 1000000, "industry": "Retail"},
            {"company": "C", "employees": 500, "revenue": 1, "industry": "Retail"},
            {"company": "D"},
        ):
            self.assertIn(classify_lead(item).tier, TIERS)


class ConfigTestCase(unittest.TestCase):
    def test_defaults_and_describe_never_leaks_values(self) -> None:
        cfg = config_module.Config({})
        self.assertEqual(cfg.db_path, "./leads.db")
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.loop_seconds, 300)

        cfg = config_module.Config(
            {
                config_module.ENV_INSTANTLY_API_KEY: "super-secret-value",
                config_module.ENV_DRY_RUN: "false",
            }
        )
        described = cfg.describe()
        self.assertEqual(described[config_module.ENV_INSTANTLY_API_KEY], "set")
        self.assertFalse(cfg.dry_run)
        self.assertNotIn("super-secret-value", json.dumps(described))
        self.assertNotIn("super-secret-value", cfg.summary())


if __name__ == "__main__":
    unittest.main()
