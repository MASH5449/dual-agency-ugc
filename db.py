"""SQLite persistence layer for LeadLoop (stdlib only).

Core requirement: **hard dedupe on email and website.** Normalisation happens
before every insert, and inserts use ``ON CONFLICT DO NOTHING`` so a repeat of
the same input never creates a second row.

Status lifecycle in step 1: ``new`` -> ``classified``, plus ``failed``.
Step 2 will extend this with video/dispatch states.
"""

from __future__ import annotations

import os
import sqlite3
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_LEADS = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    website TEXT UNIQUE,
    tier TEXT,
    video_id TEXT UNIQUE,
    video_url TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL
);
"""

# Additive tables. These must never alter `leads`.
SCHEMA_LEAD_SOURCES = """
CREATE TABLE IF NOT EXISTS lead_sources (
    lead_id INTEGER NOT NULL,
    source TEXT,
    lawful_basis TEXT,
    added_at TEXT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES leads (id)
);
"""

SCHEMA_SUPPRESSIONS = """
CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY,
    reason TEXT,
    added_at TEXT NOT NULL
);
"""

# Additional ADDITIVE helper table. Deliberately separate from `leads` so the
# frozen leads schema above is never altered: the classifier reads the
# firmographic inputs it needs from here, keyed by lead id.
SCHEMA_FIRMOGRAPHICS = """
CREATE TABLE IF NOT EXISTS lead_firmographics (
    lead_id INTEGER PRIMARY KEY,
    company TEXT,
    employees REAL,
    revenue REAL,
    industry TEXT,
    added_at TEXT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES leads (id)
);
"""

SCHEMA_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);",
    "CREATE INDEX IF NOT EXISTS idx_leads_video_id ON leads (video_id);",
    "CREATE INDEX IF NOT EXISTS idx_leads_tier ON leads (tier);",
    "CREATE INDEX IF NOT EXISTS idx_lead_sources_lead_id ON lead_sources (lead_id);",
)

ALL_SCHEMA = (
    SCHEMA_LEADS,
    SCHEMA_LEAD_SOURCES,
    SCHEMA_SUPPRESSIONS,
    SCHEMA_FIRMOGRAPHICS,
) + SCHEMA_INDEXES

#: Valid tier values, in escalation order.
TIERS = ("quick", "scale", "whitelabel")

STATUS_NEW = "new"
STATUS_CLASSIFIED = "classified"
STATUS_FAILED = "failed"


def utcnow() -> str:
    """ISO-8601 UTC timestamp (second resolution)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0
    ).isoformat()


# ---------------------------------------------------------------------------
# Normalisation — the heart of dedupe
# ---------------------------------------------------------------------------

def normalise_email(email: Any) -> str | None:
    """Lowercase + trim an email address. Returns None if not a usable string."""
    if email is None:
        return None
    text = str(email).strip().lower()
    return text or None


def normalise_website(website: Any) -> str | None:
    """Canonicalise a website so cosmetic variants dedupe to one key.

    Lowercases, strips the scheme, strips a leading ``www.``, strips trailing
    slashes and any query/fragment.
    """
    if website is None:
        return None
    text = str(website).strip().lower()
    if not text:
        return None
    if "//" in text:
        text = urllib.parse.urlsplit(text).netloc or text.split("//", 1)[1]
    # Drop path/query/fragment noise after the host.
    for sep in ("/", "?", "#"):
        text = text.split(sep, 1)[0]
    if text.startswith("www."):
        text = text[4:]
    text = text.strip().rstrip(".")
    return text or None


