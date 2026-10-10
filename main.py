#!/usr/bin/env python3
"""LeadLoop — autonomous background lead engine (step 2a)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import os
import random
from typing import Any

# Network & Web Framework
import requests
import stripe
from fastapi import FastAPI, Request, HTTPException, Header, BackgroundTasks

# Local Modules
import config as config_module
from agents.base import configure_logging
from agents.classifier_agent import ClassifierAgent
from agents.scraper_agent import ScraperAgent
from agents.script_agent import ScriptAgent
from agents.video_agent import VideoQueueAgent
from agents.video_provider import describe_provider
from db import Database

#: Log tag for the pipeline driver.
DRIVER_TAG = "[LeadLoop]"
LOG = logging.getLogger(DRIVER_TAG)

# ---------------------------------------------------------------------------
# Dynamic HeyGen UGC Avatar Pool
# ---------------------------------------------------------------------------
HEYGEN_AVATARS = [
    "Daisy-CustomerSupport-public",
    "josh_lite3_20230714",
    "Kristin_public_3_20240108",
    "Tyler-incamera-20240408"
]

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class Pipeline:
    """Runs the step-1 agents in order over a single pass."""
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

    def run_pass(self) -> dict[str, Any]:
        """Run one full pipeline pass; never raises."""
        self.passes += 1
        summary: dict[str, Any] = {"pass": self.passes}
        LOG.info("pass %d starting", self.passes)

        # Stage 1 — ingest.
        try:
            scraper = ScraperAgent(self.cfg, self.db)
            summary["ingest"] = scraper.ingest()
        except Exception as exc:
            LOG.error("scraper stage failed (%s: %s) - continuing", type(exc).__name__, exc)
            summary["ingest"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Stage 2 — tiering.
        try:
            classifier = ClassifierAgent(self.cfg, self.db)
            summary["classify"] = classifier.run_once()
        except Exception as exc:
            LOG.error("classifier stage failed (%s: %s) - continuing", type(exc).__name__, exc)
            summary["classify"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Stage 3 — script generation.
        try:
            script_agent = ScriptAgent(self.cfg, self.db)
            summary["script"] = script_agent.run_once()
        except Exception as exc:
            LOG.error("script stage failed (%s: %s) - continuing", type(exc).__name__, exc)
            summary["script"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Stage 4 — queue render.
        try:
            video_agent = VideoQueueAgent(self.cfg, self.db)
            summary["video"] = video_agent.run_once()
        except Exception as exc:
            LOG.error("video stage failed (%s: %s) - continuing", type(exc).__name__, exc)
            summary["video"] = {"error": f"{type(exc).__name__}: {exc}"}

        summary["leads_total"] = self.db.count()
        LOG.info("pass %d done: %d lead(s) in DB, dry_run=%s", self.passes, summary["leads_total"], str(self.cfg.dry_run).lower())
        return summary

    def status(self) -> dict[str, Any]:
        stats = self.db.stats()
        stats["env"] = self.cfg.describe()
        return stats


# ---------------------------------------------------------------------------
# Modes & Execution Drivers
# ---------------------------------------------------------------------------
def log_startup(cfg) -> None:
    LOG.info("starting: %s", cfg.summary())
    for name, state in cfg.describe().items():
        LOG.info("env %s: %s", name, state)
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
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: stop.set())

    while not stop.is_set():
        try:
            await asyncio.to_thread(pipeline.run_pass)
        except Exception as exc:
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
    except Exception as exc:
        print(f"cannot read {cfg.db_path}: {type(exc).__name__}: {exc}")
        stats = {"total": {"total": 0}, "by_status": {}, "by_tier": {}, "env": cfg.describe()}

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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="LeadLoop autonomous lead engine")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one pipeline pass and exit")
    mode.add_argument("--loop", action="store_true", help="run the recurring background loop (default)")
    mode.add_argument("--status", action="store_true", help="print DB counts and env state")
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
    try:
        return asyncio.run(run_loop(cfg))
    except KeyboardInterrupt:
        LOG.info("interrupted, stopping")
        return 0


# ---------------------------------------------------------------------------
# HeyGen Auto-Fulfillment Helper
# ---------------------------------------------------------------------------
def trigger_heygen_video(brand_name: str, product_name: str, script_text: str):
    """Sends payload to HeyGen API with dynamic avatar selection."""
    heygen_key = os.getenv("HEYGEN_API_KEY", "heygen_placeholder")
    url = "https://api.heygen.com/v2/video/generate"
    headers = {
        "X-Api-Key": heygen_key,
        "Content-Type": "application/json"
    }
    
    # Pick a random creator avatar from the pool for fresh variation
    chosen_avatar = random.choice(HEYGEN_AVATARS)
    LOG.info(f"[HeyGen] Dynamically selected avatar '{chosen_avatar}' for {brand_name}")

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": chosen_avatar,
                    "avatar_style": "normal"
                },
                "voice": {
                    "type": "text",
                    "input_text": script_text,
                    "voice_id": "2d4b87121c7042299c3a0e0e3369a563"
                }
            }
        ],
        "dimension": {
            "width": 1080,
            "height": 1920
        }
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        LOG.info(f"[HeyGen] Fulfillment response ({res.status_code}): {res.text}")
    except Exception as e:
        LOG.error(f"[HeyGen] Failed to dispatch video: {str(e)}")


# ---------------------------------------------------------------------------
# Railway ASGI Integration & Webhooks
# ---------------------------------------------------------------------------
app = FastAPI(title="LeadLoop Engine")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
stripe.api_key = STRIPE_SECRET_KEY

@app.on_event("startup")
async def start_leadloop_background_worker():
    """Starts the LeadLoop background pipeline when Railway boots Uvicorn."""
    cfg = config_module.load()
    LOG.info("[LeadLoop] Starting background worker via FastAPI startup event")
    asyncio.create_task(run_loop(cfg))

@app.get("/")
@app.get("/health")
async def health_check():
    """Healthcheck endpoint for Railway."""
    cfg = config_module.load()
    pipeline = Pipeline(cfg)
    return {
        "status": "healthy",
        "service": "LeadLoop Engine",
        "db_stats": pipeline.status()
    }

@app.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    background_tasks: BackgroundTasks = None
):
    """Listens for Stripe checkout completions and triggers HeyGen rendering."""
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        LOG.error(f"Stripe webhook signature error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        metadata = session.get('metadata', {})
        brand_name = metadata.get('brand_name', 'DTC Brand')
        product_name = metadata.get('product_name', 'Skincare Product')
        
        script_text = (
            f"Hey {brand_name}! Here is your high-converting demonstrator hook for {product_name}. "
            "Stop wasting ad spend on generic content that gets scrolled past. "
            "Try this UGC format to boost your ROAS instantly!"
        )

        LOG.info(f"VERIFIED PAYMENT: Fulfilling order for {brand_name}")
        
        if background_tasks:
            background_tasks.add_task(trigger_heygen_video, brand_name, product_name, script_text)
        else:
            trigger_heygen_video(brand_name, product_name, script_text)

    return {"status": "success"}

if __name__ == "__main__":
    sys.exit(main())
