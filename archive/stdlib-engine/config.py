"""LeadLoop configuration loader (stdlib only).

All configuration comes from environment variables with safe local defaults.
Nothing here performs I/O on the network, and no value is ever logged or
printed: use :func:`describe` to report set/unset state only.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Environment variable names (single source of truth)
# ---------------------------------------------------------------------------

#: Path to the SQLite database file.
ENV_DB_PATH = "DB_PATH"
#: "true"/"false" switch; anything other than an explicit go-live value stays
#: in dry-run. Dry-run is the default and the safe state.
ENV_DRY_RUN = "DRY_RUN"
#: Seconds between pipeline passes in --loop mode.
ENV_LOOP_SECONDS = "LOOP_SECONDS"
#: Directory holding the inbox of ingest files.
ENV_DATA_DIR = "DATA_DIR"

#: Instantly campaign IDs, one per tier (step 2 uses these to route a video).
ENV_CAMPAIGN_QUICK = "INSTANTLY_CAMPAIGN_QUICK"
ENV_CAMPAIGN_SCALE = "INSTANTLY_CAMPAIGN_SCALE"
ENV_CAMPAIGN_WHITELABEL = "INSTANTLY_CAMPAIGN_WHITELABEL"

#: Secrets. Never printed, only reported as set/unset.
ENV_INSTANTLY_API_KEY = "INSTANTLY_API_KEY"
ENV_HEYGEN_API_KEY = "HEYGEN_API_KEY"

#: Provenance recorded for an ingested row that does not declare its own
#: ``lawful_basis``. The engine only ever ingests the owner's opted-in list.
ENV_DEFAULT_LAWFUL_BASIS = "DEFAULT_LAWFUL_BASIS"

#: Optional per-tier price shown in a generated script. Leave unset (the
#: default) and the script says nothing about price — no pricing exists in
#: Stripe for this business yet, so the copy must never invent one.
ENV_TIER_PRICE_QUICK = "TIER_PRICE_QUICK"
ENV_TIER_PRICE_SCALE = "TIER_PRICE_SCALE"
ENV_TIER_PRICE_WHITELABEL = "TIER_PRICE_WHITELABEL"

#: Video provider selection: "dry-run" (default, no network) or "heygen".
ENV_VIDEO_PROVIDER = "VIDEO_PROVIDER"
#: Legacy-compatible hard offline switch: "true" makes every live provider
#: refuse to run even when DRY_RUN is false.
ENV_MOCK_MODE = "MOCK_MODE"
#: Randomised delay bounds (seconds) between consecutive LIVE provider calls.
ENV_THROTTLE_MIN = "THROTTLE_MIN_SECONDS"
ENV_THROTTLE_MAX = "THROTTLE_MAX_SECONDS"
#: Optional fixed override for the throttle delay. "0" disables the delay
#: entirely (tests and dry-run use this).
ENV_THROTTLE_DELAY = "THROTTLE_DELAY_SECONDS"

DEFAULT_LAWFUL_BASIS_FALLBACK = "owned opted-in list"
PROVIDER_DRY_RUN = "dry-run"
PROVIDER_HEYGEN = "heygen"

DEFAULTS = {
    ENV_DB_PATH: "./leads.db",
    ENV_DRY_RUN: "true",
    ENV_LOOP_SECONDS: "300",
    ENV_DATA_DIR: "./data",
    ENV_CAMPAIGN_QUICK: "",
    ENV_CAMPAIGN_SCALE: "",
    ENV_CAMPAIGN_WHITELABEL: "",
    ENV_INSTANTLY_API_KEY: "",
    ENV_HEYGEN_API_KEY: "",
    ENV_DEFAULT_LAWFUL_BASIS: DEFAULT_LAWFUL_BASIS_FALLBACK,
    ENV_TIER_PRICE_QUICK: "",
    ENV_TIER_PRICE_SCALE: "",
    ENV_TIER_PRICE_WHITELABEL: "",
    ENV_VIDEO_PROVIDER: PROVIDER_DRY_RUN,
    ENV_MOCK_MODE: "false",
    ENV_THROTTLE_MIN: "2",
    ENV_THROTTLE_MAX: "5",
    ENV_THROTTLE_DELAY: "",
}

#: Values that mean "leave the engine in dry-run". Anything else is treated as
#: an explicit go-live request (step 2 acts on it; step 1 never sends).
DRY_RUN_TRUTHY = {"true", "1", "yes", "on", ""}

#: Variables reported (never printed) by --status and the startup summary.
REPORTED_VARS = (
    ENV_DB_PATH,
    ENV_DRY_RUN,
    ENV_LOOP_SECONDS,
    ENV_DATA_DIR,
    ENV_CAMPAIGN_QUICK,
    ENV_CAMPAIGN_SCALE,
    ENV_CAMPAIGN_WHITELABEL,
    ENV_INSTANTLY_API_KEY,
    ENV_HEYGEN_API_KEY,
    ENV_DEFAULT_LAWFUL_BASIS,
    ENV_TIER_PRICE_QUICK,
    ENV_TIER_PRICE_SCALE,
    ENV_TIER_PRICE_WHITELABEL,
    ENV_VIDEO_PROVIDER,
    ENV_MOCK_MODE,
    ENV_THROTTLE_MIN,
    ENV_THROTTLE_MAX,
    ENV_THROTTLE_DELAY,
)

#: Variables whose values are secret. describe() only says set/unset for these.
SECRET_VARS = frozenset({ENV_INSTANTLY_API_KEY, ENV_HEYGEN_API_KEY})


class Config:
    """Resolved runtime configuration."""

    def __init__(self, env: dict | None = None) -> None:
        env = os.environ if env is None else env
        self._env = env

        self.db_path = env.get(ENV_DB_PATH) or DEFAULTS[ENV_DB_PATH]
        self.data_dir = Path(env.get(ENV_DATA_DIR) or DEFAULTS[ENV_DATA_DIR])

        raw_loop = env.get(ENV_LOOP_SECONDS) or DEFAULTS[ENV_LOOP_SECONDS]
        try:
            self.loop_seconds = max(1, int(str(raw_loop).strip()))
        except (TypeError, ValueError):
            self.loop_seconds = int(DEFAULTS[ENV_LOOP_SECONDS])

        raw_dry = env.get(ENV_DRY_RUN, DEFAULTS[ENV_DRY_RUN])
        self.dry_run = str(raw_dry).strip().lower() in DRY_RUN_TRUTHY

        self.campaign_ids = {
            "quick": env.get(ENV_CAMPAIGN_QUICK, "") or "",
            "scale": env.get(ENV_CAMPAIGN_SCALE, "") or "",
            "whitelabel": env.get(ENV_CAMPAIGN_WHITELABEL, "") or "",
        }

        # Values are held only for the provider clients; they are never logged.
        self.instantly_api_key = env.get(ENV_INSTANTLY_API_KEY) or ""
        self.heygen_api_key = env.get(ENV_HEYGEN_API_KEY) or ""
        self.instantly_api_key_set = bool(self.instantly_api_key)
        self.heygen_api_key_set = bool(self.heygen_api_key)

        #: Legacy-compatible offline switch honoured by live providers.
        self.mock_mode = (
            str(env.get(ENV_MOCK_MODE, DEFAULTS[ENV_MOCK_MODE])).strip().lower()
            in DRY_RUN_TRUTHY
            and bool(str(env.get(ENV_MOCK_MODE, "")).strip())
        )

        #: Provenance default for ingested rows that don't declare one.
        self.default_lawful_basis = (
            env.get(ENV_DEFAULT_LAWFUL_BASIS) or DEFAULT_LAWFUL_BASIS_FALLBACK
        )

        #: Optional per-tier prices. Empty string means "do not mention price".
        self.tier_prices = {
            "quick": (env.get(ENV_TIER_PRICE_QUICK, "") or "").strip(),
            "scale": (env.get(ENV_TIER_PRICE_SCALE, "") or "").strip(),
            "whitelabel": (env.get(ENV_TIER_PRICE_WHITELABEL, "") or "").strip(),
        }

        #: Which video provider the video stage asks to render. Selection is
        #: explicit: an unknown name is reported by the factory, not silently
        #: downgraded to dry-run.
        self.video_provider = (
            env.get(ENV_VIDEO_PROVIDER, DEFAULTS[ENV_VIDEO_PROVIDER])
            or DEFAULTS[ENV_VIDEO_PROVIDER]
        ).strip().lower()

        #: Throttle bounds between consecutive LIVE provider calls.
        self.throttle_min_seconds = _as_float(
            env.get(ENV_THROTTLE_MIN), float(DEFAULTS[ENV_THROTTLE_MIN])
        )
        self.throttle_max_seconds = _as_float(
            env.get(ENV_THROTTLE_MAX), float(DEFAULTS[ENV_THROTTLE_MAX])
        )
        #: Fixed override; None means "randomise between the bounds above".
        self.throttle_delay_seconds = _as_optional_float(env.get(ENV_THROTTLE_DELAY))

        #: Set to True only by the explicit live-outbound switch.
        self.live_outbound = not self.dry_run

    # -- derived paths ----------------------------------------------------
    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def scripts_dir(self) -> Path:
        """Where generated scripts are persisted for the owner to review."""
        return self.data_dir / "scripts"

    @property
    def queue_file(self) -> Path:
        return self.data_dir / "queue.jsonl"

    # -- derived flags ----------------------------------------------------
    @property
    def throttle_enabled(self) -> bool:
        """The delay only ever applies to live provider calls."""
        if self.dry_run:
            return False
        if self.throttle_delay_seconds is not None:
            return self.throttle_delay_seconds > 0
        return self.throttle_max_seconds > 0 or self.throttle_min_seconds > 0

    @property
    def throttle_bounds(self) -> tuple[float, float]:
        """Effective (min, max) throttle window in seconds."""
        if self.throttle_delay_seconds is not None:
            return (self.throttle_delay_seconds, self.throttle_delay_seconds)
        low = max(0.0, self.throttle_min_seconds)
        high = max(low, self.throttle_max_seconds)
        return (low, high)

    # -- reporting --------------------------------------------------------
    def describe(self) -> dict[str, str]:
        """Report *whether* each expected variable is set.

        Values are never included — campaign IDs and API keys are treated as
        sensitive. Returns a mapping of variable name to "set" / "unset".
        """
        out: dict[str, str] = {}
        for name in REPORTED_VARS:
            out[name] = "set" if self._env.get(name) else "unset"
        return out

    def summary(self) -> str:
        """One-line config summary safe to log (no values)."""
        campaigns = ",".join(
            tier for tier, cid in self.campaign_ids.items() if cid
        ) or "none"
        prices = ",".join(tier for tier, p in self.tier_prices.items() if p) or "none"
        low, high = self.throttle_bounds
        return (
            f"db_path={self.db_path} dry_run={str(self.dry_run).lower()} "
            f"loop_seconds={self.loop_seconds} data_dir={self.data_dir} "
            f"campaigns_set={campaigns} prices_set={prices} "
            f"video_provider={self.video_provider} "
            f"throttle_seconds={low:g}-{high:g} throttle_enabled="
            f"{str(self.throttle_enabled).lower()} "
            f"lawful_basis_default={'set' if self.default_lawful_basis else 'unset'} "
            f"instantly_api_key={'set' if self.instantly_api_key_set else 'unset'} "
            f"heygen_api_key={'set' if self.heygen_api_key_set else 'unset'}"
        )


def _as_float(raw: object, default: float) -> float:
    """Parse a float from the environment, falling back to ``default``."""
    try:
        text = str(raw).strip()
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return default
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _as_optional_float(raw: object) -> float | None:
    """Parse an optional float; ``None`` when unset or unparseable."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load(env: dict | None = None) -> Config:
    """Build a :class:`Config` from the environment."""
    return Config(env)
