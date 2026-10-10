"""Video provider interface for LeadLoop — dry-run by default (stdlib only).

One tiny interface, two implementations, chosen by configuration and logged at
startup:

* :class:`DryRunVideoProvider` — **the default**. No network at all. Returns a
  synthetic, obviously fake ``dryrun-<uuid4>`` video id and a ``.invalid``
  placeholder URL, so the whole pipeline can be exercised end to end offline.
* :class:`HeyGenVideoProvider` — real HeyGen v3 over ``urllib.request``.
  Looks up a valid public avatar look + its default voice dynamically, then
  queues an avatar video from the generated script.

Behaviour carried over from the legacy ``video_pipeline.py`` (re-implemented
stdlib-only, the legacy file is untouched): ``MOCK_MODE`` acts as a hard
offline switch, and failures report ``{"status": "error", ...}`` payloads.

LIVE-SAFETY RULE: the HeyGen provider **refuses to run** unless
``DRY_RUN=false`` *and* ``HEYGEN_API_KEY`` is set. It raises
:class:`~agents.util.ProviderUnavailable` with a clear reason, so the caller
fails the lead row loudly instead of silently skipping it. API key values are
never logged — only whether they are set.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from agents.util import (
    THROTTLE_MAX_SECONDS,
    THROTTLE_MIN_SECONDS,
    PermanentError,
    ProviderUnavailable,
    TransientError,
    retry,
    throttle,
)
from db import STATUS_VIDEO_QUEUED

# ---------------------------------------------------------------------------
# CONSTANTS — tune here
# ---------------------------------------------------------------------------

HEYGEN_API_BASE = "https://api.heygen.com"
HEYGEN_LOOKS_URL = f"{HEYGEN_API_BASE}/v3/avatars/looks?ownership=public&limit=1"
HEYGEN_VIDEOS_URL = f"{HEYGEN_API_BASE}/v3/videos"
HEYGEN_ASPECT_RATIO = "9:16"
#: Used only if the look's own default voice is missing (same fallback the
#: legacy pipeline used).
HEYGEN_FALLBACK_VOICE_ID = "2d5b0e6cf36f460aa7fc47e3eee4ba54"
HEYGEN_TIMEOUT_SECONDS = 30

PROVIDER_DRY_RUN_NAME = "dry-run"
PROVIDER_HEYGEN_NAME = "heygen"
#: Host suffix used by the fake placeholder URL. ``.invalid`` is reserved by
#: RFC 2606 and can never resolve, so it can never be mistaken for a real link.
DRYRUN_HOST = "dryrun.invalid"


def error_payload(message: str, details: Any = None) -> dict:
    """The error shape the legacy pipeline used, kept for log/diagnostic parity."""
    return {"status": "error", "message": message, "details": details}


@dataclass
class VideoRequest:
    """What a provider needs to queue one render."""

    lead_id: int
    email: str
    tier: str
    script: str
    target_duration_seconds: int | None = None


@dataclass
class VideoResult:
    """What a provider hands back after queueing."""

    video_id: str
    provider: str
    status: str = STATUS_VIDEO_QUEUED
    video_url: str | None = None
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class VideoProvider:
    """Minimal provider interface. Every provider implements ``queue_video``.

    A new provider must implement the same method, return a
    :class:`VideoResult` with a unique ``video_id``, and go through
    ``agents.util.retry`` / ``agents.util.throttle`` for pacing — see AGENTS.md.
    """

    name = "base"
    live = False

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def describe(self) -> str:
        """One-line, secret-free description for the startup log."""
        return f"{self.name} (live={str(self.live).lower()})"

    def queue_video(self, request: VideoRequest) -> VideoResult:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Dry run — the default
# ---------------------------------------------------------------------------


class DryRunVideoProvider(VideoProvider):
    """Offline provider: unique synthetic ids, no network, no credentials."""

    name = PROVIDER_DRY_RUN_NAME
    live = False

    def __init__(self, config: Any = None, id_factory: Callable[[], str] | None = None):
        super().__init__(config)
        self._id_factory = id_factory

    def new_video_id(self) -> str:
        if self._id_factory is not None:
            return self._id_factory()
        return f"dryrun-{uuid.uuid4()}"

    def queue_video(self, request: VideoRequest) -> VideoResult:
        video_id = self.new_video_id()
        return VideoResult(
            video_id=video_id,
            provider=self.name,
            status=STATUS_VIDEO_QUEUED,
            video_url=f"https://{DRYRUN_HOST}/videos/{video_id}",
            raw={
                "status": "success",
                "message": "dry-run: no render was requested",
                "dry_run": True,
            },
        )


# ---------------------------------------------------------------------------
# HeyGen v3 — live
# ---------------------------------------------------------------------------


class HeyGenVideoProvider(VideoProvider):
    """Live HeyGen provider. Never used in tests; refuses without credentials."""

    name = PROVIDER_HEYGEN_NAME
    live = True

    def __init__(
        self,
        config: Any = None,
        *,
        api_key: str | None = None,
        dry_run: bool | None = None,
        mock_mode: bool | None = None,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], Any] | None = None,
        rand: Callable[[float, float], float] | None = None,
        log: Callable[[str], None] | None = None,
        tag: str = "[Agent-Video]",
        throttle_delay: float | None = None,
    ) -> None:
        super().__init__(config)
        self._api_key = api_key
        self._dry_run = dry_run
        self._mock_mode = mock_mode
        self._opener = opener or urllib.request.urlopen
        self._throttle_delay = throttle_delay
        self.tag = tag
        if log is None:
            import logging

            log = logging.getLogger(tag).info
        self.log = log
        # Injectable so tests never wait; production keeps the real helpers.
        import time

        self.sleep = sleep or time.sleep
        self.rand = rand
        if self.rand is None:
            import random

            self.rand = random.uniform

    # -- resolved config --------------------------------------------------
    @property
    def api_key(self) -> str:
        if self._api_key is not None:
            return self._api_key
        return getattr(self.config, "heygen_api_key", "") or ""

    @property
    def dry_run(self) -> bool:
        if self._dry_run is not None:
            return self._dry_run
        return bool(getattr(self.config, "dry_run", True))

    @property
    def mock_mode(self) -> bool:
        if self._mock_mode is not None:
            return self._mock_mode
        return bool(getattr(self.config, "mock_mode", False))

    @property
    def throttle_min(self) -> float:
        return float(getattr(self.config, "throttle_min_seconds", THROTTLE_MIN_SECONDS))

    @property
    def throttle_max(self) -> float:
        return float(getattr(self.config, "throttle_max_seconds", THROTTLE_MAX_SECONDS))

    def refusal_reason(self) -> str | None:
        """Why this provider cannot run, or None when it may."""
        if self.dry_run:
            return (
                "live HeyGen render refused: DRY_RUN is not false "
                "(set DRY_RUN=false to go live)"
            )
        if self.mock_mode:
            return "live HeyGen render refused: MOCK_MODE is enabled"
        if not self.api_key:
            return "live HeyGen render refused: HEYGEN_API_KEY is unset"
        return None

    # -- HTTP -------------------------------------------------------------
    def _http_json(
        self, url: str, payload: dict | None = None, method: str = "GET"
    ) -> dict:
        """One HTTP JSON call, raising classified errors for retry().

        Never logs the key. ``urllib.error.HTTPError``/network errors are
        re-raised as TransientError/PermanentError so the retry policy is the
        shared one: 429/5xx and connection problems retry, other 4xx do not.
        """
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=HEYGEN_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            details = ""
            try:
                details = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - best effort
                details = ""
            message = f"HTTP {exc.code} from {url}: {details[:500]}"
            if exc.code in (408, 409, 425, 429) or exc.code >= 500:
                raise TransientError(message, http_status=exc.code) from exc
            raise PermanentError(message, http_status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise TransientError(f"connection failed for {url}: {exc.reason}") from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise TransientError(f"connection failed for {url}: {exc}") from exc

        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError as exc:
            raise PermanentError(f"non-JSON response from {url}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise PermanentError(f"unexpected response shape from {url}")
        if status >= 500:
            raise TransientError(f"HTTP {status} from {url}", http_status=status)
        return parsed

    def _pace(self) -> None:
        """Randomised 2-5s gap before a live call (no-op in dry-run/offline)."""
        throttle(
            min_seconds=self.throttle_min,
            max_seconds=self.throttle_max,
            delay=self._throttle_delay,
            dry_run=False,
            sleep=self.sleep,
            rand=self.rand,
        )

    def lookup_avatar(self) -> tuple[str, str]:
        """Resolve a valid public avatar look id + voice id, dynamically."""

        def _call() -> tuple[str, str]:
            self._pace()
            payload = self._http_json(HEYGEN_LOOKS_URL)
            looks = payload.get("data") or []
            if not looks:
                raise PermanentError(error_payload("no public avatar look found"))
            look = looks[0] or {}
            avatar_id = look.get("id") or look.get("look_id")
            voice_id = look.get("default_voice_id") or HEYGEN_FALLBACK_VOICE_ID
            if not avatar_id:
                raise PermanentError(error_payload("avatar look has no id"))
            return str(avatar_id), str(voice_id)

        return retry(
            _call,
            tag=self.tag,
            log=self.log,
            sleep=self.sleep,
            rand=self.rand,
            label="heygen avatar lookup",
        )

    def queue_video(self, request: VideoRequest) -> VideoResult:
        """Queue one HeyGen v3 avatar render, retried on transient failures."""
        reason = self.refusal_reason()
        if reason is not None:
            # Loud refusal: the caller marks the row failed with this reason.
            self.log(reason)
            raise ProviderUnavailable(reason)

        avatar_id, voice_id = self.lookup_avatar()

        def _call() -> dict:
            self._pace()
            return self._http_json(
                HEYGEN_VIDEOS_URL,
                payload={
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "script": request.script,
                    "voice_id": voice_id,
                    "aspect_ratio": HEYGEN_ASPECT_RATIO,
                },
                method="POST",
            )

        response = retry(
            _call,
            tag=self.tag,
            log=self.log,
            sleep=self.sleep,
            rand=self.rand,
            label="heygen video create",
        )
        data = response.get("data") or {}
        video_id = data.get("video_id") or data.get("id")
        if not video_id:
            message = error_payload("HeyGen returned no video_id", response)
            self.log(f"heygen video create failed: {message}")
            raise PermanentError(str(message))
        return VideoResult(
            video_id=str(video_id),
            provider=self.name,
            status=STATUS_VIDEO_QUEUED,
            video_url=data.get("video_url") or data.get("url"),
            raw=response,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_provider(config: Any = None, **kwargs: Any) -> VideoProvider:
    """Provider selected by ``VIDEO_PROVIDER`` (default ``dry-run``).

    An unknown name raises rather than silently falling back to dry-run: a typo
    must not look like "renders are happening".
    """
    name = (getattr(config, "video_provider", PROVIDER_DRY_RUN_NAME) or "").strip().lower()
    if name in ("", PROVIDER_DRY_RUN_NAME, "dryrun", "dry_run", "mock"):
        return DryRunVideoProvider(config, **kwargs)
    if name == PROVIDER_HEYGEN_NAME:
        return HeyGenVideoProvider(config, **kwargs)
    raise ProviderUnavailable(f"unknown VIDEO_PROVIDER {name!r}")


def describe_provider(config: Any) -> str:
    """Secret-free provider description for the startup log (no key values)."""
    try:
        provider = build_provider(config)
    except Exception as exc:  # noqa: BLE001 - reported, not raised, at startup
        return f"unavailable ({type(exc).__name__}: {exc})"
    reason = ""
    if isinstance(provider, HeyGenVideoProvider):
        refusal = provider.refusal_reason()
        reason = f" refused={refusal}" if refusal else " ready"
    return provider.describe() + reason
