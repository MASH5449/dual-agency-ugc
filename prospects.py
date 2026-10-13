import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class ProspectStore:
    """Persistent store for externally sourced prospects awaiting human review."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self):
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prospects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    website_url TEXT NOT NULL,
                    domain TEXT NOT NULL UNIQUE,
                    contacts_json TEXT NOT NULL,
                    products_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'needs_review'
                        CHECK (status IN ('needs_review', 'approved', 'rejected')),
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS prospects_source_id "
                "ON prospects(source, source_id)"
            )

    @staticmethod
    def _valid_website(value):
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if "://" not in candidate:
            candidate = "https://" + candidate
        try:
            parsed = urlsplit(candidate)
            host = parsed.hostname
        except ValueError:
            return None
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return None
        host = host.lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
            return None
        return candidate, host

    @staticmethod
    def _items(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload["items"]
        raise ValueError("Expected a JSON list of Apify dataset items.")

    def import_apify(self, payload, max_items=200):
        items = self._items(payload)
        if len(items) > max_items:
            raise ValueError(f"At most {max_items} records can be imported at once.")

        inserted = duplicates = invalid = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            for item in items:
                if not isinstance(item, dict):
                    invalid += 1
                    continue
                company = item.get("name")
                website = self._valid_website(item.get("websiteUrl"))
                if not isinstance(company, str) or not company.strip() or not website:
                    invalid += 1
                    continue

                website_url, domain = website
                source_id = str(item.get("id") or domain).strip()
                contacts = item.get("contacts")
                products = item.get("sampleProducts")
                try:
                    connection.execute(
                        """
                        INSERT INTO prospects (
                            source, source_id, company_name, website_url, domain,
                            contacts_json, products_json, raw_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "apify",
                            source_id,
                            company.strip(),
                            website_url,
                            domain,
                            json.dumps(contacts if isinstance(contacts, list) else []),
                            json.dumps(products if isinstance(products, list) else []),
                            json.dumps(item, ensure_ascii=False),
                            now,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    duplicates += 1

        return {"inserted": inserted, "duplicates": duplicates, "invalid": invalid}

    def list_prospects(self, status=None):
        query = """
            SELECT id, source, company_name, website_url, domain,
                   contacts_json, products_json, status, review_note,
                   created_at, reviewed_at
            FROM prospects
        """
        parameters = ()
        if status:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        prospects = []
        for row in rows:
            prospect = dict(row)
            prospect["contacts"] = json.loads(prospect.pop("contacts_json"))
            prospect["sampleProducts"] = json.loads(prospect.pop("products_json"))
            prospects.append(prospect)
        return prospects

    def count(self, status=None):
        query = "SELECT COUNT(*) FROM prospects"
        parameters = ()
        if status:
            query += " WHERE status = ?"
            parameters = (status,)
        with self._connection() as connection:
            return connection.execute(query, parameters).fetchone()[0]

    def review(self, prospect_id, status, note=""):
        if status not in ("approved", "rejected", "needs_review"):
            raise ValueError("Unsupported review status.")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Review notes must be text up to 2,000 characters.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE prospects
                SET status = ?, review_note = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (status, note, now, prospect_id),
            )
            if cursor.rowcount == 0:
                return False
        return True
