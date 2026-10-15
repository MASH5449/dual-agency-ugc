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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS outreach_drafts (
                    prospect_id INTEGER PRIMARY KEY REFERENCES prospects(id) ON DELETE CASCADE,
                    recipient_email TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN (
                            'draft', 'sending', 'enrolled', 'submission_unknown', 'failed'
                        )),
                    provider_lead_id TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outreach_suppressions (
                    email TEXT PRIMARY KEY COLLATE NOCASE,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS apify_intake_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    status_json TEXT NOT NULL
                );
                """
            )

    def load_apify_state(self):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status_json FROM apify_intake_state WHERE singleton = 1"
            ).fetchone()
        return json.loads(row["status_json"]) if row else None

    def save_apify_state(self, status):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO apify_intake_state (singleton, status_json)
                VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET status_json = excluded.status_json
                """,
                (json.dumps(status, ensure_ascii=False),),
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

    @staticmethod
    def _primary_email(contacts):
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            method = str(contact.get("method") or contact.get("type") or "").lower()
            target = contact.get("target") or contact.get("email")
            if method != "email" or not isinstance(target, str):
                continue
            candidate = target.strip()
            if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate):
                return candidate.lower()
        return ""

    def list_outreach_prospects(self):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT p.id, p.company_name, p.website_url, p.domain,
                       p.contacts_json, p.products_json, p.status,
                       d.recipient_email, d.subject, d.body, d.status AS outreach_status,
                       d.provider_lead_id, d.error_message, d.updated_at AS outreach_updated_at,
                       EXISTS (
                           SELECT 1 FROM outreach_suppressions s
                           WHERE s.email = COALESCE(d.recipient_email, '')
                       ) AS suppressed
                FROM prospects p
                LEFT JOIN outreach_drafts d ON d.prospect_id = p.id
                WHERE p.status = 'approved'
                ORDER BY p.id
                """
            ).fetchall()
            suppressed_emails = {
                row["email"].lower()
                for row in connection.execute(
                    "SELECT email FROM outreach_suppressions"
                ).fetchall()
            }
        prospects = []
        for row in rows:
            prospect = dict(row)
            contacts = json.loads(prospect.pop("contacts_json"))
            prospect["sampleProducts"] = json.loads(prospect.pop("products_json"))
            prospect["contacts"] = contacts
            prospect["email"] = (
                prospect["recipient_email"]
                if prospect["recipient_email"]
                else self._primary_email(contacts)
            )
            prospect["suppressed"] = bool(prospect["suppressed"]) or (
                prospect["email"].lower() in suppressed_emails
            )
            prospects.append(prospect)
        return prospects

    def get_outreach_prospect(self, prospect_id):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, company_name, website_url, domain,
                       contacts_json, products_json, status
                FROM prospects WHERE id = ?
                """,
                (prospect_id,),
            ).fetchone()
        if not row:
            return None
        prospect = dict(row)
        prospect["contacts"] = json.loads(prospect.pop("contacts_json"))
        prospect["sampleProducts"] = json.loads(prospect.pop("products_json"))
        prospect["email"] = self._primary_email(prospect["contacts"])
        return prospect

    def is_email_suppressed(self, email):
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM outreach_suppressions WHERE email = ?",
                (email.strip(),),
            ).fetchone() is not None

    def suppress_outreach_email(self, prospect_id, reason=""):
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("Suppression reason must be text up to 500 characters.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT p.status, d.recipient_email, p.contacts_json
                FROM prospects p
                LEFT JOIN outreach_drafts d ON d.prospect_id = p.id
                WHERE p.id = ?
                """,
                (prospect_id,),
            ).fetchone()
            if not row:
                return "not_found"
            email = row["recipient_email"] or self._primary_email(
                json.loads(row["contacts_json"])
            )
            if not email:
                return "no_email"
            connection.execute(
                """
                INSERT INTO outreach_suppressions (email, reason, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET reason = excluded.reason
                """,
                (email, reason.strip(), now),
            )
        return "suppressed"

    def create_outreach_draft(self, prospect_id, subject, body):
        subject = self._validated_text(subject, "Subject", 200)
        body = self._validated_text(body, "Email body", 5000)
        prospect = self.get_outreach_prospect(prospect_id)
        if not prospect:
            return "not_found"
        if prospect["status"] != "approved":
            return "not_approved"
        if not prospect["email"]:
            return "no_email"
        if self.is_email_suppressed(prospect["email"]):
            return "suppressed"
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO outreach_drafts (
                        prospect_id, recipient_email, subject, body, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (prospect_id, prospect["email"], subject, body, now, now),
                )
        except sqlite3.IntegrityError:
            return "exists"
        return "draft"

    def get_outreach_draft(self, prospect_id):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT recipient_email, subject, body, status, provider_lead_id,
                       error_message, updated_at
                FROM outreach_drafts WHERE prospect_id = ?
                """,
                (prospect_id,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _validated_text(value, field, limit):
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text.")
        value = value.strip()
        if not value:
            raise ValueError(f"{field} is required.")
        if len(value) > limit:
            raise ValueError(f"{field} must be {limit} characters or fewer.")
        return value

    def update_outreach_draft(self, prospect_id, data):
        if not isinstance(data, dict) or set(data) != {"subject", "body"}:
            raise ValueError("Provide subject and body only.")
        subject = self._validated_text(data["subject"], "Subject", 200)
        body = self._validated_text(data["body"], "Email body", 5000)
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            prospect = connection.execute(
                "SELECT status FROM prospects WHERE id = ?", (prospect_id,)
            ).fetchone()
            if not prospect:
                return "not_found"
            if prospect["status"] != "approved":
                return "not_approved"
            draft = connection.execute(
                "SELECT recipient_email, status FROM outreach_drafts WHERE prospect_id = ?",
                (prospect_id,),
            ).fetchone()
            if not draft:
                return "no_draft"
            if draft["status"] not in ("draft", "failed"):
                return "not_editable"
            if connection.execute(
                "SELECT 1 FROM outreach_suppressions WHERE email = ?",
                (draft["recipient_email"],),
            ).fetchone():
                return "suppressed"
            connection.execute(
                """
                UPDATE outreach_drafts
                SET subject = ?, body = ?, status = 'draft', provider_lead_id = '',
                    error_message = '', updated_at = ?
                WHERE prospect_id = ?
                """,
                (subject, body, now, prospect_id),
            )
        return "updated"

    def reserve_outreach_send(self, prospect_id):
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT p.status AS prospect_status, p.company_name, p.website_url,
                       d.recipient_email, d.subject, d.body, d.status AS outreach_status
                FROM prospects p
                LEFT JOIN outreach_drafts d ON d.prospect_id = p.id
                WHERE p.id = ?
                """,
                (prospect_id,),
            ).fetchone()
            if not row:
                return "not_found", None
            if row["prospect_status"] != "approved":
                return "not_approved", None
            if not row["recipient_email"]:
                return "no_draft", None
            if connection.execute(
                "SELECT 1 FROM outreach_suppressions WHERE email = ?",
                (row["recipient_email"],),
            ).fetchone():
                return "suppressed", None
            if row["outreach_status"] != "draft":
                return "not_draft", None
            cursor = connection.execute(
                """
                UPDATE outreach_drafts
                SET status = 'sending', error_message = '', updated_at = ?
                WHERE prospect_id = ? AND status = 'draft'
                """,
                (now, prospect_id),
            )
            if cursor.rowcount != 1:
                return "not_draft", None
            return "sending", dict(row)

    def finish_outreach_send(self, prospect_id, status, provider_lead_id="", error_message=""):
        if status not in ("enrolled", "submission_unknown", "failed"):
            raise ValueError("Unsupported outreach status.")
        if not isinstance(provider_lead_id, str) or len(provider_lead_id) > 200:
            raise ValueError("Instantly lead ID must be text up to 200 characters.")
        if not isinstance(error_message, str) or len(error_message) > 1000:
            raise ValueError("Error message must be text up to 1,000 characters.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE outreach_drafts
                SET status = ?, provider_lead_id = ?, error_message = ?, updated_at = ?
                WHERE prospect_id = ? AND status = 'sending'
                """,
                (status, provider_lead_id, error_message, now, prospect_id),
            )
            return cursor.rowcount == 1

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
