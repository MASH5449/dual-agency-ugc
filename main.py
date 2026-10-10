#!/usr/bin/env python3
"""LeadLoop — autonomous background lead engine (step 2a).

Stdlib only: asyncio, argparse, sqlite3, logging, signal. No network calls in
dry-run, which is the default.

Pipeline (one pass):

    [Agent-Scraper]    ingest data/inbox/*.json + *.csv + data/queue.jsonl -> leads
    [Agent-Classifier] assign tier (quick/scale/whitelabel), new -> classified
    [Agent-Script]     per-tier script -> data/scripts/<lead_id>.json, -> scripted
    [Agent-Video]      queue a render via the configured provider, -> video_queued

Dispatch (Instantly routing + the HeyGen callback) is a later delegation.

Usage:
    python3 main.py --once      one full pipeline pass, then exit 0
    python3 main.py --loop      recurring background run (this is the default)
    python3 main.py --status    report DB row counts and env var state
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

import config as config_module
from agents.base import configure_logging
from agents.classifier_agent import ClassifierAgent
from agents.scraper_agent import ScraperAgent
from agents.script_agent import ScriptAgent
from agents.video_agent import VideoQueueAgent
from agents.video_provider import describe_provider
from db import Database

#: Log tag for the pipeline driver. Doubles as the logger name, so every line
#: is tagged exactly once by the log format.
DRIVER_TAG = "[LeadLoop]"
LOG = logging.getLogger(DRIVER_TAG)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """Runs the step-1 agents in order over a single pass.

    Stage failures are caught and logged; the loop keeps going. This is the
    difference between a background service that survives a bad batch and one
    that dies on the first malformed row.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.db = Database(cfg.db_path)
        self.passes = 0

    def setup(self) -> None:
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.processed_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.db.init_schema()
        LOG.info("schema ready at %s", self.cfg.db_path)

    # -- one pass ---------------------------------------------------------
    def run_pass(self) -> dict[str, Any]:
        """Run one full pipeline pass; never raises."""
        self.passes += 1
        summary: dict[str, Any] = {"pass": self.passes}
        LOG.info("pass %d starting", self.passes)

        # Stage 1 — ingest.
        try:
            scraper = ScraperAgent(self.cfg, self.db)
            summary["ingest"] = scraper.ingest()
        except Exception as exc:  # noqa: BLE001 - stage isolation is deliberate
            LOG.error(
                "scraper stage failed (%s: %s) - continuing", type(exc).__name__, exc
            )
            summary["ingest"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Stage 2 — tiering.
        try:
            classifier = ClassifierAgent(self.cfg, self.db)
            summary["classify"] = classifier.run_once()
        except Exception as exc:  # noqa: BLE001
            LOG.error(
                "classifier stage failed (%s: %s) - continuing",
                type(exc).__name__, exc,
            )
            summary["classify"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Stage 3 — script generation (persists one JSON per lead).
        try:
            script_agent = ScriptAgent(self.cfg, self.db)
            summary["script"] = script_agent.run_once()
        except Exception as exc:  # noqa: BLE001
            LOG.error(
                "script stage failed (%s: %s) - continuing",
                type(exc).__name__, exc,
            )
            summary["script"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Stage 4 — queue a render per scripted lead through the provider.
        try:
            video_agent = VideoQueueAgent(self.cfg, self.db)
            summary["video"] = video_agent.run_once()
        except Exception as exc:  # noqa: BLE001
            LOG.error(
                "video stage failed (%s: %s) - continuing",
                type(exc).__name__, exc,
            )
            summary["video"] = {"error": f"{type(exc).__name__}: {exc}"}

        summary["leads_total"] = self.db.count()
        LOG.info(
            "pass %d done: %d lead(s) in DB, dry_run=%s",
            self.passes, summary["leads_total"], str(self.cfg.dry_run).lower(),
        )
        return summary

    # -- stats ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        stats = self.db.stats()
        stats["env"] = self.cfg.describe()
        return stats


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def log_startup(cfg) -> None:
    LOG.info("starting: %s", cfg.summary())
    for name, state in cfg.describe().items():
        LOG.info("env %s: %s", name, state)
    # Which video provider is in play, and whether it may run. Never a key value.
    LOG.info("video provider: %s", describe_provider(cfg))
    if cfg.dry_run:
        LOG.info("DRY RUN - no outbound will be sent")
    else:
        LOG.warning("DRY_RUN is disabled: live outbound is enabled for step 2")


def run_once(cfg) -> int:
    pipeline = Pipeline(cfg)
    pipeline.setup()
    log_startup(cfg)
    pipeline.run_pass()
    LOG.info("--once complete")
    return 0


async def run_loop(cfg) -> int:
    """Recurring background loop. Returns on SIGINT/SIGTERM."""
    pipeline = Pipeline(cfg)
    pipeline.setup()
    log_startup(cfg)
    LOG.info("loop mode: every %ds (Ctrl-C to stop)", cfg.loop_seconds)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover
            signal.signal(sig, lambda *_: stop.set())

    while not stop.is_set():
        try:
            # to_thread keeps the (blocking) sqlite work off the event loop.
            await asyncio.to_thread(pipeline.run_pass)
        except Exception as exc:  # noqa: BLE001 - the loop must never die
            LOG.error("pass failed (%s: %s) - continuing", type(exc).__name__, exc)

        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.loop_seconds)
        except asyncio.TimeoutError:
            continue

    LOG.info("shutdown signal received, stopping cleanly")
    return 0


def print_status(cfg) -> int:
    pipeline = Pipeline(cfg)
    try:
        stats = pipeline.status()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read {cfg.db_path}: {type(exc).__name__}: {exc}")
        stats = {
            "total": {"total": 0},
            "by_status": {},
            "by_tier": {},
            "env": cfg.describe(),
        }

    print(f"LeadLoop status - db={cfg.db_path} (dry_run={str(cfg.dry_run).lower()})")
    print(f"total leads: {stats['total']['total']}")

    print("\nby status:")
    if stats["by_status"]:
        for status, count in stats["by_status"].items():
            print(f"  {status or '(none)'}: {count}")
    else:
        print("  (no rows)")

    print("\nby tier:")
    if stats["by_tier"]:
        for tier, count in stats["by_tier"].items():
            print(f"  {tier or '(none)'}: {count}")
    else:
        print("  (no rows)")

    print("\nenvironment (values never printed):")
    for name, state in stats["env"].items():
        print(f"  {name}: {state}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "LeadLoop autonomous lead engine "
            "(step 2a: ingest, tiering, scripting, video queueing)."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one pipeline pass and exit")
    mode.add_argument(
        "--loop", action="store_true",
        help="run the recurring background loop (default)",
    )
    mode.add_argument(
        "--status", action="store_true", help="print DB counts and env state"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_module.load()
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)

    if args.status:
        return print_status(cfg)
    if args.once:
        return run_once(cfg)
    # --loop and no flag both mean the recurring background run.
    try:
        return asyncio.run(run_loop(cfg))
    except KeyboardInterrupt:  # pragma: no cover
        LOG.info("interrupted, stopping")
        return 0


if __name__ == "__main__":
    sys.exit(main())
