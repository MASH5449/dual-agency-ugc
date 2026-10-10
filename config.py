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

        self.instantly_api_key_set = bool(env.get(ENV_INSTANTLY_API_KEY))
        self.heygen_api_key_set = bool(env.get(ENV_HEYGEN_API_KEY))

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
    def queue_file(self) -> Path:
        return self.data_dir / "queue.jsonl"

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
        return (
            f"db_path={self.db_path} dry_run={str(self.dry_run).lower()} "
            f"loop_seconds={self.loop_seconds} data_dir={self.data_dir} "
            f"campaigns_set={campaigns} "
            f"instantly_api_key={'set' if self.instantly_api_key_set else 'unset'} "
            f"heygen_api_key={'set' if self.heygen_api_key_set else 'unset'}"
        )


def load(env: dict | None = None) -> Config:
    """Build a :class:`Config` from the environment."""
    return Config(env)
