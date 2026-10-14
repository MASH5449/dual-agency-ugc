import re
import sqlite3
import math
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


CAMPAIGN_STATUSES = ("planning", "testing", "completed")
CREATIVE_STATUSES = ("draft", "approved", "rejected", "produced", "testing", "completed")
ALLOWED_TRANSITIONS = {
    "draft": ("approved", "rejected"),
    "approved": ("produced", "rejected"),
    "rejected": (),
    "produced": ("testing", "rejected"),
    "testing": ("completed",),
    "completed": (),
}


class CreativeStore:
    """SQLite storage for campaign briefs, creative variants, and test results."""

    def __init__(self, db_path):
        self.db_path = db_path

    def _connect(self):
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS creative_campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prospect_id INTEGER REFERENCES prospects(id) ON DELETE SET NULL,
                    brand_name TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    product_url TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    target_audience TEXT NOT NULL,
                    offer TEXT NOT NULL DEFAULT '',
                    hypothesis TEXT NOT NULL,
                    primary_metric TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planning'
                        CHECK (status IN ('planning', 'testing', 'completed')),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creative_variants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES creative_campaigns(id)
                        ON DELETE CASCADE,
                    concept TEXT NOT NULL,
                    hook TEXT NOT NULL,
                    script TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL CHECK (duration_seconds BETWEEN 5 AND 60),
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'approved', 'rejected', 'produced', 'testing', 'completed')),
                    video_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creative_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creative_id INTEGER NOT NULL REFERENCES creative_variants(id)
                        ON DELETE CASCADE,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    spend REAL NOT NULL CHECK (spend >= 0),
                    impressions INTEGER NOT NULL CHECK (impressions >= 0),
                    clicks INTEGER NOT NULL CHECK (clicks >= 0),
                    conversions INTEGER NOT NULL CHECK (conversions >= 0),
                    revenue REAL NOT NULL CHECK (revenue >= 0),
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE (creative_id, start_date, end_date),
                    CHECK (end_date >= start_date)
                );
                """
            )

    @staticmethod
    def _text(value, field, limit, required=True):
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text.")
        value = value.strip()
        if required and not value:
            raise ValueError(f"{field} is required.")
        if len(value) > limit:
            raise ValueError(f"{field} must be {limit} characters or fewer.")
        return value

    @staticmethod
    def _http_url(value, field):
        value = CreativeStore._text(value, field, 2048)
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
        except ValueError:
            raise ValueError(f"{field} must be an http or https URL.")
        if parsed.scheme not in ("http", "https") or not hostname or parsed.username or parsed.password:
            raise ValueError(f"{field} must be an http or https URL.")
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", hostname):
            raise ValueError(f"{field} must contain a valid hostname.")
        return value

    @staticmethod
    def _integer(value, field, minimum=0, maximum=None):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{field} must be an integer of at least {minimum}.")
        if maximum is not None and value > maximum:
            raise ValueError(f"{field} must be no more than {maximum}.")
        return value

    @staticmethod
    def _number(value, field):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a non-negative number.")
        if value < 0 or not math.isfinite(value):
            raise ValueError(f"{field} must be a non-negative number.")
        return float(value)

    @staticmethod
    def _date(value, field):
        if not isinstance(value, str):
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).")

    def create_campaign(self, data):
        if not isinstance(data, dict):
            raise ValueError("Campaign must be a JSON object.")
        prospect_id = data.get("prospect_id")
        if prospect_id is not None:
            prospect_id = self._integer(prospect_id, "Prospect ID", 1)
        brand = self._text(data.get("brand_name"), "Brand name", 160)
        product = self._text(data.get("product_name"), "Product name", 160)
        url = self._http_url(data.get("product_url"), "Product URL")
        goal = self._text(data.get("goal"), "Campaign goal", 500)
        audience = self._text(data.get("target_audience"), "Target audience", 500)
        offer = self._text(data.get("offer", ""), "Offer", 500, required=False)
        hypothesis = self._text(data.get("hypothesis"), "Test hypothesis", 1000)
        metric = self._text(data.get("primary_metric"), "Primary metric", 80)
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            if prospect_id is not None:
                prospect = connection.execute(
                    "SELECT status FROM prospects WHERE id = ?", (prospect_id,)
                ).fetchone()
                if not prospect or prospect["status"] != "approved":
                    raise ValueError("Campaigns can only link to an approved prospect.")
            cursor = connection.execute(
                """
                INSERT INTO creative_campaigns (
                    prospect_id, brand_name, product_name, product_url, goal, target_audience,
                    offer, hypothesis, primary_metric, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prospect_id, brand, product, url, goal, audience, offer,
                    hypothesis, metric, now,
                ),
            )
            return cursor.lastrowid

    def get_campaign_brief(self, campaign_id):
        with self._connection() as connection:
            campaign = connection.execute(
                """
                SELECT brand_name, product_name, product_url, goal, target_audience,
                    offer, hypothesis, primary_metric
                FROM creative_campaigns WHERE id = ?
                """,
                (campaign_id,),
            ).fetchone()
        return dict(campaign) if campaign else None

    def add_creative(self, campaign_id, data):
        creative_ids = self.add_creatives(campaign_id, [data])
        return creative_ids[0] if creative_ids else None

    def add_creatives(self, campaign_id, creatives):
        if not isinstance(creatives, list) or not 1 <= len(creatives) <= 10:
            raise ValueError("Provide between 1 and 10 creative concepts.")
        validated = []
        for data in creatives:
            if not isinstance(data, dict):
                raise ValueError("Creative must be a JSON object.")
            validated.append(
                (
                    self._text(data.get("concept"), "Concept", 200),
                    self._text(data.get("hook"), "Hook", 500),
                    self._text(data.get("script"), "Script", 5000),
                    self._text(data.get("channel"), "Channel", 80),
                    self._integer(data.get("duration_seconds"), "Duration", 5, 60),
                )
            )
        now = datetime.now(timezone.utc).isoformat()
        creative_ids = []
        with self._connection() as connection:
            campaign = connection.execute(
                "SELECT id FROM creative_campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
            if not campaign:
                return []
            connection.execute(
                "UPDATE creative_campaigns SET status = 'planning' "
                "WHERE id = ? AND status = 'completed'",
                (campaign_id,),
            )
            for concept, hook, script, channel, duration in validated:
                cursor = connection.execute(
                    """
                    INSERT INTO creative_variants (
                        campaign_id, concept, hook, script, channel,
                        duration_seconds, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign_id, concept, hook, script, channel,
                        duration, now,
                    ),
                )
                creative_ids.append(cursor.lastrowid)
        return creative_ids

    def update_creative_draft(self, creative_id, data):
        if not isinstance(data, dict) or set(data) != {
            "concept", "hook", "script", "channel", "duration_seconds"
        }:
            raise ValueError(
                "Provide concept, hook, script, channel, and duration_seconds."
            )
        values = (
            self._text(data["concept"], "Concept", 200),
            self._text(data["hook"], "Hook", 500),
            self._text(data["script"], "Script", 5000),
            self._text(data["channel"], "Channel", 80),
            self._integer(data["duration_seconds"], "Duration", 5, 60),
        )
        with self._connection() as connection:
            creative = connection.execute(
                "SELECT status FROM creative_variants WHERE id = ?", (creative_id,)
            ).fetchone()
            if not creative:
                return "not_found"
            if creative["status"] != "draft":
                return "not_draft"
            cursor = connection.execute(
                """
                UPDATE creative_variants
                SET concept = ?, hook = ?, script = ?, channel = ?,
                    duration_seconds = ?
                WHERE id = ? AND status = 'draft'
                """,
                (*values, creative_id),
            )
            return "updated" if cursor.rowcount else "not_draft"

    def update_creative_status(self, creative_id, status, video_url=""):
        if status not in CREATIVE_STATUSES:
            raise ValueError("Unsupported creative status.")
        if not isinstance(video_url, str) or len(video_url) > 2048:
            raise ValueError("Video URL must be text up to 2,048 characters.")
        if video_url:
            video_url = self._http_url(video_url, "Video URL")
        with self._connection() as connection:
            creative = connection.execute(
                "SELECT status FROM creative_variants WHERE id = ?", (creative_id,)
            ).fetchone()
            if not creative:
                return "not_found"
            if status not in ALLOWED_TRANSITIONS[creative["status"]]:
                raise ValueError(
                    f"Cannot move a {creative['status']} creative to {status}."
                )
            if status == "produced" and not video_url:
                raise ValueError("A video URL is required to mark a creative produced.")
            connection.execute(
                "UPDATE creative_variants SET status = ?, video_url = ? WHERE id = ?",
                (status, video_url, creative_id),
            )
            if status == "testing":
                connection.execute(
                    "UPDATE creative_campaigns SET status = 'testing' "
                    "WHERE id = (SELECT campaign_id FROM creative_variants WHERE id = ?)",
                    (creative_id,),
                )
            elif status == "completed":
                campaign_id = connection.execute(
                    "SELECT campaign_id FROM creative_variants WHERE id = ?",
                    (creative_id,),
                ).fetchone()["campaign_id"]
                unfinished = connection.execute(
                    "SELECT COUNT(*) FROM creative_variants "
                    "WHERE campaign_id = ? AND status NOT IN ('completed', 'rejected')",
                    (campaign_id,),
                ).fetchone()[0]
                if unfinished == 0:
                    connection.execute(
                        "UPDATE creative_campaigns SET status = 'completed' WHERE id = ?",
                        (campaign_id,),
                    )
        return status

    def add_result(self, creative_id, data):
        if not isinstance(data, dict):
            raise ValueError("Results must be a JSON object.")
        start = self._date(data.get("start_date"), "Start date")
        end = self._date(data.get("end_date"), "End date")
        if end < start:
            raise ValueError("End date must be on or after start date.")
        spend = self._number(data.get("spend"), "Spend")
        impressions = self._integer(data.get("impressions"), "Impressions")
        clicks = self._integer(data.get("clicks"), "Clicks")
        conversions = self._integer(data.get("conversions"), "Conversions")
        revenue = self._number(data.get("revenue"), "Revenue")
        notes = self._text(data.get("notes", ""), "Notes", 2000, required=False)
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            creative = connection.execute(
                "SELECT status FROM creative_variants WHERE id = ?", (creative_id,)
            ).fetchone()
            if not creative:
                return None
            if creative["status"] not in ("testing", "completed"):
                raise ValueError("Only a creative marked testing can have results recorded.")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO creative_results (
                        creative_id, start_date, end_date, spend, impressions,
                        clicks, conversions, revenue, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        creative_id, start, end, spend, impressions, clicks,
                        conversions, revenue, notes, now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "UNIQUE constraint failed" in str(error):
                    raise ValueError("Results for this creative and date range already exist.")
                raise
            return cursor.lastrowid

    def list_campaigns(self):
        with self._connection() as connection:
            campaigns = connection.execute(
                "SELECT * FROM creative_campaigns ORDER BY id DESC"
            ).fetchall()
            output = []
            for campaign in campaigns:
                campaign_data = dict(campaign)
                creatives = connection.execute(
                    "SELECT * FROM creative_variants WHERE campaign_id = ? ORDER BY id",
                    (campaign["id"],),
                ).fetchall()
                campaign_data["creatives"] = []
                for creative in creatives:
                    creative_data = dict(creative)
                    results = connection.execute(
                        "SELECT * FROM creative_results WHERE creative_id = ? ORDER BY start_date",
                        (creative["id"],),
                    ).fetchall()
                    creative_data["results"] = [dict(row) for row in results]
                    campaign_data["creatives"].append(creative_data)
                output.append(campaign_data)
        return output
