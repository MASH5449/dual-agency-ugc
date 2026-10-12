"""Scraper agent — pluggable ingest source for LeadLoop (stdlib only).

Local sources only:

* ``<DATA_DIR>/inbox/*.json`` — a JSON array of lead objects.
* ``<DATA_DIR>/inbox/*.csv``  — a CSV export of the owner's opted-in list
  (header row, case-insensitive column names; see ``CSV_COLUMN_ALIASES``).
* ``<DATA_DIR>/queue.jsonl``  — one JSON object per line.

Consumed inbox files are moved to ``<DATA_DIR>/processed/`` so the same batch
is never re-ingested.

COMPLIANCE (hard rule, do not work around): this agent must never fetch email
addresses off the open web. Inputs are curated, opted-in or otherwise lawfully
obtained business contact lists, and every item declares its ``lawful_basis``
(or falls back to ``DEFAULT_LAWFUL_BASIS``, which names the owner's own
opted-in list). A future live source may fetch *company* domains from permitted
directories — never personal email addresses scraped for bulk cold email.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agents.base import Agent
from db import Database, normalise_email, normalise_website

#: Deliberately conservative address check — mirrors the normalisation rules in
#: db.py. Anything that fails is logged and skipped, never inserted.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

#: Hostname sanity: at least one dot, no spaces, a plausible TLD.
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$")

# ---------------------------------------------------------------------------
# CSV INGEST — header aliases (lowercased header name -> lead field)
# ---------------------------------------------------------------------------
#: Recognised columns. Unknown columns are ignored, so an owner's export can
#: carry extra data without breaking the import. ``domain`` is the common
#: export name for the website column; ``e-mail``/``email address`` are common
#: spreadsheet headers.
CSV_COLUMN_ALIASES = {
    "email": "email",
    "e-mail": "email",
    "email address": "email",
    "website": "website",
    "domain": "website",
    "company": "company",
    "company name": "company",
    "employees": "employees",
    "revenue": "revenue",
    "industry": "industry",
    "source": "source",
    "lawful_basis": "lawful_basis",
    "lawful basis": "lawful_basis",
}

#: Columns that must be numeric when present.
CSV_NUMERIC_FIELDS = ("employees", "revenue")


@dataclass
class RawLead:
    """One validated input record."""

    email: str
    website: str | None
    raw: dict = field(default_factory=dict)


class ScraperAgent(Agent):
    """Reads local ingest files and inserts new leads into SQLite."""

    name = "Scraper"
    tag = "[Agent-Scraper]"

    def __init__(self, config=None, db: Database | None = None) -> None:
        super().__init__(config)
        self.db = db or Database(getattr(config, "db_path", "./leads.db"))

    @property
    def default_lawful_basis(self) -> str:
        """Env default recorded for rows that do not declare their own basis."""
        value = getattr(self.config, "default_lawful_basis", None)
        return str(value) if value else "owned opted-in list"

    # -- source discovery -------------------------------------------------
    def source_files(self) -> list[Path]:
        """Inbox files waiting to be consumed, plus the optional queue file."""
        files: list[Path] = []
        inbox = Path(getattr(self.config, "inbox_dir", "./data/inbox"))
        if inbox.is_dir():
            files.extend(sorted(p for p in inbox.glob("*.json") if p.is_file()))
            files.extend(sorted(p for p in inbox.glob("*.csv") if p.is_file()))
        queue = Path(getattr(self.config, "queue_file", "./data/queue.jsonl"))
        if queue.is_file():
            files.append(queue)
        return files

    # -- parsing ----------------------------------------------------------
    def read_items(self, path: Path) -> list[dict]:
        """Parse one ingest file into a list of raw dicts."""
        if path.suffix.lower() == ".csv":
            return self.read_csv_items(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.log_error(f"cannot read {path.name}: {exc}")
            return []

        items: list[dict] = []
        if path.suffix.lower() == ".jsonl":
            for lineno, line in enumerate(text.splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    self.log_error(f"{path.name}:{lineno} bad JSON: {exc}")
                    continue
                if isinstance(obj, dict):
                    items.append(obj)
                else:
                    self.log_error(f"{path.name}:{lineno} not an object, skipped")
            return items

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self.log_error(f"{path.name} bad JSON: {exc}")
            return []
        if isinstance(payload, dict):
            payload = payload.get("leads", [])
        if not isinstance(payload, list):
            self.log_error(f"{path.name} is not a JSON array, skipped")
            return []
        for entry in payload:
            if isinstance(entry, dict):
                items.append(entry)
            else:
                self.log_error(f"{path.name} entry is not an object, skipped")
        return items

    def read_csv_items(self, path: Path) -> list[dict]:
        """Parse a CSV export of the owner's list into lead dicts.

        Header row required; header names are matched case-insensitively and
        trimmed. ``website``/``domain`` are treated as the same column.
        Unrecognised columns are ignored. A row with no email is skipped with a
        log line, and the surrounding rows still import.
        """
        try:
            handle = path.open("r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            self.log_error(f"cannot read {path.name}: {exc}")
            return []

        items: list[dict] = []
        with handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                self.log_error(f"{path.name} has no header row, skipped")
                return []
            mapping = {
                field: CSV_COLUMN_ALIASES[field.strip().lower()]
                for field in reader.fieldnames
                if field and field.strip().lower() in CSV_COLUMN_ALIASES
            }
            if "email" not in mapping.values():
                self.log_error(f"{path.name} has no email column, skipped")
                return []
            for lineno, row in enumerate(reader, start=2):
                item: dict = {}
                for field, target in mapping.items():
                    value = row.get(field)
                    if value is None:
                        continue
                    value = str(value).strip()
                    if not value:
                        continue
                    if target in CSV_NUMERIC_FIELDS:
                        number = _to_number(value)
                        if number is None:
                            self.log_error(
                                f"{path.name}:{lineno} {target}={value!r} not "
                                f"numeric, ignored"
                            )
                            continue
                        item[target] = number
                    else:
                        item[target] = value
                if not item.get("email"):
                    self.log_error(f"{path.name}:{lineno} no email, skipped")
                    continue
                items.append(item)
        return items

    # -- validation -------------------------------------------------------
    def validate(self, item: dict, origin: str) -> RawLead | None:
        """Validate one record. Returns None (and logs) when unusable."""
        email = normalise_email(item.get("email"))
        if not email or not EMAIL_RE.match(email):
            self.log_error(f"{origin}: invalid email, skipped")
            return None

        website = normalise_website(item.get("website"))
        if website and not HOST_RE.match(website):
            self.log_error(f"{origin}: invalid website, skipped")
            return None

        return RawLead(email=email, website=website, raw=item)

    # -- ingest -----------------------------------------------------------
    def handle(self, item: dict) -> RawLead | None:
        """Validate a single record (used by the base run() loop)."""
        return self.validate(item, "item")

    def ingest(self) -> dict[str, int]:
        """Full ingest pass over every available source file."""
        files = self.source_files()
        if not files:
            self.log("no inbox files found; nothing to ingest")
            return {"files": 0, "inserted": 0, "skipped": 0, "suppressed": 0}

        totals = {"files": 0, "inserted": 0, "skipped": 0, "suppressed": 0}
        for path in files:
            raw_items = self.read_items(path)
            valid: list[dict] = []
            for index, item in enumerate(raw_items):
                lead = self.validate(item, f"{path.name}#{index}")
                if lead is None:
                    continue
                record = dict(lead.raw)
                record["email"] = lead.email
                record["website"] = lead.website
                # Provenance: an input that does not declare its lawful basis
                # gets the configured default (the owner's own opted-in list).
                if not (record.get("lawful_basis") or "").strip():
                    record["lawful_basis"] = self.default_lawful_basis
                valid.append(record)

            result = self.db.insert_leads(valid)
            totals["files"] += 1
            totals["inserted"] += result.inserted
            totals["skipped"] += result.skipped
            totals["suppressed"] += result.suppressed

            self.log(
                f"{path.name}: {len(raw_items)} read, {len(valid)} valid, "
                f"{result.inserted} inserted, {result.skipped} skipped "
                f"({result.suppressed} suppressed)"
            )
            self.archive(path)

        self.log(
            f"ingest complete: {totals['files']} file(s), "
            f"{totals['inserted']} inserted, {totals['skipped']} skipped"
        )
        return totals

    def archive(self, path: Path) -> None:
        """Move a consumed source file into data/processed/."""
        processed = Path(getattr(self.config, "processed_dir", "./data/processed"))
        try:
            processed.mkdir(parents=True, exist_ok=True)
            target = processed / path.name
            if target.exists():
                stem, suffix = path.stem, path.suffix
                counter = 1
                while target.exists():
                    target = processed / f"{stem}.{counter}{suffix}"
                    counter += 1
            shutil.move(str(path), str(target))
            self.log(f"archived {path.name} -> {target}")
        except OSError as exc:
            self.log_error(f"could not archive {path.name}: {exc}")


def _to_number(value: str) -> float | None:
    """Parse a spreadsheet-style number (``1,200``, ``$1.2M``, ``45``).

    Returns None when it cannot be read, so the caller logs and ignores the
    cell instead of inventing a firmographic value.
    """
    text = value.strip().replace(",", "").replace("_", "")
    text = text.replace("$", "").replace("\u20ac", "").replace("\u00a3", "").strip()
    multiplier = 1.0
    lowered = text.lower()
    for suffix, factor in (("k", 1_000.0), ("m", 1_000_000.0), ("b", 1_000_000_000.0)):
        if lowered.endswith(suffix):
            multiplier = factor
            text = text[: -len(suffix)].strip()
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None
