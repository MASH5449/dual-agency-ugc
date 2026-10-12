"""Shared resilience helpers for LeadLoop: retry + throttle (stdlib only).

Every outbound provider call (HeyGen today, Instantly later) goes through these
two helpers, so the retry policy and the pacing policy live in exactly one
place. Nothing here performs I/O itself; the network call is the caller's
``operation`` callable.

The engine runs on asyncio (APScheduler is not installable on this machine), so
the pacing helper has both a sync form — for a blocking ``urllib`` call running
in a worker thread — and an async form for the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import time
import urllib.error
from typing import Any, Callable

# ---------------------------------------------------------------------------
# RESILIENCE CONSTANTS — tune everything here
# ---------------------------------------------------------------------------

#: Total attempts for one logical call (1 try + 2 retries).
RETRY_MAX_ATTEMPTS = 3
#: First backoff delay in seconds; doubles each retry: 1s -> 2s -> 4s.
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_BACKOFF_FACTOR = 2.0
#: +/- this fraction of random jitter is added to every backoff delay, so a
#: batch of failures does not retry in lockstep.
RETRY_JITTER_FRACTION = 0.25
#: Hard ceiling for a single backoff delay.
RETRY_MAX_DELAY_SECONDS = 8.0

#: HTTP statuses worth retrying: rate limits, timeouts and server faults.
#: Everything else in the 4xx range is the caller's fault and is *never*
#: retried (a plain 400 means retrying would fail identically).
TRANSIENT_HTTP_STATUSES = frozenset(
    {408, 409, 425, 429, 500, 502, 503, 504, 507, 508, 510, 511}
)

#: Randomised delay window between consecutive LIVE provider calls.
THROTTLE_MIN_SECONDS = 2.0
THROTTLE_MAX_SECONDS = 5.0

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TransientError(Exception):
    """A failure that is worth retrying (timeout, connection, 429, 5xx)."""

    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class PermanentError(Exception):
    """A failure that will not get better: never retried (plain 4xx, config)."""

    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class ProviderUnavailable(PermanentError):
    """A provider refuses to run (live mode without credentials, say).

    Deliberately *permanent*: the engine must fail that row loudly instead of
    quietly skipping it or retrying a configuration problem.
    """


def is_transient(exc: BaseException) -> bool:
    """Whether ``exc`` should be retried.

    Transient: our own :class:`TransientError`, ``TimeoutError``/``socket``
    timeouts, connection errors, ``urllib`` URL/connection errors, and HTTP
    429/5xx. Everything else — including every other 4xx — is permanent.
    """
    if isinstance(exc, PermanentError):
        return False
    if isinstance(exc, TransientError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return int(exc.code) in TRANSIENT_HTTP_STATUSES
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        # URLError wraps connection-level socket failures; a timeout or reset
        # here is transient, a malformed URL is not.
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout, ConnectionError, OSError)):
            return True
        return not isinstance(reason, ValueError)
    return isinstance(exc, OSError)


# ---------------------------------------------------------------------------
# Backoff / retry
# ---------------------------------------------------------------------------


def backoff_delay(
    attempt: int,
    *,
    base_delay: float = RETRY_BASE_DELAY_SECONDS,
    factor: float = RETRY_BACKOFF_FACTOR,
    jitter: float = RETRY_JITTER_FRACTION,
    max_delay: float = RETRY_MAX_DELAY_SECONDS,
    rand: Callable[[float, float], float] = random.uniform,
) -> float:
    """Delay before the *next* attempt: 1s, 2s, 4s..., plus jitter."""
    core = base_delay * (factor ** max(0, attempt - 1))
    spread = core * max(0.0, jitter)
    delay = core + (rand(-spread, spread) if spread else 0.0)
    return max(0.0, min(delay, max_delay))


def default_logger(tag: str) -> logging.Logger:
    """A logger whose name *is* the agent tag, so lines stay tagged once."""
    return logging.getLogger(tag)


def retry(
    operation: Callable[[], Any],
    *,
    tag: str = "[retry]",
    log: Callable[[str], None] | None = None,
    attempts: int = RETRY_MAX_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_SECONDS,
    factor: float = RETRY_BACKOFF_FACTOR,
    jitter: float = RETRY_JITTER_FRACTION,
    max_delay: float = RETRY_MAX_DELAY_SECONDS,
    sleep: Callable[[float], Any] = time.sleep,
    rand: Callable[[float, float], float] = random.uniform,
    label: str = "call",
) -> Any:
    """Run ``operation`` with exponential backoff, logging under ``tag``.

    Up to ``attempts`` tries in total. Only transient failures are retried
    (:func:`is_transient`); a plain 4xx raises immediately without consuming
    another attempt. Every attempt and the final give-up are logged, so a
    rejected render is visible in the log rather than silent.

    ``sleep``/``rand``/``base_delay`` are injectable so tests never wait.
    """
    emit = log or default_logger(tag).info
    last: BaseException | None = None

    for attempt in range(1, max(1, attempts) + 1):
        try:
            result = operation()
            if attempt > 1:
                emit(f"{label} succeeded on attempt {attempt}/{attempts}")
            return result
        except BaseException as exc:  # noqa: BLE001 - classified below
            last = exc
            transient = is_transient(exc)
            emit(
                f"{label} attempt {attempt}/{attempts} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            if not transient:
                emit(f"{label} is not retryable ({type(exc).__name__}) - giving up")
                raise
            if attempt >= attempts:
                emit(f"{label} giving up after {attempt} attempts")
                raise
            delay = backoff_delay(
                attempt,
                base_delay=base_delay,
                factor=factor,
                jitter=jitter,
                max_delay=max_delay,
                rand=rand,
            )
            emit(f"{label} retrying in {delay:.2f}s")
            sleep(delay)

    raise last if last is not None else RuntimeError(f"{label} failed")  # pragma: no cover


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------


def pick_throttle_delay(
    *,
    min_seconds: float = THROTTLE_MIN_SECONDS,
    max_seconds: float = THROTTLE_MAX_SECONDS,
    delay: float | None = None,
    rand: Callable[[float, float], float] = random.uniform,
) -> float:
    """The delay to apply before the next live provider call (seconds)."""
    if delay is not None:
        return max(0.0, float(delay))
    low = max(0.0, float(min_seconds))
    high = max(low, float(max_seconds))
    if high == 0.0:
        return 0.0
    if high == low:
        return low
    return rand(low, high)


def throttle(
    *,
    min_seconds: float = THROTTLE_MIN_SECONDS,
    max_seconds: float = THROTTLE_MAX_SECONDS,
    delay: float | None = None,
    dry_run: bool = True,
    sleep: Callable[[float], Any] = time.sleep,
    rand: Callable[[float, float], float] = random.uniform,
) -> float:
    """Sleep between consecutive LIVE provider calls; returns seconds slept.

    Dry-run is free: nothing is sent, so there is nothing to pace, and the
    delay is skipped entirely. A fixed ``delay`` of 0 (what tests and any
    offline run set) also skips the sleep.
    """
    if dry_run:
        return 0.0
    seconds = pick_throttle_delay(
        min_seconds=min_seconds, max_seconds=max_seconds, delay=delay, rand=rand
    )
    if seconds <= 0:
        return 0.0
    sleep(seconds)
    return seconds


async def throttle_async(
    *,
    min_seconds: float = THROTTLE_MIN_SECONDS,
    max_seconds: float = THROTTLE_MAX_SECONDS,
    delay: float | None = None,
    dry_run: bool = True,
    sleep: Callable[[float], Any] = asyncio.sleep,
    rand: Callable[[float, float], float] = random.uniform,
) -> float:
    """Asyncio form of :func:`throttle` — awaits instead of blocking."""
    if dry_run:
        return 0.0
    seconds = pick_throttle_delay(
        min_seconds=min_seconds, max_seconds=max_seconds, delay=delay, rand=rand
    )
    if seconds <= 0:
        return 0.0
    await sleep(seconds)
    return seconds


class Throttle:
    """Small stateful wrapper so a caller can pace a sequence of live calls.

    ``await throttle.between_calls()`` before call 2, 3, ... — never before the
    first one. In dry-run (or with ``delay=0``) it is a no-op that returns 0.0,
    which is what makes both tests and offline runs free.
    """

    def __init__(
        self,
        *,
        min_seconds: float = THROTTLE_MIN_SECONDS,
        max_seconds: float = THROTTLE_MAX_SECONDS,
        delay: float | None = None,
        dry_run: bool = True,
        sleep: Callable[[float], Any] | None = None,
        rand: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.delay = delay
        self.dry_run = dry_run
        self.sleep = sleep
        self.rand = rand
        self.calls_made = 0
        self.seconds_slept = 0.0

    @property
    def active(self) -> bool:
        """False when pacing is pointless (dry-run, or a zero/negative delay)."""
        if self.dry_run:
            return False
        if self.delay is not None:
            return float(self.delay) > 0
        return max(self.min_seconds, self.max_seconds) > 0

    def _sleep_callable(self) -> Callable[[float], Any]:
        if self.sleep is not None:
            return self.sleep
        return time.sleep

    def wait(self) -> float:
        """Blocking form: sleep unless this is the first call / a no-op."""
        if self.calls_made > 0 and self.active:
            slept = throttle(
                min_seconds=self.min_seconds,
                max_seconds=self.max_seconds,
                delay=self.delay,
                dry_run=False,
                sleep=self._sleep_callable(),
                rand=self.rand,
            )
            self.seconds_slept += slept
        self.calls_made += 1
        return self.seconds_slept

    async def between_calls(self) -> float:
        """Awaitable form: no delay before the first call."""
        slept = 0.0
        if self.calls_made > 0 and self.active:
            seconds = pick_throttle_delay(
                min_seconds=self.min_seconds,
                max_seconds=self.max_seconds,
                delay=self.delay,
                rand=self.rand,
            )
            if seconds > 0:
                if self.sleep is not None:
                    # Injected (synchronous) sleep: tests and offline runs.
                    self.sleep(seconds)
                else:
                    await asyncio.sleep(seconds)
                slept = seconds
                self.seconds_slept += slept
        self.calls_made += 1
        return slept
