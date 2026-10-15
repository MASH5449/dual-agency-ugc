import asyncio
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote


APIFY_API_BASE = "https://api.apify.com/v2"
MAX_PROSPECT_ITEMS = 200
DEFAULT_ACTOR_ID = "R9SL2PPdhdhLQwdck"
DEFAULT_ACTOR_INPUT = {
    "query": "skincare",
    "category": "gid://shopify/ProductCategory/5",
    "storeLocation": "US",
    "maxItems": 25,
    "leadListName": "creative-lab-skincare-us",
    "skipPreviouslyScraped": True,
    "enrichWebsite": False,
}


class ApifyHTTPError(RuntimeError):
    def __init__(self, operation, status_code):
        super().__init__(f"{operation} returned HTTP {status_code}.")
        self.status_code = status_code


class ApifyRunFailed(RuntimeError):
    pass


class ApifyRunner:
    """Runs a configured Apify Actor and imports its dataset for human review."""

    def __init__(
        self,
        prospect_store,
        token="",
        actor_id="",
        actor_input=None,
        max_items=100,
        max_charge_usd=5.0,
        interval_seconds=86400,
        run_timeout_seconds=1800,
        poll_interval_seconds=10,
        auto_run=False,
        config_error="",
        post=None,
        get=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self.prospect_store = prospect_store
        self.token = token
        self.actor_id = actor_id
        self.actor_input = actor_input
        self.max_items = max_items
        self.max_charge_usd = max_charge_usd
        self.interval_seconds = interval_seconds
        self.run_timeout_seconds = run_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.auto_run = auto_run
        self.config_error = config_error
        self._post = post
        self._get = get
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._active = False
        self._status = {
            "state": "idle",
            "message": "No Apify run has started.",
            "run_id": "",
            "started_at": "",
            "finished_at": "",
            "results": None,
        }
        self._task = None

    @classmethod
    def from_environment(cls, prospect_store, post=None, get=None):
        errors = []
        actor_input = None
        raw_input = os.getenv("APIFY_ACTOR_INPUT_JSON", "").strip()
        if raw_input:
            try:
                actor_input = json.loads(raw_input)
                if not isinstance(actor_input, dict):
                    errors.append("APIFY_ACTOR_INPUT_JSON must be a JSON object.")
            except json.JSONDecodeError:
                errors.append("APIFY_ACTOR_INPUT_JSON must contain valid JSON.")
        else:
            actor_input = DEFAULT_ACTOR_INPUT.copy()

        def number(name, default, cast):
            try:
                return cast(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                errors.append(f"{name} must be a valid number.")
                return default

        return cls(
            prospect_store,
            token=os.getenv("APIFY_API_TOKEN", "").strip(),
            actor_id=os.getenv("APIFY_ACTOR_ID", DEFAULT_ACTOR_ID).strip(),
            actor_input=actor_input,
            max_items=number("APIFY_MAX_ITEMS", 25, int),
            max_charge_usd=number("APIFY_MAX_CHARGE_USD", 5.0, float),
            interval_seconds=number("APIFY_INTERVAL_SECONDS", 86400, int),
            run_timeout_seconds=number("APIFY_RUN_TIMEOUT_SECONDS", 1800, int),
            poll_interval_seconds=number("APIFY_POLL_INTERVAL_SECONDS", 10, int),
            auto_run=os.getenv("APIFY_AUTO_RUN", "").strip().lower()
            in {"1", "true", "yes", "on"},
            config_error=" ".join(errors),
            post=post,
            get=get,
        )

    @property
    def configuration_error(self):
        errors = [self.config_error] if self.config_error else []
        if not self.token:
            errors.append("APIFY_API_TOKEN is not configured.")
        if not self.actor_id:
            errors.append("APIFY_ACTOR_ID is not configured.")
        if not callable(self._post) or not callable(self._get):
            errors.append("Apify HTTP clients are unavailable.")
        if self.actor_input is None:
            errors.append("APIFY_ACTOR_INPUT_JSON is not configured.")
        elif not isinstance(self.actor_input, dict):
            errors.append("APIFY_ACTOR_INPUT_JSON must be a JSON object.")
        if not isinstance(self.max_items, int) or not 1 <= self.max_items <= MAX_PROSPECT_ITEMS:
            errors.append(f"APIFY_MAX_ITEMS must be between 1 and {MAX_PROSPECT_ITEMS}.")
        if (
            not isinstance(self.max_charge_usd, (int, float))
            or not math.isfinite(self.max_charge_usd)
            or self.max_charge_usd <= 0
        ):
            errors.append("APIFY_MAX_CHARGE_USD must be greater than zero.")
        if not isinstance(self.interval_seconds, int) or self.interval_seconds < 300:
            errors.append("APIFY_INTERVAL_SECONDS must be at least 300.")
        if not isinstance(self.run_timeout_seconds, int) or self.run_timeout_seconds < 30:
            errors.append("APIFY_RUN_TIMEOUT_SECONDS must be at least 30.")
        if not isinstance(self.poll_interval_seconds, int) or self.poll_interval_seconds < 1:
            errors.append("APIFY_POLL_INTERVAL_SECONDS must be at least 1.")
        return " ".join(errors)

    def status(self):
        with self._lock:
            result = dict(self._status)
            active = self._active
        result.update(
            {
                "configured": not self.configuration_error,
                "configuration_error": self.configuration_error,
                "auto_run": self.auto_run,
                "interval_seconds": self.interval_seconds,
                "max_items": self.max_items,
                "max_charge_usd": self.max_charge_usd,
                "active": active,
            }
        )
        return result

    def restore_state(self):
        saved = self.prospect_store.load_apify_state()
        if saved is None:
            return
        if saved.get("state") in {"starting", "running", "importing"}:
            saved.update(
                {
                    "state": "interrupted",
                    "message": (
                        "The service restarted during an Apify run. Check Apify before "
                        "unlocking another run."
                    ),
                    "finished_at": self._now(),
                }
            )
        with self._lock:
            self._status.update(saved)
            self._active = False
            self._persist_locked()

    def clear_block(self):
        with self._lock:
            if self._status["state"] not in {"submission_unknown", "interrupted"}:
                return False
            self._status.update(
                {
                    "state": "idle",
                    "message": "Owner confirmed the previous run was checked.",
                    "run_id": "",
                    "started_at": "",
                    "finished_at": self._now(),
                    "results": None,
                }
            )
            self._persist_locked()
            return True

    async def start(self):
        if self.configuration_error:
            raise ValueError(self.configuration_error)
        with self._lock:
            if self._active:
                return False
            if self._status["state"] in {"submission_unknown", "interrupted"}:
                return False
            self._active = True
            self._status.update(
                {
                    "state": "starting",
                    "message": "Starting the configured Apify Actor.",
                    "run_id": "",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "finished_at": "",
                    "results": None,
                }
            )
            self._persist_locked()
        self._task = asyncio.create_task(self._run_task())
        return True

    async def run_schedule(self):
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.start()
            except ValueError as error:
                with self._lock:
                    self._status.update(
                        {"state": "failed", "message": str(error), "finished_at": self._now()}
                    )

    async def _run_task(self):
        try:
            await asyncio.to_thread(self._run_actor)
        except Exception as error:
            with self._lock:
                if isinstance(error, ApifyRunFailed) or (
                    isinstance(error, ApifyHTTPError) and error.status_code < 500
                ):
                    state = "failed"
                elif self._status["state"] == "starting":
                    state = "submission_unknown"
                elif self._status["state"] == "running":
                    state = "interrupted"
                else:
                    state = "failed"
                self._status.update(
                    {
                        "state": state,
                        "message": f"Apify intake failed: {error}"[:1000],
                        "finished_at": self._now(),
                    }
                )
                self._persist_locked()
        finally:
            with self._lock:
                self._active = False
                self._persist_locked()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _update_status(self, **values):
        with self._lock:
            self._status.update(values)
            self._persist_locked()

    def _persist_locked(self):
        self.prospect_store.save_apify_state(self._status)

    def _request_json(self, response, operation):
        if not 200 <= response.status_code < 300:
            raise ApifyHTTPError(operation, response.status_code)
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{operation} returned invalid JSON.") from error

    def _run_actor(self):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        actor_path = quote(self.actor_id, safe="~")
        run_url = f"{APIFY_API_BASE}/acts/{actor_path}/runs"
        self._update_status(state="starting", message="Submitting the Actor run.")
        response = self._post(
            run_url,
            json=self.actor_input,
            params={
                "maxTotalChargeUsd": self.max_charge_usd,
                "maxItems": self.max_items,
                "timeout": self.run_timeout_seconds,
            },
            headers=headers,
            timeout=30,
        )
        payload = self._request_json(response, "Apify Actor start")
        run_data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(run_data, dict) or not run_data.get("id"):
            raise RuntimeError("Apify Actor start response did not include a run ID.")
        run_id = str(run_data["id"])
        self._update_status(
            state="running",
            run_id=run_id,
            message="Apify Actor is running.",
        )

        deadline = self._monotonic() + self.run_timeout_seconds
        run_url = f"{APIFY_API_BASE}/actor-runs/{quote(run_id, safe='')}"
        while True:
            if self._monotonic() >= deadline:
                raise RuntimeError("Apify Actor did not finish before the configured timeout.")
            self._sleep(self.poll_interval_seconds)
            status_response = self._get(
                run_url, headers=headers, timeout=30
            )
            status_payload = self._request_json(status_response, "Apify Actor status")
            status_data = status_payload.get("data") if isinstance(status_payload, dict) else None
            if not isinstance(status_data, dict):
                raise RuntimeError("Apify Actor status response did not include run data.")
            actor_status = str(status_data.get("status") or "").upper()
            if actor_status in {"FAILED", "ABORTED", "TIMED-OUT"}:
                raise ApifyRunFailed(f"Apify Actor finished with status {actor_status}.")
            if actor_status == "SUCCEEDED":
                dataset_id = status_data.get("defaultDatasetId")
                if not dataset_id:
                    raise RuntimeError("Successful Apify run did not include a dataset ID.")
                break
            if actor_status not in {"READY", "RUNNING", "WAITING", "TIMING-OUT"}:
                raise RuntimeError(f"Apify Actor returned unexpected status {actor_status or 'unknown'}.")

        self._update_status(state="importing", message="Importing Actor results for review.")
        dataset_url = f"{APIFY_API_BASE}/datasets/{quote(str(dataset_id), safe='')}/items"
        dataset_response = self._get(
            dataset_url,
            params={"format": "json", "clean": "true", "limit": self.max_items},
            headers=headers,
            timeout=60,
        )
        items = self._request_json(dataset_response, "Apify dataset fetch")
        if not isinstance(items, list):
            raise RuntimeError("Apify dataset response must be a JSON list.")
        results = self.prospect_store.import_apify(
            {"items": items},
            max_items=self.max_items,
        )
        self._update_status(
            state="succeeded",
            message="Apify results were imported into the prospect review queue.",
            results=results,
            finished_at=self._now(),
        )