@dataclass
class InsertResult:
    """Outcome of an insert batch."""

    inserted: int = 0
    skipped: int = 0
    suppressed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "skipped": self.skipped,
            "suppressed": self.suppressed,
        }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    """Thin wrapper around a SQLite connection."""

    def __init__(self, path: str = "./leads.db") -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    # -- connection -------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            for statement in ALL_SCHEMA:
                conn.execute(statement)
            conn.commit()

    # -- suppression ------------------------------------------------------
    def is_suppressed(self, conn: sqlite3.Connection, email: Any) -> bool:
        key = normalise_email(email)
        if not key:
            return False
        row = conn.execute(
            "SELECT 1 FROM suppressions WHERE email = ? LIMIT 1;", (key,)
        ).fetchone()
        return row is not None

    def add_suppression(
        self, email: str, reason: str = "opt-out", conn: sqlite3.Connection | None = None
    ) -> bool:
        key = normalise_email(email)
        if not key:
            return False
        owns = conn is None
        conn = conn or self.connect()
        try:
            cur = conn.execute(
                "INSERT INTO suppressions (email, reason, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO NOTHING;",
                (key, reason, utcnow()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            if owns:
                conn.close()

    # -- inserts ----------------------------------------------------------
    def insert_leads(
        self, items: Iterable[dict], conn: sqlite3.Connection | None = None
    ) -> InsertResult:
        """Insert normalised leads, skipping duplicates and suppressed emails.

        Each item must already have ``email``. Returns inserted/skipped counts;
        ``skipped`` includes both duplicate rows and suppressed addresses.
        """
        owns = conn is None
        conn = conn or self.connect()
        result = InsertResult()
        try:
            for item in items:
                email = normalise_email(item.get("email"))
                website = normalise_website(item.get("website"))
                if not email:
                    result.skipped += 1
                    continue
                if self.is_suppressed(conn, email):
                    result.suppressed += 1
                    result.skipped += 1
                    continue

                cur = conn.execute(
                    "INSERT INTO leads (email, website, tier, video_id, video_url, "
                    "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING;",
                    (
                        email,
                        website,
                        item.get("tier"),
                        item.get("video_id"),
                        item.get("video_url"),
                        item.get("status") or STATUS_NEW,
                        item.get("created_at") or utcnow(),
                    ),
                )
                if cur.rowcount > 0:
                    result.inserted += 1
                    lead_id = cur.lastrowid
                    self.record_source(
                        conn,
                        lead_id,
                        item.get("source"),
                        item.get("lawful_basis"),
                    )
                    self.record_firmographics(conn, lead_id, item)
                else:
                    result.skipped += 1
            conn.commit()
            return result
        finally:
            if owns:
                conn.close()

    def record_source(
        self,
        conn: sqlite3.Connection,
        lead_id: int,
        source: str | None,
        lawful_basis: str | None,
    ) -> None:
        """Append one provenance row per inserted lead."""
        conn.execute(
            "INSERT INTO lead_sources (lead_id, source, lawful_basis, added_at) "
            "VALUES (?, ?, ?, ?);",
            (lead_id, source or "unknown", lawful_basis or "unspecified", utcnow()),
        )

    def record_firmographics(
        self, conn: sqlite3.Connection, lead_id: int, item: dict
    ) -> None:
        """Store the firmographic inputs the classifier needs (additive table)."""
        conn.execute(
            "INSERT INTO lead_firmographics "
            "(lead_id, company, employees, revenue, industry, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(lead_id) DO NOTHING;",
            (
                lead_id,
                item.get("company"),
                item.get("employees"),
                item.get("revenue"),
                item.get("industry"),
                utcnow(),
            ),
        )

    def firmographics(self, conn: sqlite3.Connection, lead_id: int) -> dict:
        """Return firmographic fields for a lead, or an empty dict."""
        row = conn.execute(
            "SELECT company, employees, revenue, industry FROM lead_firmographics "
            "WHERE lead_id = ?;",
            (lead_id,),
        ).fetchone()
        return dict(row) if row is not None else {}

    # -- reads ------------------------------------------------------------
    def count(self, conn: sqlite3.Connection | None = None) -> int:
        owns = conn is None
        conn = conn or self.connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM leads;").fetchone()[0])
        finally:
            if owns:
                conn.close()

    def leads_by_status(self, status: str, conn: sqlite3.Connection | None = None):
        owns = conn is None
        conn = conn or self.connect()
        try:
            return conn.execute(
                "SELECT id, email, website, tier, video_id, video_url, status, "
                "created_at FROM leads WHERE status = ? ORDER BY id;",
                (status,),
            ).fetchall()
        finally:
            if owns:
                conn.close()

    def stats(self, conn: sqlite3.Connection | None = None) -> dict[str, dict[str, int]]:
        """Row counts grouped by status and by tier."""
        owns = conn is None
        conn = conn or self.connect()
        try:
            by_status = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY status;"
                )
            }
            by_tier = {
                str(row[0] if row[0] is not None else "unclassified"): int(row[1])
                for row in conn.execute(
                    "SELECT tier, COUNT(*) FROM leads GROUP BY tier ORDER BY tier;"
                )
            }
            return {
                "total": {"total": self.count(conn)},
                "by_status": by_status,
                "by_tier": by_tier,
            }
        finally:
            if owns:
                conn.close()
