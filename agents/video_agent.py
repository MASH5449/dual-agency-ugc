"""Video stage — queues one render per scripted lead (stdlib only).

Tag: ``[Agent-Video]``. Status move: ``scripted`` -> ``video_queued`` (or
``failed``, logged with the reason).

This is the only stage that talks to a video provider, and it does so through
``agents/video_provider.py``'s single interface, so swapping providers changes
nothing here. It runs on asyncio: the blocking provider call is pushed to a
worker thread and consecutive live calls are paced by ``agents.util.throttle``
(a no-op in dry-run, which is the default).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from pathlib import Path

from agents.base import Agent
from agents.util import THROTTLE_MAX_SECONDS, THROTTLE_MIN_SECONDS, throttle_async
from agents.video_provider import VideoProvider, VideoRequest, build_provider
from db import STATUS_FAILED, STATUS_SCRIPTED, STATUS_VIDEO_QUEUED, Database


class VideoQueueAgent(Agent):
    """Queues a render for every ``scripted`` lead via the configured provider."""

    name = "Video"
    tag = "[Agent-Video]"

    def __init__(
        self,
        config=None,
        db: Database | None = None,
        provider: VideoProvider | None = None,
        sleep=None,
        rand=None,
        throttle_delay: float | None = None,
    ) -> None:
        super().__init__(config)
        self.db = db or Database(getattr(config, "db_path", "./leads.db"))
        self.provider = provider if provider is not None else build_provider(config)
        # Injectable for tests: sleep/rand are never real waits there.
        self._sleep = sleep
        self._rand = rand
        #: None keeps the configured bounds; 0 disables agent-level pacing.
        self._throttle_delay = throttle_delay
        self.log(f"video provider: {self.provider.describe()}")

    # -- helpers ----------------------------------------------------------
    @property
    def scripts_dir(self) -> Path:
        return Path(getattr(self.config, "scripts_dir", "./data/scripts"))

    def load_script(self, lead_id: int) -> dict | None:
        path = self.scripts_dir / f"{lead_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log_error(f"cannot read script for lead {lead_id}: {exc}")
            return None

    def _throttle_kwargs(self) -> dict:
        kwargs = {
            "min_seconds": float(
                getattr(self.config, "throttle_min_seconds", THROTTLE_MIN_SECONDS)
            ),
            "max_seconds": float(
                getattr(self.config, "throttle_max_seconds", THROTTLE_MAX_SECONDS)
            ),
            "dry_run": bool(getattr(self.config, "dry_run", True)),
        }
        if self._throttle_delay is not None:
            kwargs["delay"] = self._throttle_delay
        else:
            kwargs["delay"] = getattr(self.config, "throttle_delay_seconds", None)
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        if self._rand is not None:
            kwargs["rand"] = self._rand
        return kwargs

    # -- stage ------------------------------------------------------------
    async def run_async(self) -> dict[str, int]:
        """Queue every ``scripted`` lead; never raises on a bad provider call."""
        totals = {"queued": 0, "failed": 0, "duplicate": 0}
        rows = self.db.leads_by_status(STATUS_SCRIPTED)
        if not rows:
            self.log("no 'scripted' leads to queue")
            return totals

        for index, row in enumerate(rows):
            lead_id = row["id"]
            document = self.load_script(lead_id)
            if not document:
                self.log_error(f"lead {lead_id} has no readable script - failed")
                self.db.update_lead(lead_id, status=STATUS_FAILED)
                totals["failed"] += 1
                continue

            request = VideoRequest(
                lead_id=lead_id,
                email=row["email"],
                tier=(row["tier"] or document.get("tier") or ""),
                script=str(document.get("script") or ""),
                target_duration_seconds=document.get("target_duration_seconds"),
            )
            try:
                # Pace consecutive live calls; a no-op in dry-run.
                if index > 0:
                    slept = await throttle_async(**self._throttle_kwargs())
                    if slept:
                        self.log(f"throttled {slept:.2f}s before the next live call")
                # The provider is blocking (urllib) - keep the loop free.
                result = await asyncio.to_thread(self.provider.queue_video, request)
            except Exception as exc:  # noqa: BLE001 - per-lead safety net
                self.log_error(
                    f"lead {lead_id} tier={request.tier} render not queued "
                    f"({type(exc).__name__}: {exc}) -> status={STATUS_FAILED}"
                )
                self.db.update_lead(lead_id, status=STATUS_FAILED)
                totals["failed"] += 1
                continue

            ok, reason = self.db.attach_video(
                lead_id,
                result.video_id,
                result.video_url,
                result.status or STATUS_VIDEO_QUEUED,
            )
            if not ok:
                # A duplicate render must never corrupt the row that owns the id.
                self.log_error(
                    f"lead {lead_id} render rejected: {reason} - existing row "
                    f"left intact, this lead marked {STATUS_FAILED}"
                )
                self.db.update_lead(lead_id, status=STATUS_FAILED)
                totals["duplicate"] += 1
                continue

            totals["queued"] += 1
            self.log(
                f"lead {lead_id} tier={request.tier} queued via "
                f"{result.provider}: video_id={result.video_id} "
                f"url={result.video_url} -> status={result.status}"
            )

        self.log(
            f"video queueing complete: {totals['queued']} queued, "
            f"{totals['failed']} failed, {totals['duplicate']} duplicate-id "
            f"(provider={self.provider.name})"
        )
        return totals

    def run_once(self) -> dict[str, int]:
        """Sync entry point used by the pipeline."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async())
        # Already inside a loop: give the coroutine its own loop in a thread.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(self.run_async())).result()
